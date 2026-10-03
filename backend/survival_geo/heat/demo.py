"""Synthetic heat + flood relief comparison and disk-only replay."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import osmnx as ox
from shapely.geometry import box, mapping

from ..demo import demo_scenario
from ..flood import parse_nws_alerts, flood_hazard_batch
from ..offline import Coverage, RefreshService, SnapshotStore
from .adapter import heat_hazard_batch
from .nws import parse_heat_alerts
from .relief import evaluate_relief_candidates, ingest_official_centers, relief_offline

DEMO_TIME = datetime(2026, 10, 3, 14, 10, tzinfo=timezone.utc)
TRUSTED_SOURCES = {'synthetic-government': 'https://example.invalid/synthetic-centers'}
COVERAGE = Coverage('synthetic-heat-demo', (-78.641, 35.769, -78.629, 35.774))


def synthetic_payload():
    features = []
    for event in ('Excessive Heat Warning', 'Flood Warning'):
        identifier = 'synthetic-' + event.lower().replace(' ', '-')
        features.append({'type': 'Feature', 'id': identifier,
            'geometry': mapping(box(-78.633, 35.7699, -78.632, 35.7701)),
            'properties': {'id': identifier, 'event': event, 'severity': 'Severe',
                'status': 'Actual', 'messageType': 'Alert', 'sent': DEMO_TIME.isoformat(),
                'effective': DEMO_TIME.isoformat(), 'onset': DEMO_TIME.isoformat(),
                'expires': (DEMO_TIME + timedelta(hours=2)).isoformat(),
                'certainty': 'Likely', 'urgency': 'Immediate',
                'description': 'SYNTHETIC FIXTURE, NOT LIVE CONDITIONS.'}})
    return {'type': 'FeatureCollection', 'features': features}


def scenario():
    graph, origin, _, _ = demo_scenario()
    graph.add_node(5, x=-78.630, y=35.773)
    graph.add_edge(4, 5, key=0, length=600, osmid=45)
    candidates = ingest_official_centers([{'id': 'center-a', 'name': 'Cooling Center A',
        'latitude': 35.770, 'longitude': -78.630, 'categories': ['cooling_centre'],
        'observed_at': DEMO_TIME.isoformat(), 'designated_cooling_center': True,
        'synthetic': True}], source_id='synthetic-government', authority='SYNTHETIC GOVERNMENT FIXTURE',
        source_url=TRUSTED_SOURCES['synthetic-government'], fetched_at=DEMO_TIME)
    candidates.append({'id': 'library-b', 'name': 'Library B', 'latitude': 35.773,
        'longitude': -78.630, 'categories': ['library'], 'source': 'OpenStreetMap',
        'synthetic': True})
    return graph, origin, candidates


def run_demo(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    graph, origin, candidates = scenario()
    payload = synthetic_payload()
    adapters = {
        'heat_nws': lambda now: heat_hazard_batch(parse_heat_alerts(payload,
            fetched_at=DEMO_TIME, query={'area': 'NC'}), now=now),
        'flood_nws': lambda now: flood_hazard_batch(parse_nws_alerts(payload,
            fetched_at=DEMO_TIME, query={'area': 'NC'}), now=now)}
    store = SnapshotStore(directory / 'hazards.json')
    service = RefreshService(store, COVERAGE, adapters=adapters)
    calls = []
    def forbidden(*args, **kwargs):
        calls.append('external_or_adapter')
        raise AssertionError('Offline demo cannot make external calls or invoke adapters.')
    with patch('requests.sessions.Session.request', side_effect=forbidden), patch('socket.socket.connect', side_effect=forbidden):
        state = service.refresh(now=DEMO_TIME)
        result = evaluate_relief_candidates(graph, origin, candidates, state,
                                           trusted_sources=TRUSTED_SOURCES)
        ox.save_graphml(graph, directory / 'roads.graphml')
        (directory / 'candidates.json').write_text(json.dumps(candidates))
        service.adapters = {key: forbidden for key in adapters}
        offline = relief_offline(service, directory / 'roads.graphml', origin,
            json.loads((directory / 'candidates.json').read_text()),
            now=DEMO_TIME + timedelta(seconds=30), trusted_sources=TRUSTED_SOURCES)
    summary = []
    for item in result['candidates']:
        comparison = item['comparison']
        summary.append({'id': item['destination']['id'], 'classification': item['destination']['classification'],
            **{mode: {'nodes': comparison[mode]['route']['route_node_ids'],
                       'distance_m': comparison[mode]['route']['total_distance_m'],
                       'risk_penalty': comparison[mode]['route']['total_risk_penalty'],
                       'exposure': item['exposure'][mode]} for mode in ('baseline', 'safer')},
            'avoided_edge_count': comparison['avoided_edge_count']})
    return {'scenario': 'SYNTHETIC HEAT + FLOOD; ALL LOCATIONS AND EVENTS ARE FIXTURES',
        'summary': {'selected_destination_id': result['selected_destination_id'],
            'candidates': summary, 'snapshot_schema': store.load().schema_version,
            'offline_selected_destination_id': offline['selected_destination_id'],
            'offline_mode': offline['data_state']['mode'], 'external_or_offline_adapter_calls': len(calls)},
        'comparison': result, 'offline': offline}


def main():
    with tempfile.TemporaryDirectory(prefix='wolfhacks-heat-demo-') as directory:
        print(json.dumps(run_demo(directory), indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
