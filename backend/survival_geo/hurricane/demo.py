"""Deterministic synthetic hurricane + surge + flood demo; no network."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import osmnx as ox
from shapely.geometry import box, mapping

from ..demo import demo_scenario
from ..flood import parse_nws_alerts, flood_hazard_batch
from ..offline import Coverage, RefreshService, SnapshotStore, route_offline, route_with_state
from .nws import parse_tropical_alerts
from .adapter import hurricane_hazard_batch

DEMO_TIME = datetime(2026, 10, 3, 14, 10, tzinfo=timezone.utc)


def synthetic_payload(now=DEMO_TIME):
    """Official CAP field layout; all events and footprints here are synthetic."""
    features = []
    for event in ('Hurricane Warning', 'Storm Surge Warning', 'Flood Warning'):
        id = 'synthetic-' + event.lower().replace(' ', '-')
        features.append({'type': 'Feature', 'id': 'https://api.weather.gov/alerts/' + id,
            'geometry': mapping(box(-78.633,35.7699,-78.632,35.7701)),
            'properties': {'id': id, 'event': event, 'severity': 'Severe', 'status': 'Actual',
                'messageType': 'Alert', 'sent': now.isoformat(), 'effective': now.isoformat(),
                'expires': (now + timedelta(hours=2)).isoformat(),
                'description': 'SYNTHETIC DEMO ONLY; not a live alert.'}})
    return {'type': 'FeatureCollection', 'features': features}


def run_demo(directory):
    graph, origin, destination, _ = demo_scenario()
    coverage = Coverage('synthetic-hurricane-demo', (-78.641,35.769,-78.629,35.774))
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    store = SnapshotStore(directory / 'hazards.json')
    graph_path = directory / 'roads.graphml'
    payload = synthetic_payload()
    calls = []
    def forbidden(*args, **kwargs):
        calls.append('network')
        raise AssertionError('The hurricane demo must not use the network.')
    adapters = {
        'hurricane_nws': lambda now: hurricane_hazard_batch(parse_tropical_alerts(
            payload, fetched_at=DEMO_TIME, query={'area':'NC'}, data_origin='live'), now=now),
        'flood_nws': lambda now: flood_hazard_batch(parse_nws_alerts(
            payload, fetched_at=DEMO_TIME, query={'area':'NC'}, data_origin='live'), now=now)}
    service = RefreshService(store, coverage, adapters=adapters)
    with patch('requests.sessions.Session.request', side_effect=forbidden), patch('socket.socket.connect', side_effect=forbidden):
        ox.save_graphml(graph, graph_path)
        state = service.refresh(now=DEMO_TIME)
        result = route_with_state(graph, origin, destination, state)
        def adapter_forbidden(now):
            raise AssertionError('Offline routing must not invoke any adapter.')
        service.adapters = {key: adapter_forbidden for key in adapters}
        offline = route_offline(service, graph_path, origin, destination, now=DEMO_TIME+timedelta(seconds=30))
    baseline, safer = result['baseline']['route'], result['safer']['route']
    hurricane_ids = {h.id for h in state.hazards if h.hazard_type == 'hurricane'}
    return {'scenario': 'SYNTHETIC HURRICANE + STORM SURGE + FLOOD; NO LIVE DATA',
        'summary': {'baseline_route': baseline['route_node_ids'], 'safer_route': safer['route_node_ids'],
            'baseline_distance_m': baseline['total_distance_m'], 'safer_distance_m': safer['total_distance_m'],
            'hurricane_hazards_encountered': sorted(hurricane_ids & set(baseline['hazard_ids'])),
            'hurricane_hazards_avoided': sorted(hurricane_ids & set(result['avoided_hazard_ids'])),
            'affected_edge_count': baseline['affected_edge_count'], 'avoided_edge_count': result['avoided_edge_count'],
            'baseline_risk_penalty': baseline['total_risk_penalty'], 'safer_risk_penalty': safer['total_risk_penalty'],
            'baseline_passable': baseline['passable'], 'offline_route': offline['safer']['route']['route_node_ids'],
            'offline_mode': offline['data_state']['mode'], 'snapshot_schema': store.load().schema_version,
            'external_network_attempts': len(calls)},
        'comparison': result, 'offline': offline}


def main():
    with tempfile.TemporaryDirectory(prefix='wolfhacks-hurricane-demo-') as directory:
        print(json.dumps(run_demo(directory), indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
