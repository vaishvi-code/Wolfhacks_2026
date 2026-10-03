"""Wayfinder: dependency-free, local geospatial routing prototype."""
import heapq
import json
import math
import os
import sqlite3
import threading
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'data' / 'wayfinder.sqlite'
LOCK = threading.Lock()


def now():
    return datetime.now(timezone.utc).isoformat()


def connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    DB.parent.mkdir(exist_ok=True)
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY, lat REAL, lon REAL, kind TEXT, severity TEXT, note TEXT, created TEXT, expires TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS observations (site TEXT, observed TEXT, value REAL, unit TEXT, name TEXT, PRIMARY KEY (site, observed))')


def normalize_sensors(payload):
    readings = []
    for series in payload['value']['timeSeries']:
        site = series['sourceInfo']['siteCode'][0]['value']
        unit = series['variable']['unit']['unitCode']
        missing = float(series['variable'].get('noDataValue', -999999))
        for group in series['values']:
            for reading in group['value']:
                value = float(reading['value'])
                if value != missing and math.isfinite(value):
                    readings.append({'site': site, 'observed': reading['dateTime'], 'value': value, 'unit': unit, 'name': series['sourceInfo']['siteName']})
    return readings


def sensor_loop():
    while True:
        try:
            url = 'https://waterservices.usgs.gov/nwis/iv/?format=json&sites=02087500,02087183,02087359&parameterCd=00065&period=PT2H&siteStatus=all'
            with urllib.request.urlopen(url, timeout=15) as response:
                readings = normalize_sensors(json.load(response))
            with connect() as db:
                db.executemany('INSERT OR REPLACE INTO observations VALUES (:site, :observed, :value, :unit, :name)', readings)
                db.execute('INSERT OR REPLACE INTO cache VALUES (?, ?)', ('sensor_status', json.dumps({'fetched': now(), 'status': 'Connected to USGS' if readings else 'No recent observations available'})))
                cutoff = (datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
                db.execute('DELETE FROM observations WHERE observed < ?', (cutoff,))
        except Exception:
            with connect() as db:
                old = db.execute("SELECT value FROM cache WHERE key = 'sensor_status'").fetchone()
                status = json.loads(old[0]) if old else {'fetched': None}
                status['status'] = 'USGS unavailable · cached observations'
                db.execute('INSERT OR REPLACE INTO cache VALUES (?, ?)', ('sensor_status', json.dumps(status)))
        time.sleep(60)


def sensors():
    with connect() as db:
        row = db.execute("SELECT value FROM cache WHERE key = 'sensor_status'").fetchone()
        readings = [dict(r) for r in db.execute('SELECT * FROM observations ORDER BY observed')]
    groups = {}
    for reading in readings:
        groups.setdefault(reading['site'], []).append(reading)
    return {'source': 'USGS instantaneous values', 'poll_seconds': 60, 'stations': list(groups.values()), **(json.loads(row[0]) if row else {'fetched': None, 'status': 'Connecting to USGS…'})}


def graph():
    # Illustrative graph at real geographic coordinates, NOT an OSM road network.
    nodes = [{'id': f'{r}-{c}', 'lat': 35.756 + r * .008, 'lon': -78.674 + c * .009} for r in range(6) for c in range(6)]
    by_id = {n['id']: n for n in nodes}
    edges = []
    for r in range(6):
        for c in range(6):
            for rr, cc in [(r + 1, c), (r, c + 1)]:
                if rr < 6 and cc < 6:
                    a, b = f'{r}-{c}', f'{rr}-{cc}'
                    edges.append({'id': f'{a}:{b}', 'a': a, 'b': b, 'distance': round(distance(by_id[a], by_id[b]))})
    return nodes, edges


def distance(a, b):
    lat1, lat2 = math.radians(a['lat']), math.radians(b['lat'])
    dlat, dlon = lat2 - lat1, math.radians(b['lon'] - a['lon'])
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(h)))


def segment_distance(p, a, b):
    # Local equirectangular projection, suitable for this small Raleigh extent.
    scale = math.cos(math.radians(p['lat']))
    ax, ay = (a['lon']-p['lon'])*scale*111320, (a['lat']-p['lat'])*111320
    bx, by = (b['lon']-p['lon'])*scale*111320, (b['lat']-p['lat'])*111320
    dx, dy = bx-ax, by-ay
    t = max(0, min(1, -(ax*dx+ay*dy)/(dx*dx+dy*dy))) if dx or dy else 0
    return math.hypot(ax+t*dx, ay+t*dy)


def hazards():
    demo = [
        {'id': 'demo-1', 'lat': 35.780, 'lon': -78.647, 'kind': 'Flooding', 'severity': 'blocked', 'radius': 480, 'note': 'Simulated flooded crossing', 'source': 'Sample scenario', 'confidence': 'Illustrative'},
        {'id': 'demo-2', 'lat': 35.764, 'lon': -78.656, 'kind': 'Flooding', 'severity': 'high', 'radius': 500, 'note': 'Simulated standing water', 'source': 'Sample scenario', 'confidence': 'Illustrative'},
        {'id': 'demo-3', 'lat': 35.788, 'lon': -78.629, 'kind': 'Debris', 'severity': 'caution', 'radius': 350, 'note': 'Simulated debris near road', 'source': 'Sample scenario', 'confidence': 'Illustrative'},
    ]
    with connect() as db:
        reports = [dict(row) for row in db.execute('SELECT * FROM reports WHERE expires > ? ORDER BY id DESC', (now(),))]
    for report in reports:
        report.update(radius=350, source='Community report', confidence='Unverified')
    return demo + reports


LEVELS = {'clear': 0, 'caution': 1, 'high': 2, 'blocked': 3}
MULTIPLIERS = {'clear': 1, 'caution': 3, 'high': 12}


def risk_edges(nodes, edges, items):
    by_id = {n['id']: n for n in nodes}
    result = []
    for edge in edges:
        matches = [h for h in items if segment_distance(h, by_id[edge['a']], by_id[edge['b']]) <= h['radius']]
        risk = max((h['severity'] for h in matches), key=lambda s: LEVELS[s], default='clear')
        result.append(dict(edge, risk=risk, hazards=[h['id'] for h in matches]))
    return result


DESTINATIONS = [
    {'id': 'north', 'node': '5-2', 'name': 'North community hub', 'type': 'Sample shelter', 'capacity': 'Demo destination'},
    {'id': 'west', 'node': '3-0', 'name': 'West relief center', 'type': 'Sample shelter', 'capacity': 'Demo destination'},
    {'id': 'east', 'node': '4-5', 'name': 'East community hub', 'type': 'Sample shelter', 'capacity': 'Demo destination'},
]


def shortest_path(nodes, edges, start, end, weighted=True):
    adjacent = {n['id']: [] for n in nodes}
    for e in edges:
        if e['risk'] == 'blocked':
            continue
        cost = e['distance'] * (MULTIPLIERS[e['risk']] if weighted else 1)
        adjacent[e['a']].append((e['b'], cost, e))
        adjacent[e['b']].append((e['a'], cost, e))
    queue, costs, previous = [(0, start)], {start: 0}, {}
    while queue:
        cost, current = heapq.heappop(queue)
        if cost != costs[current]:
            continue
        if current == end:
            path, used = [end], []
            while path[-1] != start:
                parent, edge = previous[path[-1]]
                path.append(parent)
                used.append(edge)
            return {'nodes': list(reversed(path)), 'edges': list(reversed(used)), 'cost': cost, 'distance': sum(e['distance'] for e in used)}
        for neighbor, weight, edge in adjacent[current]:
            new_cost = cost + weight
            if new_cost < costs.get(neighbor, float('inf')):
                costs[neighbor] = new_cost
                previous[neighbor] = (current, edge)
                heapq.heappush(queue, (new_cost, neighbor))
    return None


def weather_cache():
    with connect() as db:
        row = db.execute("SELECT value FROM cache WHERE key = 'weather'").fetchone()
    return json.loads(row[0]) if row else {'updated': None, 'alerts': [], 'status': 'Not refreshed'}


def snapshot():
    nodes, edges = graph()
    items = hazards()
    return {'nodes': nodes, 'edges': risk_edges(nodes, edges, items), 'hazards': items, 'destinations': DESTINATIONS, 'weather': weather_cache(), 'generated': now(), 'mode': 'sample'}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'static'), **kwargs)

    def send_json(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path == '/api/state':
            self.send_json(snapshot())
        elif urlparse(self.path).path == '/api/sensors':
            self.send_json(sensors())
        else:
            super().do_GET()

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length > 8192:
                raise ValueError('Request too large')
            data = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(data, dict):
                raise ValueError('Expected a JSON object')
            path = urlparse(self.path).path
            if path == '/api/route':
                state = snapshot()
                start = data.get('start', '1-2')
                if start not in {n['id'] for n in state['nodes']}:
                    raise ValueError('Choose a valid starting point')
                target = data.get('destination', 'auto')
                candidates = [d for d in DESTINATIONS if target == 'auto' or d['id'] == target]
                if not candidates:
                    raise ValueError('Choose a valid destination')
                routes = [(shortest_path(state['nodes'], state['edges'], start, d['node']), d) for d in candidates]
                available = [(r, d) for r, d in routes if r is not None]
                if not available:
                    self.send_json({'error': 'No viable route in the sample network. Try another start or destination.'}, 422)
                    return
                route, destination = min(available, key=lambda pair: pair[0]['cost'])
                route.update(destination=destination, high_risk=sum(e['risk'] == 'high' for e in route['edges']), caution=sum(e['risk'] == 'caution' for e in route['edges']), mode='sample')
                self.send_json(route)
            elif path == '/api/reports':
                lat, lon = float(data['lat']), float(data['lon'])
                if not (35.752 <= lat <= 35.800 and -78.680 <= lon <= -78.623):
                    raise ValueError('Report location must be inside the sample map')
                kind, severity = data.get('kind'), data.get('severity')
                if kind not in ['Flooding', 'Debris', 'Blocked road'] or severity not in ['caution', 'high', 'blocked']:
                    raise ValueError('Invalid hazard type or severity')
                note = data.get('note', '').strip()
                if len(note) > 280:
                    raise ValueError('Notes must be 280 characters or fewer')
                with LOCK, connect() as db:
                    db.execute('INSERT INTO reports (lat, lon, kind, severity, note, created, expires) VALUES (?, ?, ?, ?, ?, ?, ?)', (lat, lon, kind, severity, note, now(), (datetime.now(timezone.utc)+timedelta(hours=6)).isoformat()))
                self.send_json(snapshot(), 201)
            elif path == '/api/refresh':
                try:
                    request = urllib.request.Request('https://api.weather.gov/alerts/active?point=35.7796,-78.6382', headers={'User-Agent': os.environ.get('NWS_USER_AGENT', 'WayfinderHackathonPrototype/1.0'), 'Accept': 'application/geo+json'})
                    with urllib.request.urlopen(request, timeout=12) as response:
                        weather = json.load(response)
                    alerts = [{'event': f['properties'].get('event'), 'headline': f['properties'].get('headline'), 'severity': f['properties'].get('severity'), 'expires': f['properties'].get('expires'), 'instruction': f['properties'].get('instruction')} for f in weather['features']]
                    value = {'updated': now(), 'alerts': alerts, 'status': 'Fetched from NWS'}
                    with connect() as db:
                        db.execute('INSERT OR REPLACE INTO cache VALUES (?, ?)', ('weather', json.dumps(value)))
                    self.send_json(value)
                except Exception:
                    self.send_json(dict(weather_cache(), status='Refresh failed · cached information only'), 503)
            else:
                self.send_json({'error': 'Unknown endpoint'}, 404)
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            self.send_json({'error': str(error)}, 400)


if __name__ == '__main__':
    init_db()
    threading.Thread(target=sensor_loop, daemon=True).start()
    port = int(os.environ.get('PORT', 8000))
    print(f'Wayfinder is running at http://localhost:{port}', flush=True)
    ThreadingHTTPServer((os.environ.get('HOST', '127.0.0.1'), port), Handler).serve_forever()
