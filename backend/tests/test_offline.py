"""Disk-backed orchestration tests. HTTP and socket network calls are forbidden."""
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path

import osmnx as ox
import pytest
from shapely.geometry import MultiPolygon, box

from survival_geo import RiskLevel, RiskPolicy, build_route
from survival_geo.demo import demo_scenario
from survival_geo.errors import DataAccessError
from survival_geo.flood.usgs import parse_usgs_observations
from survival_geo.offline import (
    CacheError, Coverage, RefreshService, SnapshotStore, make_snapshot,
    reevaluate_route, route_offline, route_with_state,
)
from survival_geo.offline.demo import DEMO_COVERAGE, DEMO_TIME, SimulatedClient, run_demo, simulated_sources
from survival_geo.offline.service import merge_source
from survival_geo.offline.snapshot import snapshot_from_dict


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Offline tests must not call external network services.')
    monkeypatch.setattr('requests.sessions.Session.request', forbidden)
    monkeypatch.setattr('socket.socket.connect', forbidden)


@pytest.fixture
def service(tmp_path):
    sources = simulated_sources(DEMO_TIME)
    return RefreshService(SnapshotStore(tmp_path / 'snapshot.json'), DEMO_COVERAGE,
                           nws_client=SimulatedClient(sources['nws']),
                           usgs_client=SimulatedClient(sources['usgs']))


def set_sources(service, now, **kwargs):
    sources = simulated_sources(now, **kwargs)
    service.nws_client.result, service.usgs_client.result = sources['nws'], sources['usgs']
    return sources


def snapshot(warning=True):
    return make_snapshot(DEMO_COVERAGE, simulated_sources(DEMO_TIME, warning=warning), now=DEMO_TIME)


def test_snapshot_json_roundtrip_and_hazard_provenance():
    original = snapshot()
    loaded = snapshot_from_dict(json.loads(json.dumps(original.to_dict())))
    assert loaded.to_dict() == original.to_dict()
    assert loaded.hazards[0].geometry.equals_exact(original.hazards[0].geometry, 0)
    assert loaded.sources['nws'].records[0].expires == DEMO_TIME + timedelta(hours=2)
    assert loaded.sources['nws'].records[0].sent == DEMO_TIME
    assert loaded.sources['nws'].fetched_at == DEMO_TIME
    assert loaded.coverage == DEMO_COVERAGE
    assert 'graph' not in loaded.to_dict()


def test_polygon_and_context_point_roundtrip():
    sources = simulated_sources(DEMO_TIME, warning=True)
    polygon = MultiPolygon([box(-78.633,35.7699,-78.632,35.7701), box(-78.636,35.771,-78.635,35.772)])
    sources['nws'] = replace(sources['nws'], records=(replace(sources['nws'].records[0], geometry=polygon),))
    payload = json.loads((Path(__file__).parent / 'fixtures/flood/usgs_observations.json').read_text())
    sources['usgs'] = parse_usgs_observations(payload, fetched_at=DEMO_TIME)
    loaded = snapshot_from_dict(make_snapshot(DEMO_COVERAGE, sources, now=DEMO_TIME).to_dict())
    assert loaded.hazards[0].geometry.geom_type == 'MultiPolygon'
    gauge = loaded.sources['usgs'].records[0]
    assert gauge.geometry.geom_type == 'Point' and gauge.value == 125.25
    assert gauge.metadata['approval_status'] == 'Provisional'
    assert gauge.observed_at == DEMO_TIME


def test_missing_cache_is_explicit(service):
    with pytest.raises(CacheError) as caught:
        service.store.load()
    assert caught.value.code == 'MISSING'
    state = service.load_offline(now=DEMO_TIME)
    assert state.mode == 'UNAVAILABLE'
    assert state.to_dict()['cache_error']['code'] == 'MISSING'
    assert not state.hazards


@pytest.mark.parametrize('payload,code', [
    ('{truncated', 'MALFORMED'), ('[]', 'MALFORMED'),
    ('{"schema_version":99}', 'INCOMPATIBLE_VERSION'),
    ('{"schema_version":true}', 'INCOMPATIBLE_VERSION'),
    ('{}', 'INCOMPATIBLE_VERSION'),
])
def test_malformed_or_incompatible_cache(service, payload, code):
    service.store.path.write_text(payload)
    state = service.load_offline(now=DEMO_TIME)
    assert state.cache_error['code'] == code
    assert state.mode == 'UNAVAILABLE'


@pytest.mark.parametrize('mutate', [
    lambda d: d['sources']['nws'].update(status='bad'),
    lambda d: d['sources']['nws'].update(endpoint='https://wrong.example'),
    lambda d: d['sources']['nws']['records'][0].update(fetched_at=None),
    lambda d: d['sources']['nws']['records'][0].update(geometry={'type':'Polygon','coordinates':[]}),
    lambda d: d['sources']['nws'].update(records=[{}, {}]),
    lambda d: d.update(created_at='no date'),
    lambda d: d.update(hazards=[]),
    lambda d: d['hazards'][0].update(confidence=float('nan')),
    lambda d: d['coverage'].update(bbox=[0,0,0,0]),
])
def test_structurally_invalid_snapshots_rejected(mutate):
    data = snapshot().to_dict()
    mutate(data)
    with pytest.raises(CacheError) as caught:
        snapshot_from_dict(data)
    assert caught.value.code == 'MALFORMED'


def test_atomic_replace_sees_complete_temp_and_old_file(service, monkeypatch):
    old, new = snapshot(False), snapshot(True)
    service.store.save(old)
    import os
    real_replace = os.replace
    calls = []
    def checked_replace(source, destination):
        assert SnapshotStore(source).load().id == new.id
        assert service.store.load().id == old.id
        assert source.parent == destination.parent
        calls.append(source)
        real_replace(source, destination)
    monkeypatch.setattr('survival_geo.offline.store.os.replace', checked_replace)
    service.store.save(new)
    assert len(calls) == 1 and service.store.load().id == new.id
    assert not list(service.store.path.parent.glob('*.tmp'))


@pytest.mark.parametrize('operation', ['replace', 'fsync'])
def test_interrupted_write_preserves_previous_cache(service, monkeypatch, operation):
    service.store.save(snapshot(False))
    before = service.store.path.read_bytes()
    def failed(*args):
        raise OSError('simulated interruption')
    monkeypatch.setattr(f'survival_geo.offline.store.os.{operation}', failed)
    with pytest.raises(CacheError) as caught:
        service.store.save(snapshot(True))
    assert caught.value.code == 'WRITE_FAILED'
    assert service.store.path.read_bytes() == before
    assert not list(service.store.path.parent.glob('.*.tmp'))


def test_invalid_snapshot_never_replaces_good_one(service):
    good = snapshot()
    service.store.save(good)
    with pytest.raises(CacheError):
        service.store.save(replace(good, hazards=()))
    assert service.store.load().id == good.id


def test_region_mismatch_is_explicit(service):
    service.store.save(snapshot())
    other = RefreshService(service.store, Coverage('another', (-79,35,-78,36)))
    assert other.load_offline(now=DEMO_TIME).cache_error['code'] == 'COVERAGE_MISMATCH'


def test_successful_refresh_persists_latest_sources(service):
    state = service.refresh(now=DEMO_TIME)
    assert state.mode == 'LIVE' and state.cache_status == 'SAVED'
    assert service.store.load().id == state.snapshot.id
    report = state.to_dict()
    assert report['sources']['nws']['data_state'] == 'LIVE_CURRENT'
    assert report['sources']['usgs']['data_state'] == 'LIVE_CURRENT'
    assert report['snapshot_persisted']
    assert report['cache_error'] is None


@pytest.mark.parametrize('minutes,label', [(5, 'CACHED_CURRENT'), (30, 'CACHED_STALE')])
def test_offline_empty_snapshot_freshness(service, minutes, label):
    online = service.refresh(now=DEMO_TIME)
    state = service.load_offline(now=DEMO_TIME + timedelta(minutes=minutes))
    report = state.to_dict()
    assert report['mode'] == 'CACHED' and report['attempts'] == {}
    assert report['sources']['nws']['data_state'] == label
    assert report['snapshot_created_at'] == online.snapshot.created_at.isoformat()
    assert report['snapshot_age_seconds'] == minutes * 60
    assert report['sources']['nws']['fetched_at'] == DEMO_TIME.isoformat()


def test_expired_cached_alert_is_preserved_but_not_routed(service):
    set_sources(service, DEMO_TIME, warning=True)
    service.refresh(now=DEMO_TIME)
    state = service.load_offline(now=DEMO_TIME + timedelta(hours=3))
    assert not state.hazards
    assert len(state.snapshot.hazards) == 1  # historical derivative, never used directly
    record = state.to_dict()['sources']['nws']['records'][0]
    assert record['data_state'] == 'EXPIRED'
    assert record['expires'] == (DEMO_TIME + timedelta(hours=2)).isoformat()


def test_stale_alert_policy_is_explicit(service):
    set_sources(service, DEMO_TIME, warning=True)
    service.refresh(now=DEMO_TIME)
    now = DEMO_TIME + timedelta(minutes=30)
    assert not service.load_offline(now=now).hazards
    opt_in = RefreshService(service.store, DEMO_COVERAGE, include_stale=True)
    state = opt_in.load_offline(now=now)
    assert state.hazards[0].metadata['freshness'] == 'stale'
    assert state.to_dict()['sources']['nws']['data_state'] == 'CACHED_STALE'


def test_total_failure_keeps_cache_byte_for_byte(service):
    set_sources(service, DEMO_TIME, warning=True)
    service.refresh(now=DEMO_TIME)
    before = service.store.path.read_bytes()
    now = DEMO_TIME + timedelta(minutes=5)
    set_sources(service, now, unavailable=True)
    state = service.refresh(now=now)
    assert state.mode == 'CACHED' and state.cache_status == 'PRESERVED'
    assert service.store.path.read_bytes() == before
    assert state.to_dict()['unavailable_during_attempt'] == ['nws', 'usgs']
    assert state.to_dict()['sources']['nws']['data_state'] == 'CACHED_CURRENT'
    assert state.hazards[0].metadata['fetched_at'] == DEMO_TIME.isoformat()


def test_total_failure_without_cache(service):
    set_sources(service, DEMO_TIME, unavailable=True)
    state = service.refresh(now=DEMO_TIME)
    assert state.mode == 'UNAVAILABLE' and not service.store.path.exists()
    assert state.to_dict()['sources']['nws']['data_state'] == 'UNAVAILABLE'
    assert not state.to_dict()['snapshot_persisted']


def test_one_success_one_failed_uses_cached_other_source(service):
    service.refresh(now=DEMO_TIME)
    now = DEMO_TIME + timedelta(minutes=20)
    fresh = set_sources(service, now, warning=True)
    service.usgs_client.result = simulated_sources(now, unavailable=True)['usgs']
    state = service.refresh(now=now)
    report = state.to_dict()
    assert report['mode'] == 'MIXED'
    assert report['sources']['nws']['data_state'] == 'LIVE_CURRENT'
    assert report['sources']['usgs']['data_state'] == 'CACHED_STALE'
    assert report['unavailable_during_attempt'] == ['usgs']
    cached = service.store.load()
    assert cached.sources['nws'].fetched_at == now
    assert cached.sources['usgs'].fetched_at == DEMO_TIME
    assert cached.sources['nws'].records == fresh['nws'].records


def test_successful_empty_nws_response_retires_old_alerts(service):
    set_sources(service, DEMO_TIME, warning=True)
    old = service.refresh(now=DEMO_TIME)
    set_sources(service, DEMO_TIME + timedelta(minutes=5))
    new = service.refresh(now=DEMO_TIME + timedelta(minutes=5))
    assert old.hazards and not new.hazards
    assert not service.store.load().sources['nws'].records


def test_partial_source_upserts_without_clearing_missing_records(service):
    set_sources(service, DEMO_TIME, warning=True)
    service.refresh(now=DEMO_TIME)
    now = DEMO_TIME + timedelta(minutes=5)
    fresh = set_sources(service, now, warning=True)
    extra = replace(fresh['nws'].records[0], id='second-alert')
    service.nws_client.result = replace(fresh['nws'], status='partial', records=(extra,), issues=('missing page',))
    state = service.refresh(now=now)
    rows = state.to_dict()['sources']['nws']['records']
    assert len(rows) == 2
    assert {r['data_state'] for r in rows} == {'CACHED_CURRENT', 'LIVE_CURRENT'}
    assert state.mode == 'MIXED'
    assert len(service.store.load().sources['nws'].records) == 2


def test_empty_partial_total_response_cannot_overwrite_cache(service):
    service.refresh(now=DEMO_TIME)
    before = service.store.path.read_bytes()
    set_sources(service, DEMO_TIME + timedelta(minutes=5))
    service.nws_client.result = replace(service.nws_client.result, status='partial')
    service.usgs_client.result = replace(service.usgs_client.result, status='partial')
    state = service.refresh(now=DEMO_TIME + timedelta(minutes=5))
    assert state.cache_status == 'PRESERVED'
    assert service.store.path.read_bytes() == before


def test_query_mismatch_does_not_use_cached_source(service):
    service.refresh(now=DEMO_TIME)
    set_sources(service, DEMO_TIME, unavailable=True)
    service.nws_client.result = replace(service.nws_client.result, query={'area':'SC'})
    state = service.refresh(now=DEMO_TIME)
    assert state.sources['nws'].fetched_at is None
    assert any('scope differs' in s for s in state.attempts['nws']['issues'])


def test_write_failure_keeps_live_result_and_reports_persistence_failure(service, monkeypatch):
    old = service.refresh(now=DEMO_TIME)
    set_sources(service, DEMO_TIME + timedelta(minutes=5), warning=True)
    def fail(*args):
        raise OSError('disk failure')
    monkeypatch.setattr('survival_geo.offline.store.os.replace', fail)
    state = service.refresh(now=DEMO_TIME + timedelta(minutes=5))
    assert state.hazards and state.mode == 'LIVE'
    assert state.cache_status == 'WRITE_FAILED' and not state.to_dict()['snapshot_persisted']
    assert service.store.load().id == old.snapshot.id


def test_fully_offline_graph_route_and_destination_filter(service, tmp_path, monkeypatch):
    graph, origin, destination, _ = demo_scenario()
    path = tmp_path / 'roads.graphml'
    ox.save_graphml(graph, path)
    set_sources(service, DEMO_TIME, warning=True)
    service.refresh(now=DEMO_TIME)
    def forbidden(*args, **kwargs):
        pytest.fail('Offline load must not even attempt a source fetch or graph download.')
    monkeypatch.setattr(service.nws_client, 'fetch', forbidden)
    monkeypatch.setattr(service.usgs_client, 'fetch', forbidden)
    monkeypatch.setattr('osmnx.graph_from_point', forbidden)
    result = route_offline(service, path, origin, destination, now=DEMO_TIME + timedelta(minutes=5),
                           candidates=[{'name':'affected', 'latitude':35.770, 'longitude':-78.6325},
                                       {'name':'unaffected', 'latitude':35.773, 'longitude':-78.635}])
    assert result['data_state']['mode'] == 'CACHED'
    assert result['flood_aware']['route']['route_node_ids'] == [1,4,3]
    assert len(result['destinations']['excluded']) == 1
    assert result['destinations']['accepted'][0]['name'] == 'unaffected'


def test_missing_graph_never_downloads(service, tmp_path):
    with pytest.raises(DataAccessError, match='downloads are disabled'):
        route_offline(service, tmp_path / 'missing.graphml', (35.77,-78.64), (35.77,-78.63), now=DEMO_TIME)


def test_offline_without_hazard_cache_routes_with_explicit_unknown_status(service, tmp_path):
    graph, origin, destination, _ = demo_scenario()
    path = tmp_path / 'roads.graphml'
    ox.save_graphml(graph, path)
    result = route_offline(service, path, origin, destination, now=DEMO_TIME)
    assert result['distance']['exists']
    assert result['data_state']['mode'] == 'UNAVAILABLE'
    assert result['flood_data']['assessment_status'] == 'degraded'


def test_graph_outside_declared_region_rejected(service):
    graph, origin, destination, _ = demo_scenario()
    graph.nodes[4]['x'] = -79
    with pytest.raises(CacheError) as caught:
        route_with_state(graph, origin, destination, service.load_offline(now=DEMO_TIME))
    assert caught.value.code == 'COVERAGE_MISMATCH'


def setup_route(service):
    graph, origin, destination, _ = demo_scenario()
    previous = service.refresh(now=DEMO_TIME)
    return graph, build_route(graph, origin, destination), previous


def test_route_still_viable_without_new_hazard(service):
    graph, route, previous = setup_route(service)
    now = DEMO_TIME + timedelta(minutes=5)
    set_sources(service, now)
    current, result = service.refresh_and_reevaluate(graph, route, previous, now=now)
    assert result['route_still_viable'] and not result['reroute_recommended']
    assert result['alternative_route'] is None
    assert not result['newly_affected_edges']
    assert result['old_state_timestamp'] == DEMO_TIME.isoformat()
    assert result['new_snapshot_timestamp'] == current.snapshot.created_at.isoformat()


def test_changed_route_recalculates_and_preserves_both_summaries(service):
    graph, route, previous = setup_route(service)
    now = DEMO_TIME + timedelta(minutes=5)
    set_sources(service, now, warning=True)
    _, result = service.refresh_and_reevaluate(graph, route, previous, now=now)
    assert result['reroute_recommended'] and result['route_updated']
    assert result['route_still_viable']  # penalized alert area is not a physical closure
    assert result['newly_affected_edges'][0]['edge_id'] == {'u':2,'v':3,'key':0}
    assert result['newly_encountered_hazard_ids'] == ['nws:synthetic-reconnect-warning']
    assert result['old_route_summary']['route']['route_node_ids'] == [1,2,3]
    assert result['old_route_summary']['total_risk_penalty'] == 0
    assert result['current_route_summary']['total_risk_penalty'] == 2500
    assert result['alternative_route']['route_node_ids'] == [1,4,3]
    assert 'NEW_HIGH_RISK_CONDITION' in result['reason_codes']
    json.dumps(result, allow_nan=False)


def test_no_different_route_and_no_passable_route(service):
    graph, route, previous = setup_route(service)
    graph.remove_edge(1,4,0)
    now = DEMO_TIME + timedelta(minutes=5)
    set_sources(service, now, warning=True)
    current = service.refresh(now=now)
    result = reevaluate_route(graph, route, previous, current)
    assert result['alternative_status'] == 'SAME_ROUTE'
    assert not result['route_updated'] and 'NO_BETTER_ROUTE' in result['reason_codes']
    # Explicit synthetic caller policy tests engine exclusion, not a flood inference.
    synthetic_closure_policy = RiskPolicy(severity_levels={'Severe':RiskLevel.IMPASSABLE})
    result = reevaluate_route(graph, route, previous, current, policy=synthetic_closure_policy)
    assert not result['route_still_viable'] and result['reroute_recommended']
    assert result['alternative_status'] == 'NO_ROUTE' and result['alternative_route'] is None


def test_missing_route_edge_recalculates(service):
    graph, route, previous = setup_route(service)
    graph.remove_edge(2,3,0)
    result = reevaluate_route(graph, route, previous, previous)
    assert not result['route_still_viable']
    assert 'ROUTE_EDGE_MISSING' in result['reason_codes']
    assert result['alternative_route']['route_node_ids'] == [1,4,3]


def test_alert_disappearance_only_claimed_after_complete_refresh(service):
    graph, origin, destination, _ = demo_scenario()
    route = build_route(graph, origin, destination)
    set_sources(service, DEMO_TIME, warning=True)
    previous = service.refresh(now=DEMO_TIME)
    later = DEMO_TIME + timedelta(minutes=30)
    cached = service.load_offline(now=later)
    unknown = reevaluate_route(graph, route, previous, cached)
    assert not unknown['hazards_no_longer_applicable']
    assert unknown['hazard_applicability_unknown'] == ['nws:synthetic-reconnect-warning']
    set_sources(service, later)
    fresh = service.refresh(now=later)
    cleared = reevaluate_route(graph, route, previous, fresh)
    assert cleared['hazards_no_longer_applicable'][0]['reason_code'] == 'NO_LONGER_INTERSECTS_IN_COMPLETE_REFRESH'


def test_expiration_explained_without_claiming_clearance(service):
    graph, origin, destination, _ = demo_scenario()
    route = build_route(graph, origin, destination)
    set_sources(service, DEMO_TIME, warning=True)
    old = service.refresh(now=DEMO_TIME)
    cached = service.load_offline(now=DEMO_TIME + timedelta(hours=3))
    result = reevaluate_route(graph, route, old, cached)
    assert result['hazards_no_longer_applicable'][0]['reason_code'] == 'ALERT_EXPIRED'
    assert 'DATA_LIMITED' in result['reason_codes']


def test_missing_geometry_does_not_falsely_retire_previous_hazard(service):
    graph, origin, destination, _ = demo_scenario()
    route = build_route(graph, origin, destination)
    set_sources(service, DEMO_TIME, warning=True)
    old = service.refresh(now=DEMO_TIME)
    now = DEMO_TIME + timedelta(minutes=5)
    sources = set_sources(service, now, warning=True)
    service.nws_client.result = replace(sources['nws'], records=(replace(sources['nws'].records[0], geometry=None),))
    current = service.refresh(now=now)
    result = reevaluate_route(graph, route, old, current)
    assert not result['hazards_no_longer_applicable']
    assert result['hazard_applicability_unknown'] == ['nws:synthetic-reconnect-warning']


def test_live_stale_records_do_not_falsely_retire_previous_hazard(service):
    graph, origin, destination, _ = demo_scenario()
    route = build_route(graph, origin, destination)
    set_sources(service, DEMO_TIME, warning=True)
    old = service.refresh(now=DEMO_TIME)
    # Simulate an adapter returning an older snapshot, not newly fetched content.
    stale = service.refresh(now=DEMO_TIME + timedelta(minutes=30))
    assert stale.to_dict()['sources']['nws']['data_state'] == 'LIVE_STALE'
    result = reevaluate_route(graph, route, old, stale)
    assert not result['hazards_no_longer_applicable']
    assert result['hazard_applicability_unknown']


def test_partial_gauge_refresh_uses_time_series_identity():
    payload = json.loads((Path(__file__).parent / 'fixtures/flood/usgs_observations.json').read_text())
    cached = parse_usgs_observations(payload, fetched_at=DEMO_TIME)
    later = DEMO_TIME + timedelta(minutes=5)
    row = replace(cached.records[0], id='new-measurement', observed_at=later, fetched_at=later)
    partial = replace(cached, records=(row,), status='partial', fetched_at=later)
    merged, origins = merge_source(partial, cached)
    assert len(merged.records) == 2
    assert cached.records[0].id not in origins and origins['new-measurement'] == 'live'
    assert origins[cached.records[1].id] == 'cached'


def test_demo_exact_offline_reconnect_sequence(tmp_path):
    result = run_demo(tmp_path)
    assert result['network_access'] == 'FORBIDDEN'
    assert result['online']['flood_aware']['route']['route_node_ids'] == [1,2,3]
    assert result['offline']['flood_aware']['route']['route_node_ids'] == [1,2,3]
    assert result['offline']['data_state']['mode'] == 'CACHED'
    assert result['cache_preserved_on_failure'] and result['cache_replaced_on_reconnect']
    assert result['reevaluation']['alternative_route']['route_node_ids'] == [1,4,3]
