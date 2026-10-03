"""Generic snapshots and reconnect contracts; network access is forbidden."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json

import osmnx as ox
import pytest
from shapely.geometry import Point, LineString, MultiPolygon, box

from survival_geo import Hazard, HazardBatch
from survival_geo.demo import demo_scenario
from survival_geo.errors import DataAccessError
from survival_geo.offline import (
    CacheError, DataState, RefreshService, SnapshotStore, make_hazard_snapshot,
    reevaluate_route, route_offline, route_with_state,
)
from survival_geo.offline.multi_hazard_demo import COVERAGE, DEMO_TIME as NOW, run_demo
from survival_geo.offline.snapshot import snapshot_from_dict
from survival_geo.offline.service import merge_hazard_batch


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail('Generic offline tests cannot access network.')
    monkeypatch.setattr('requests.sessions.Session.request', fail)
    monkeypatch.setattr('socket.socket.connect', fail)


def hazard(id='closure', severity='high', **metadata):
    return Hazard(id, 'road_closure', box(-78.633,35.7699,-78.632,35.7701), severity,
                  'synthetic evidence', .8, NOW,
                  {'freshness': 'current', 'reason': 'Synthetic evidence', **metadata})


def batch(*hazards, now=NOW, **kwargs):
    return HazardBatch(tuple(hazards), fetched_at=now, attempted_at=now,
                       coverage={'region_id': COVERAGE.region_id}, **kwargs)


def snapshot(*hazards, now=NOW, sources=None):
    return make_hazard_snapshot(COVERAGE, sources or {'test': batch(*hazards, now=now)},
                                now=now, snapshot_id='fixed-id')


def state(*hazards, now=NOW, origin='live', status='available', sources=None):
    snap = snapshot(now=now, sources=sources or {'test': batch(*hazards, now=now, data_origin=origin, status=status)})
    return DataState(COVERAGE, snap.sources, now, snapshot=snap, generic=True)


def service(tmp_path, adapters):
    return RefreshService(SnapshotStore(tmp_path / 'hazards.json'), COVERAGE, adapters=adapters)


@pytest.mark.parametrize('geometry', [Point(-78.632,35.77), LineString([(-78.633,35.77),(-78.632,35.77)]),
    box(-78.633,35.7699,-78.632,35.7701), MultiPolygon([box(-78.633,35.7699,-78.632,35.7701)])])
def test_geometry_timestamp_provenance_roundtrip(geometry):
    original = snapshot(replace(hazard(expires_at=(NOW + timedelta(hours=1)).isoformat()), geometry=geometry))
    loaded = snapshot_from_dict(json.loads(json.dumps(original.to_dict())))
    assert loaded.to_dict() == original.to_dict()
    assert loaded.hazards[0].geometry.equals_exact(geometry, 0)
    assert loaded.hazards[0].timestamp == NOW
    assert loaded.hazards[0].metadata['reason'] == 'Synthetic evidence'
    assert loaded.sources['test'].fetched_at == NOW
    assert 'graph' not in loaded.to_dict()


def test_multiple_hazard_types_and_empty_snapshot():
    first = hazard('flood')
    second = replace(hazard('fire'), hazard_type='wildfire')
    loaded = snapshot_from_dict(snapshot(first, second).to_dict())
    assert {h.hazard_type for h in loaded.hazards} == {'road_closure', 'wildfire'}
    empty = snapshot_from_dict(snapshot().to_dict())
    assert empty.hazards == () and empty.hazard_coverage_state == 'unavailable'


@pytest.mark.parametrize('mutate,code', [
    (lambda d: d.update(schema_version=99), 'INCOMPATIBLE_VERSION'),
    (lambda d: d.update(created_at='bad'), 'MALFORMED'),
    (lambda d: d.update(sources=[]), 'MALFORMED'),
    (lambda d: d.update(hazards={}), 'MALFORMED'),
    (lambda d: d['coverage'].update(bbox=[0,0,0,0]), 'MALFORMED')])
def test_invalid_envelope(mutate, code):
    data = snapshot(hazard()).to_dict()
    mutate(data)
    with pytest.raises(CacheError) as caught:
        snapshot_from_dict(data)
    assert caught.value.code == code


def test_partially_corrupt_records_preserve_valid_and_warn():
    data = snapshot(hazard('one'), hazard('two')).to_dict()
    data['hazards'][0]['geometry'] = {'type': 'Polygon', 'coordinates': []}
    loaded = snapshot_from_dict(data)
    assert len(loaded.hazards) == 1
    assert loaded.sources['test'].status == 'partial'
    assert 'INVALID_HAZARD' in loaded.sources['test'].issues[0]
    assert loaded.hazard_coverage_state == 'incomplete'


def test_missing_optional_fields_and_bad_owner_are_visible():
    data = snapshot(hazard()).to_dict()
    del data['hazards'][0]['confidence']
    del data['hazards'][0]['source']
    del data['sources']['test']['context']
    del data['sources']['test']['freshness_policy']
    loaded = snapshot_from_dict(data)
    assert loaded.hazards[0].source == 'unknown'
    assert loaded.hazard_coverage_state == 'unavailable'
    data['hazards'][0]['adapter'] = 'missing'
    loaded = snapshot_from_dict(data)
    assert loaded.warnings[0]['code'] == 'INVALID_HAZARD_OWNER'


def test_corrupt_source_does_not_discard_other_adapter():
    data = snapshot(sources={'one': batch(hazard('one')), 'two': batch(hazard('two'))}).to_dict()
    data['sources']['one']['fetched_at'] = 'invalid'
    loaded = snapshot_from_dict(data)
    assert loaded.sources['two'].hazards[0].id == 'two'
    assert loaded.sources['one'].status == 'unavailable'
    assert loaded.hazard_coverage_state == 'incomplete'


def test_store_atomic_replacement(tmp_path, monkeypatch):
    store = SnapshotStore(tmp_path / 'nested' / 'hazards.json')
    first, second = snapshot(), snapshot(hazard(), now=NOW + timedelta(seconds=1))
    store.save(first)
    import os
    actual = os.replace
    calls = []
    def replace_file(source, destination):
        assert store.load().created_at == first.created_at
        assert SnapshotStore(source).load().created_at == second.created_at
        calls.append(source)
        actual(source, destination)
    monkeypatch.setattr('survival_geo.offline.store.os.replace', replace_file)
    store.save(second)
    assert store.load().to_dict() == second.to_dict()
    assert len(calls) == 1 and not list(store.path.parent.glob('*.tmp'))


@pytest.mark.parametrize('operation', ['fsync', 'replace'])
def test_failed_write_preserves_bytes(tmp_path, monkeypatch, operation):
    store = SnapshotStore(tmp_path / 'hazards.json')
    store.save(snapshot(hazard()))
    before = store.path.read_bytes()
    def fail(*args):
        raise OSError('Synthetic write failure')
    monkeypatch.setattr(f'survival_geo.offline.store.os.{operation}', fail)
    with pytest.raises(CacheError):
        store.save(snapshot())
    assert store.path.read_bytes() == before


@pytest.mark.parametrize('contents,code', [(None,'MISSING'), ('{bad','MALFORMED'), ('[]','MALFORMED')])
def test_missing_corrupt_cache_allows_baseline_with_warning(tmp_path, contents, code):
    svc = service(tmp_path, {})
    if contents is not None:
        svc.store.path.write_text(contents)
    current = svc.load_offline(now=NOW)
    graph, origin, destination, _ = demo_scenario()
    result = route_with_state(graph, origin, destination, current)
    assert result['safer']['route']['route_node_ids'] == [1,2,3]
    assert result['data_state']['mode'] == 'UNAVAILABLE'
    assert result['data_state']['cache_error']['code'] == code
    assert result['hazard_data']['coverage_status'] == 'unavailable'


def test_cached_current_stale_expired_and_mixed(tmp_path):
    svc = service(tmp_path, {})
    svc.store.save(snapshot(hazard('short', expires_at=(NOW+timedelta(minutes=1)).isoformat()),
                            hazard('long')))
    current = svc.load_offline(now=NOW+timedelta(seconds=30))
    assert {h.metadata['data_state'] for h in current.hazards} == {'CACHED_CURRENT'}
    expired = svc.load_offline(now=NOW+timedelta(minutes=2))
    assert {h.metadata['data_state'] for h in expired.hazards} == {'EXPIRED','CACHED_CURRENT'}
    stale = svc.load_offline(now=NOW+timedelta(minutes=16))
    assert {h.metadata['data_state'] for h in stale.hazards} == {'EXPIRED','CACHED_STALE'}
    assert stale.snapshot.created_at == NOW


def test_expired_alert_has_no_penalty_or_destination_exclusion(tmp_path):
    svc = service(tmp_path, {})
    alert = replace(hazard(expires=(NOW+timedelta(minutes=1)).isoformat()), hazard_type='flood')
    svc.store.save(snapshot(alert))
    graph, origin, destination, _ = demo_scenario()
    result = route_with_state(graph, origin, destination, svc.load_offline(now=NOW+timedelta(minutes=2)),
        candidates=[{'latitude':35.770,'longitude':-78.6325}])
    assert result['safer']['route']['route_node_ids'] == [1,2,3]
    assert result['safer']['route']['total_risk_penalty'] == 0
    assert result['destinations']['accepted']
    assert result['hazard_data']['hazards'][0]['metadata']['freshness'] == 'expired'


def test_refresh_success_multiple_adapters(tmp_path):
    svc = service(tmp_path, {'one':lambda now: batch(hazard('one'), now=now),
                             'two':lambda now: batch(replace(hazard('two'), hazard_type='wildfire'), now=now)})
    current = svc.refresh(now=NOW)
    assert current.mode == 'LIVE' and current.cache_status == 'SAVED'
    assert len(svc.store.load().hazards) == 2
    assert current.to_dict()['sources']['one']['hazards'][0]['metadata']['data_state'] == 'LIVE_CURRENT'


def test_one_failed_adapter_retains_cached_with_original_times(tmp_path):
    svc = service(tmp_path, {'one':lambda now: batch(hazard('one'), now=now),
                             'two':lambda now: batch(hazard('two'), now=now)})
    svc.refresh(now=NOW)
    def fail(now):
        raise ConnectionError('Synthetic outage')
    svc.adapters['two'] = fail
    current = svc.refresh(now=NOW+timedelta(minutes=1))
    assert current.mode == 'MIXED'
    assert {h.id for h in current.hazards} == {'one','two'}
    assert current.sources['two'].fetched_at == NOW
    assert current.sources['one'].fetched_at == NOW+timedelta(minutes=1)
    assert current.sources['two'].hazards[0].metadata['data_state'] == 'CACHED_CURRENT'
    assert current.to_dict()['hazard_coverage_state'] == 'incomplete'
    assert 'Synthetic outage' in str(svc.store.load().warnings)


@pytest.mark.parametrize('have_cache', [True, False])
def test_all_fail_never_overwrite_cache(tmp_path, have_cache):
    svc = service(tmp_path, {'one':lambda now: batch(hazard(),now=now)})
    if have_cache:
        svc.refresh(now=NOW)
    before = svc.store.path.read_bytes() if have_cache else None
    svc.adapters['one'] = lambda now: HazardBatch(status='unavailable', issues=('Outage',), attempted_at=now, data_origin='none')
    current = svc.refresh(now=NOW+timedelta(minutes=1))
    if have_cache:
        assert svc.store.path.read_bytes() == before
        assert current.mode == 'CACHED' and current.cache_status == 'PRESERVED'
    else:
        assert not svc.store.path.exists() and current.mode == 'UNAVAILABLE'
    assert current.attempts['one']['status'] == 'unavailable'


def test_partial_upsert_preserves_original_fetch_times_and_no_deletion(tmp_path):
    svc = service(tmp_path, {'test':lambda now: batch(hazard('old'),now=now)})
    svc.refresh(now=NOW)
    later = NOW+timedelta(minutes=16)
    svc.adapters['test'] = lambda now: batch(replace(hazard('new'),timestamp=now),now=now,status='partial')
    current = svc.refresh(now=later)
    assert {h.id:h.metadata['data_state'] for h in current.hazards} == {'old':'CACHED_STALE','new':'LIVE_CURRENT'}
    assert current.mode == 'MIXED'
    assert len(svc.store.load().hazards) == 2


def test_complete_empty_refresh_retires_previous_hazards(tmp_path):
    svc = service(tmp_path, {'test':lambda now: batch(hazard(),now=now)})
    svc.refresh(now=NOW)
    svc.adapters['test'] = lambda now: batch(now=now)
    current = svc.refresh(now=NOW+timedelta(minutes=1))
    assert current.hazards == () and svc.store.load().hazards == ()


def test_scope_change_prevents_fallback():
    cached = batch(hazard(),data_origin='cached')
    live = HazardBatch(status='unavailable',coverage={'region_id':'elsewhere'},data_origin='none')
    merged = merge_hazard_batch(live,cached)
    assert not merged.hazards and 'scope differs' in merged.issues[-1]


def test_offline_graphml_no_adapter_invocation_evidence_and_filtering(tmp_path):
    def must_not_call(now):
        pytest.fail('Offline path invoked an adapter')
    svc = service(tmp_path, {'test':must_not_call})
    svc.store.save(snapshot(hazard()))
    graph, origin, destination, _ = demo_scenario()
    graph_path = tmp_path / 'roads.graphml'
    ox.save_graphml(graph,graph_path)
    result = route_offline(svc,graph_path,origin,destination,now=NOW+timedelta(seconds=30),
        candidates=[{'latitude':35.770,'longitude':-78.6325}])
    assert result['safer']['route']['route_node_ids'] == [1,4,3]
    assert result['destinations']['excluded'][0]['hazard_ids'] == ['closure']
    evidence = result['baseline']['route']['road_risks'][1]['contributions'][0]
    assert evidence['evidence']['reason'] == 'Synthetic evidence'
    assert evidence['evidence']['data_state'] == 'CACHED_CURRENT'
    assert result['data_state']['attempts'] == {}
    with pytest.raises(DataAccessError):
        route_offline(svc,tmp_path/'missing.graphml',origin,destination,now=NOW)


def route_for(current):
    graph, origin, destination, _ = demo_scenario()
    return graph, route_with_state(graph,origin,destination,current)['baseline']['route']


def test_unchanged_route_no_reroute_and_determinism():
    current = state()
    graph, route = route_for(current)
    first = reevaluate_route(graph,route,current,current)
    assert first['route_still_viable'] and not first['reroute_recommended']
    assert not first['changed_edges']
    assert first == reevaluate_route(graph,route,current,current)


@pytest.mark.parametrize('severity,viable', [('low',True),('high',True),('critical',False)])
def test_new_penalized_or_blocked_edge_reroutes(severity,viable):
    previous, current = state(), state(hazard(severity=severity),now=NOW+timedelta(minutes=1))
    graph, route = route_for(previous)
    change = reevaluate_route(graph,route,previous,current)
    assert change['route_still_viable'] == viable
    assert change['reroute_recommended'] and change['alternative_route']['route_node_ids'] == [1,4,3]
    assert change['newly_affected_edges'][0]['edge_id'] == {'u':2,'v':3,'key':0}
    assert change['newly_encountered_hazards'][0]['source'] == 'synthetic evidence'
    assert change['distance_difference_m'] == 200
    assert change['affected_edge_count'] == 1 and change['avoided_edge_count'] == 1


def test_resolved_hazard_vs_lost_stale_evidence():
    previous = state(hazard())
    current = state(now=NOW+timedelta(minutes=1))
    graph, route = route_for(previous)
    change = reevaluate_route(graph,route,previous,current)
    assert change['resolved_hazards'][0]['hazard_id'] == 'closure'
    assert not change['reroute_recommended']
    stale = replace(previous,evaluated_at=NOW+timedelta(minutes=16))
    change = reevaluate_route(graph,route,previous,stale)
    assert not change['resolved_hazards'] and change['hazard_applicability_unknown'] == ['closure']


def test_expired_hazard_resolution_is_not_clearance():
    previous = state(hazard(expires_at=(NOW+timedelta(minutes=1)).isoformat()))
    current = replace(previous,evaluated_at=NOW+timedelta(minutes=2))
    graph, route = route_for(previous)
    change = reevaluate_route(graph,route,previous,current)
    assert change['resolved_hazards'][0]['reason_code'] == 'HAZARD_EXPIRED'
    assert not change['reroute_recommended']


def test_no_alternative_and_recalculate_false():
    previous = state()
    current = state(replace(hazard(severity='critical'),geometry=box(-78.65,35.76,-78.62,35.78)))
    graph, route = route_for(previous)
    change = reevaluate_route(graph,route,previous,current)
    assert not change['route_still_viable'] and change['reroute_recommended']
    assert change['alternative_route'] is None and change['alternative_status'] == 'NO_ROUTE'
    assert change['old_route_summary']['total_distance_m'] == 900
    assert reevaluate_route(graph,route,previous,current,recalculate=False)['alternative_status'] == 'NOT_REQUESTED'


def test_missing_graph_edge_recommends_reroute():
    previous = state()
    graph, route = route_for(previous)
    graph.remove_edge(2,3,0)
    change = reevaluate_route(graph,route,previous,previous)
    assert not change['route_still_viable'] and 'ROUTE_EDGE_MISSING' in change['reason_codes']
    assert change['alternative_route']['route_node_ids'] == [1,4,3]


def test_demo_exact_sequence(tmp_path):
    result = run_demo(tmp_path)
    summary = result['summary']
    assert summary['initial_route'] == summary['offline_route'] == [1,2,3]
    assert summary['new_route'] == [1,4,3]
    assert summary['external_network_attempts'] == summary['offline_adapter_calls'] == 0
    assert summary['offline_source_state'] == 'CACHED_CURRENT'
    assert summary['cache_preserved_on_failure'] and summary['reroute_recommended']
    assert summary['previous_distance_m'] == 900 and summary['new_distance_m'] == 1100
    json.dumps(result,allow_nan=False)


def test_real_flood_registry_partial_failure_and_expiry(tmp_path):
    from survival_geo.flood import registered_flood_adapters
    from survival_geo.offline.demo import simulated_sources, SimulatedClient
    sources = simulated_sources(NOW,warning=True)
    nws, usgs = SimulatedClient(sources['nws']), SimulatedClient(sources['usgs'])
    svc = service(tmp_path,registered_flood_adapters(COVERAGE,nws_client=nws,usgs_client=usgs))
    initial = svc.refresh(now=NOW)
    assert initial.hazards[0].hazard_type == 'flood'
    assert initial.hazards[0].severity == 'high'
    assert initial.hazards[0].metadata['official_severity'] == 'Severe'
    later = NOW+timedelta(minutes=1)
    fresh = simulated_sources(later,warning=True)
    nws.result = fresh['nws']
    usgs.result = simulated_sources(later,unavailable=True)['usgs']
    current = svc.refresh(now=later)
    assert current.sources['nws'].data_origin == 'live'
    assert current.sources['usgs'].data_origin == 'cached'
    assert current.sources['usgs'].fetched_at == NOW
    graph, origin, destination, _ = demo_scenario()
    result = route_with_state(graph,origin,destination,current)
    assert result['safer']['route']['route_node_ids'] == [1,4,3]
    assert result['data_state']['mode'] == 'MIXED'
    expired = svc.load_offline(now=later+timedelta(hours=3))
    result = route_with_state(graph,origin,destination,expired)
    assert result['safer']['route']['route_node_ids'] == [1,2,3]
    assert expired.hazards[0].metadata['data_state'] == 'EXPIRED'


def test_flood_cancellation_removes_cached_hazard_on_partial_refresh(tmp_path):
    from survival_geo.flood import registered_flood_adapters
    from survival_geo.offline.demo import simulated_sources, SimulatedClient
    sources = simulated_sources(NOW,warning=True)
    nws, usgs = SimulatedClient(sources['nws']), SimulatedClient(sources['usgs'])
    svc = service(tmp_path,registered_flood_adapters(COVERAGE,nws_client=nws,usgs_client=usgs))
    initial = svc.refresh(now=NOW)
    later = NOW+timedelta(minutes=1)
    cancellation = replace(sources['nws'].records[0],message_type='Cancel',sent=later,fetched_at=later)
    nws.result = replace(sources['nws'],records=(cancellation,),fetched_at=later,attempted_at=later,status='partial')
    usgs.result = simulated_sources(later,unavailable=True)['usgs']
    current = svc.refresh(now=later)
    assert not current.hazards
    graph, route = route_for(initial)
    change = reevaluate_route(graph,route,initial,current)
    assert change['resolved_hazards'][0]['reason_code'] == 'HAZARD_WITHDRAWN_BY_SOURCE'


def test_complete_success_resolves_hazard_despite_unrelated_failed_adapter(tmp_path):
    svc = service(tmp_path,{'test':lambda now:batch(hazard(),now=now),'other':lambda now:batch(now=now)})
    previous = svc.refresh(now=NOW)
    svc.adapters['test'] = lambda now:batch(now=now)
    svc.adapters['other'] = lambda now:HazardBatch(status='unavailable',issues=('Outage',),data_origin='none')
    current = svc.refresh(now=NOW+timedelta(minutes=1))
    graph, route = route_for(previous)
    change = reevaluate_route(graph,route,previous,current)
    assert change['resolved_hazards'][0]['hazard_id'] == 'closure'


def test_existing_hazard_escalation_identifies_trigger_and_exposure_change():
    previous, current = state(hazard(severity='low')), state(hazard(severity='critical'))
    graph, route = route_for(previous)
    change = reevaluate_route(graph,route,previous,current)
    assert change['newly_encountered_hazards'] == []
    assert change['hazards_causing_reroute'][0]['id'] == 'closure'
    assert change['changed_edges'][0]['triggering_hazard_ids'] == ['closure']
    assert change['blocked_edge_count_difference'] == -1
    assert change['affected_edge_count_difference'] == -1
    assert change['hazard_exposure_difference'] == -1


def test_generic_refresh_write_failure_still_routes_fresh_state(tmp_path,monkeypatch):
    svc = service(tmp_path,{'test':lambda now:batch(now=now)})
    svc.refresh(now=NOW)
    before = svc.store.path.read_bytes()
    svc.adapters['test'] = lambda now:batch(hazard(),now=now)
    def fail(*args):
        raise OSError('Disk full')
    monkeypatch.setattr('survival_geo.offline.store.os.replace',fail)
    current = svc.refresh(now=NOW+timedelta(seconds=30))
    assert svc.store.path.read_bytes() == before
    assert current.cache_status == 'WRITE_FAILED' and not current.to_dict()['snapshot_persisted']
    graph, origin, destination, _ = demo_scenario()
    assert route_with_state(graph,origin,destination,current)['safer']['route']['route_node_ids'] == [1,4,3]


def test_unmapped_severity_is_reported_without_crashing_offline_filter_or_reevaluation():
    previous = state(hazard())
    current = state(hazard(severity='bad'),now=NOW+timedelta(minutes=1))
    graph, origin, destination, _ = demo_scenario()
    result = route_with_state(graph,origin,destination,current,candidates=[{'latitude':35.77,'longitude':-78.6325}])
    assert result['safer']['route']['route_node_ids'] == [1,2,3]
    assert result['hazard_data']['coverage_status'] == 'unavailable'
    assert result['data_state']['hazard_coverage_state'] == 'unavailable'
    graph, route = route_for(previous)
    change = reevaluate_route(graph,route,previous,current)
    assert not change['resolved_hazards']
    assert change['hazard_applicability_unknown'] == ['closure']
    assert change['data_state']['warnings'][0]['code'] == 'INVALID_POLICY_HAZARD'


def test_custom_policy_severity_survives_generic_codec_and_routing():
    from survival_geo import RiskPolicy, RiskLevel
    current = state(hazard(severity='adapter-defined'))
    graph, origin, destination, _ = demo_scenario()
    policy = RiskPolicy(severity_levels={'adapter-defined':RiskLevel.HIGH_RISK})
    result = route_with_state(graph,origin,destination,current,policy=policy)
    assert result['safer']['route']['route_node_ids'] == [1,4,3]


def test_still_blocked_route_keeps_reroute_trigger_and_same_route_possible():
    previous = state(hazard(severity='critical'))
    graph, route = route_for(previous)
    change = reevaluate_route(graph,route,previous,previous)
    assert change['reroute_recommended'] and not change['route_still_viable']
    from survival_geo import RiskPolicy, RiskLevel
    small_penalty = RiskPolicy(penalties={RiskLevel.SAFE:0,RiskLevel.CAUTION:10,RiskLevel.HIGH_RISK:20,RiskLevel.IMPASSABLE:0})
    graph, route = route_for(state())
    change = reevaluate_route(graph,route,state(),state(hazard(severity='low')),policy=small_penalty)
    assert change['reroute_recommended'] and change['alternative_status'] == 'SAME_ROUTE'
    assert change['avoided_edge_count'] == 0


def test_duplicate_hazards_and_empty_partial_do_not_replace_good_cache(tmp_path):
    svc = service(tmp_path,{'test':lambda now:batch(hazard(),now=now)})
    svc.refresh(now=NOW)
    before = svc.store.path.read_bytes()
    svc.adapters['test'] = lambda now:batch(hazard(),hazard(),now=now)
    current = svc.refresh(now=NOW+timedelta(minutes=1))
    assert svc.store.path.read_bytes() == before
    assert current.sources['test'].hazards[0].id == 'closure'
    assert 'DUPLICATE_HAZARDS' in str(current.attempts)


def test_future_effective_hazard_becomes_active_at_effective_time(tmp_path):
    svc = service(tmp_path,{})
    future = hazard(freshness='not_yet_active',effective_at=(NOW+timedelta(minutes=1)).isoformat())
    svc.store.save(snapshot(future))
    assert svc.load_offline(now=NOW).hazards[0].metadata['freshness'] == 'not_yet_active'
    assert svc.load_offline(now=NOW+timedelta(minutes=1)).hazards[0].metadata['freshness'] == 'current'


def test_failed_nws_registry_reports_unavailable_and_retains_cache(tmp_path):
    from survival_geo.flood import registered_flood_adapters
    from survival_geo.offline.demo import simulated_sources, SimulatedClient
    sources = simulated_sources(NOW,warning=True)
    nws, usgs = SimulatedClient(sources['nws']), SimulatedClient(sources['usgs'])
    svc = service(tmp_path,registered_flood_adapters(COVERAGE,nws_client=nws,usgs_client=usgs))
    svc.refresh(now=NOW)
    before = svc.store.path.read_bytes()
    later = NOW+timedelta(minutes=1)
    failed = simulated_sources(later,unavailable=True)
    nws.result, usgs.result = failed['nws'], failed['usgs']
    current = svc.refresh(now=later)
    assert current.attempts['nws']['status'] == 'unavailable'
    assert current.attempts['usgs']['status'] == 'unavailable'
    assert current.mode == 'CACHED'
    assert current.hazards[0].metadata['data_state'] == 'CACHED_CURRENT'
    assert svc.store.path.read_bytes() == before
