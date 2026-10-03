"""Deterministic GraphML + schema-2 reconnect demo; no live APIs."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import osmnx as ox
from shapely.geometry import box

from ..demo import demo_scenario
from ..hazards import Hazard
from ..pipeline import HazardBatch
from .snapshot import Coverage
from .store import SnapshotStore
from .service import RefreshService
from .routing import route_offline, route_with_state

DEMO_TIME = datetime(2026, 10, 3, 14, tzinfo=timezone.utc)
COVERAGE = Coverage('synthetic-multi-hazard', (-78.641, 35.769, -78.629, 35.774))


class SyntheticAdapter:
    def __init__(self):
        self.available = True
        self.new_conditions = False
        self.calls = 0

    def __call__(self, now):
        self.calls += 1
        if not self.available:
            raise ConnectionError('SYNTHETIC outage')
        hazards = ()
        if self.new_conditions:
            hazards = (Hazard('synthetic-reconnect-closure', 'road_closure',
                box(-78.633, 35.7699, -78.632, 35.7701), 'critical', 'synthetic source', 1.0,
                timestamp=now, metadata={'freshness': 'current', 'expires_at': (now + timedelta(hours=2)).isoformat(),
                                         'reason': 'SYNTHETIC closure polygon intersects edge 2 to 3.'}),)
        return HazardBatch(hazards, fetched_at=now, attempted_at=now, coverage={'region_id': COVERAGE.region_id})


def run_demo(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    graph, origin, destination, _ = demo_scenario()
    graph_path = directory / 'roads.graphml'
    store = SnapshotStore(directory / 'multi-hazard.json')
    adapter = SyntheticAdapter()
    service = RefreshService(store, COVERAGE, adapters={'synthetic': adapter})
    network_attempts = []
    def forbidden(*args, **kwargs):
        network_attempts.append('attempt')
        raise AssertionError('Network access is forbidden in this synthetic demo.')
    with patch('requests.sessions.Session.request', side_effect=forbidden), patch('socket.socket.connect', side_effect=forbidden):
        ox.save_graphml(graph, graph_path)
        first_state = service.refresh(now=DEMO_TIME)
        first = route_with_state(graph, origin, destination, first_state)
        bytes_before = store.path.read_bytes()
        adapter.available = False
        outage_time = DEMO_TIME + timedelta(seconds=30)
        outage_state = service.refresh(now=outage_time)
        preserved = store.path.read_bytes() == bytes_before
        calls_before = adapter.calls
        offline_state = service.load_offline(now=outage_time)
        offline = route_offline(service, graph_path, origin, destination, now=outage_time)
        offline_adapter_calls = adapter.calls - calls_before
        adapter.available, adapter.new_conditions = True, True
        reconnect_time = DEMO_TIME + timedelta(minutes=1)
        current, change = service.refresh_and_reevaluate(
            graph, offline['safer']['route'], offline_state, now=reconnect_time)
        summary = {
            'initial_route': first['safer']['route']['route_node_ids'],
            'offline_route': offline['safer']['route']['route_node_ids'],
            'new_route': change['alternative_route']['route_node_ids'],
            'previous_distance_m': change['old_route_summary']['total_distance_m'],
            'new_distance_m': change['alternative_route']['total_distance_m'],
            'newly_affected_edges': [r['edge_id'] for r in change['newly_affected_edges']],
            'triggering_hazard_ids': change['newly_encountered_hazard_ids'],
            'triggering_source': change['newly_encountered_hazards'][0]['source'],
            'previous_snapshot_timestamp': change['old_snapshot_timestamp'],
            'current_snapshot_timestamp': change['new_snapshot_timestamp'],
            'offline_mode': offline['data_state']['mode'],
            'offline_source_state': offline['data_state']['sources']['synthetic']['data_state'],
            'freshness': current.hazards[0].metadata['data_state'],
            'reroute_recommended': change['reroute_recommended'],
            'cache_preserved_on_failure': preserved,
            'offline_adapter_calls': offline_adapter_calls,
            'external_network_attempts': len(network_attempts),
            'distance_difference_m': change['distance_difference_m'],
            'affected_edge_count': change['affected_edge_count'],
            'avoided_edge_count': change['avoided_edge_count']}
        return {'scenario': 'SYNTHETIC MULTI-HAZARD OFFLINE/RECONNECT; NO LIVE DATA',
                'summary': summary, 'initial': first, 'offline': offline,
                'outage_state': outage_state.to_dict(), 'reconnected_state': current.to_dict(),
                'reevaluation': change}


def main():
    with tempfile.TemporaryDirectory(prefix='wolfhacks-unified-offline-') as directory:
        print(json.dumps(run_demo(directory), indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
