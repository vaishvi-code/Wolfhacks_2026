"""Deterministic disk-backed reconnect demo; all network access is forbidden."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import osmnx as ox
from shapely.geometry import box, mapping

from ..demo import demo_scenario
from ..flood.models import SourceResult
from ..flood.nws import NWS_ENDPOINT, NWS_SOURCE, parse_nws_alerts
from ..flood.usgs import USGS_ENDPOINT, USGS_SOURCE, parse_usgs_observations
from .service import RefreshService
from .snapshot import Coverage
from .store import SnapshotStore
from .routing import route_offline, route_with_state

DEMO_TIME = datetime(2026, 10, 3, 14, tzinfo=timezone.utc)
DEMO_COVERAGE = Coverage('synthetic-raleigh', (-78.641, 35.769, -78.629, 35.774))


def simulated_sources(now, *, warning=False, unavailable=False):
    """Official-schema synthetic responses, never fetched or presented as real."""
    queries = {'nws': {'area': 'NC'}, 'usgs': {'bbox': '-78.7,35.7,-78.5,35.9', 'parameters': ['00060', '00065']}}
    if unavailable:
        return {name: SourceResult(source, endpoint, 'unavailable', (), None, now,
                                    query=queries[name], issues=('SIMULATED network unavailable.',), data_origin='none')
                for name, source, endpoint in [('nws', NWS_SOURCE, NWS_ENDPOINT), ('usgs', USGS_SOURCE, USGS_ENDPOINT)]}
    features = []
    if warning:
        features.append({'type': 'Feature', 'geometry': mapping(box(-78.633, 35.7699, -78.632, 35.7701)),
                         'properties': {'id': 'synthetic-reconnect-warning', 'event': 'Flood Warning',
                                        'severity': 'Severe', 'status': 'Actual', 'messageType': 'Alert',
                                        'sent': now.isoformat(), 'effective': now.isoformat(),
                                        'expires': (now + timedelta(hours=2)).isoformat(),
                                        'description': 'SYNTHETIC DEMO ONLY; not a real warning.'}})
    return {'nws': parse_nws_alerts({'type': 'FeatureCollection', 'features': features}, fetched_at=now,
                                    query=queries['nws'], data_origin='live'),
            'usgs': parse_usgs_observations({'type': 'FeatureCollection', 'features': []}, fetched_at=now,
                                             query=queries['usgs'], data_origin='live')}


class SimulatedClient:
    def __init__(self, result):
        self.result = result

    def fetch(self, **kwargs):
        return self.result


def run_demo(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    graph, origin, destination, _ = demo_scenario()
    graph_path = directory / 'roads.graphml'
    store = SnapshotStore(directory / 'snapshot.json')
    def no_network(*args, **kwargs):
        raise AssertionError('The offline/reconnect demo must never access the network.')
    with patch('requests.sessions.Session.request', side_effect=no_network), patch('socket.socket.connect', side_effect=no_network):
        ox.save_graphml(graph, graph_path)
        first = simulated_sources(DEMO_TIME)
        nws_client, usgs_client = SimulatedClient(first['nws']), SimulatedClient(first['usgs'])
        service = RefreshService(store, DEMO_COVERAGE, nws_client=nws_client, usgs_client=usgs_client)
        online_state = service.refresh(now=DEMO_TIME)
        online = route_with_state(graph, origin, destination, online_state)
        saved_before_failure = store.path.read_bytes()
        outage_time = DEMO_TIME + timedelta(minutes=5)
        failed = simulated_sources(outage_time, unavailable=True)
        nws_client.result, usgs_client.result = failed['nws'], failed['usgs']
        outage = service.refresh(now=outage_time)
        cache_preserved = store.path.read_bytes() == saved_before_failure
        offline = route_offline(service, graph_path, origin, destination, now=outage_time)
        reconnect_time = DEMO_TIME + timedelta(minutes=10)
        fresh = simulated_sources(reconnect_time, warning=True)
        nws_client.result, usgs_client.result = fresh['nws'], fresh['usgs']
        reconnected, reevaluation = service.refresh_and_reevaluate(
            graph, offline['flood_aware']['route'], service.load_offline(now=outage_time), now=reconnect_time)
        return {'scenario': 'SYNTHETIC OFFLINE/RECONNECT DEMO; NO LIVE DATA',
                'network_access': 'FORBIDDEN', 'online': online, 'offline': offline,
                'outage_state': outage.to_dict(), 'cache_preserved_on_failure': cache_preserved,
                'reconnected_state': reconnected.to_dict(), 'reevaluation': reevaluation,
                'cache_replaced_on_reconnect': store.load().id != online_state.snapshot.id}


def main():
    with tempfile.TemporaryDirectory(prefix='wolfhacks-offline-demo-') as directory:
        print(json.dumps(run_demo(directory), indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
