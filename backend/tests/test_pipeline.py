"""Unified routing contracts; entirely synthetic and fixture-backed."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest
from survival_geo import HazardBatch, compare_hazard_routes
from survival_geo.demo import demo_scenario
from survival_geo.flood import parse_nws_alerts, parse_usgs_observations
from survival_geo.flood.adapter import flood_hazard_batch
from survival_geo.multi_hazard_demo import demo_result

NOW = datetime(2026, 10, 3, 14, 10, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parent / 'fixtures' / 'flood'


def scenario(severity='high', freshness='current'):
    graph, origin, destination, hazards = demo_scenario()
    hazard = replace(hazards[0], severity=severity,
                     metadata={'freshness': freshness, 'reason': 'Synthetic footprint'})
    return graph, origin, destination, hazard


def run(hazards, **kwargs):
    graph, origin, destination, _ = scenario()
    return compare_hazard_routes(graph, origin, destination, hazards,
                                 evaluated_at=NOW, **kwargs)


def test_empty_inputs_keep_baseline_and_unknown_coverage():
    result = run([])
    assert result['baseline']['route'] == result['safer']['route']
    assert result['hazard_data']['coverage_status'] == 'unavailable'
    assert result['avoided_edge_count'] == 0


@pytest.mark.parametrize('severity,changed,passable', [
    ('none', False, True), ('low', True, True), ('high', True, True), ('critical', True, False)])
def test_informational_penalized_and_blocked(severity, changed, passable):
    _, _, _, hazard = scenario(severity)
    result = run([hazard])
    baseline, safer = result['baseline']['route'], result['safer']['route']
    assert baseline['route_node_ids'] == [1, 2, 3]
    assert safer['route_node_ids'] == ([1, 4, 3] if changed else [1, 2, 3])
    assert baseline['passable'] == passable
    assert result['hazard_data']['coverage_status'] == 'available'
    assert baseline['road_risks'][0]['contributions'][0]['source'] == hazard.source


def test_multiple_hazards_add_penalties_once_each():
    _, _, _, hazard = scenario('low')
    result = run([hazard, replace(hazard, id='other', severity='high', hazard_type='wildfire')])
    road = result['baseline']['route']['road_risks'][0]
    assert road['hazard_penalty'] == 2750
    assert road['risk_level'] == 'HIGH_RISK'
    assert len(road['contributions']) == 2
    assert result['avoided_edge_count'] == 2


@pytest.mark.parametrize('state', ['stale', 'unknown', 'expired', 'not_yet_active'])
def test_noncurrent_evidence_visible_without_blocking(state):
    _, _, _, hazard = scenario('critical', state)
    result = run([hazard])
    assert result['safer']['route']['route_node_ids'] == [1, 2, 3]
    contribution = result['safer']['route']['road_risks'][0]['contributions'][0]
    assert contribution['freshness'] == state
    assert contribution['hazard_penalty'] == 0 and contribution['passable']
    assert result['hazard_data']['coverage_status'] == 'unavailable'


def test_malformed_partial_input_does_not_discard_valid_hazard():
    _, _, _, hazard = scenario()
    result = run([None, {'geometry': {'bad': True}}, hazard])
    assert result['safer']['route']['route_node_ids'] == [1, 4, 3]
    assert result['hazard_data']['coverage_status'] == 'incomplete'
    assert len([w for w in result['hazard_data']['warnings'] if w['code'] == 'INVALID_HAZARD']) == 2


def test_missing_optional_source_is_visible_and_not_authoritative():
    _, _, _, hazard = scenario('critical')
    record = hazard.to_dict()
    del record['source']
    del record['confidence']
    result = run([record])
    assert result['hazard_data']['hazards'][0]['source'] == 'unknown'
    assert result['safer']['route']['passable']
    assert result['hazard_data']['coverage_status'] == 'unavailable'


def test_failed_adapter_isolated_from_successful_adapter():
    _, _, _, hazard = scenario()
    def fail():
        raise RuntimeError('Synthetic outage')
    result = run([], adapters={'bad': fail, 'good': lambda: HazardBatch((hazard,))})
    assert result['safer']['route']['route_node_ids'] == [1, 4, 3]
    assert result['hazard_data']['coverage_status'] == 'incomplete'
    assert any(w['code'] == 'ADAPTER_FAILED' for w in result['hazard_data']['warnings'])


def test_conflicting_ids_are_not_silently_selected():
    _, _, _, hazard = scenario()
    result = run([hazard, replace(hazard, severity='none')])
    assert result['hazard_data']['hazards'] == []
    assert any(w['code'] == 'DUPLICATE_HAZARDS' for w in result['hazard_data']['warnings'])


def test_flood_adapter_unified_path_and_stale_alert_context():
    alerts = parse_nws_alerts(json.loads((FIXTURES / 'nws_active.json').read_text()), fetched_at=NOW)
    # Geometry matches the synthetic baseline and retains official evidence.
    graph, origin, destination, hazard = scenario()
    alerts = replace(alerts, records=(replace(alerts.records[0], geometry=hazard.geometry),))
    batch = flood_hazard_batch(alerts, now=NOW)
    result = run([], adapters={'flood': lambda: batch})
    assert result['safer']['route']['route_node_ids'] == [1, 4, 3]
    contribution = result['baseline']['route']['road_risks'][0]['contributions'][0]
    assert contribution['hazard_type'] == 'flood'
    assert contribution['evidence']['official_severity'] == 'Severe'
    assert contribution['freshness'] == 'current'
    assert contribution['risk_level'] == 'HIGH_RISK'
    old = replace(alerts, fetched_at=NOW - timedelta(hours=1),
                  records=tuple(replace(a, fetched_at=NOW - timedelta(hours=1)) for a in alerts.records))
    stale_result = run([], adapters={'flood': lambda: flood_hazard_batch(old, now=NOW)})
    assert stale_result['safer']['route']['route_node_ids'] == [1, 2, 3]
    assert stale_result['hazard_data']['hazards'][0]['metadata']['freshness'] == 'stale'


def test_determinism_including_reordered_hazards_and_demo():
    _, _, _, hazard = scenario('low')
    other = replace(hazard, id='other', severity='high')
    assert run([hazard, other]) == run([other, hazard])
    assert demo_result() == demo_result()
    result = demo_result()
    assert result['baseline']['route']['total_distance_m'] == 900
    assert result['safer']['route']['total_distance_m'] == 1100
    assert result['avoided_edge_count'] == 2
    json.dumps(result, allow_nan=False)


def test_usgs_stale_context_and_failed_source_preserve_nws():
    alerts = parse_nws_alerts(json.loads((FIXTURES / 'nws_active.json').read_text()), fetched_at=NOW)
    observations = parse_usgs_observations(
        json.loads((FIXTURES / 'usgs_observations.json').read_text()), fetched_at=NOW)
    observations = replace(observations, records=tuple(
        replace(o, observed_at=NOW - timedelta(hours=3)) for o in observations.records))
    batch = flood_hazard_batch(alerts, observations, now=NOW)
    result = run([], adapters={'flood': lambda: batch})
    assert result['safer']['route']['route_node_ids'] == [1, 4, 3]
    gauges = [c for c in result['hazard_data']['context'] if c['kind'] == 'water_observation']
    assert gauges and all(g['freshness'] == 'stale' and not g['used_for_routing'] for g in gauges)
    assert result['hazard_data']['coverage_status'] == 'incomplete'
    failed = replace(observations, status='unavailable', records=(), issues=('Outage',))
    result = run([], adapters={'flood': lambda: flood_hazard_batch(alerts, failed, now=NOW)})
    assert result['safer']['route']['route_node_ids'] == [1, 4, 3]
    assert result['hazard_data']['coverage_status'] == 'incomplete'


def test_no_passable_route_keeps_baseline_without_unsafe_fallback():
    from shapely.geometry import box
    _, _, _, hazard = scenario('critical')
    hazard = replace(hazard, geometry=box(-78.65, 35.76, -78.62, 35.78))
    result = run([hazard])
    assert result['baseline']['exists'] and not result['baseline']['route']['passable']
    assert not result['safer']['exists'] and result['safer']['route'] is None
    assert result['avoided_edge_count'] is None


def test_invalid_severity_geometry_and_missing_snapshot_do_not_crash():
    _, _, _, hazard = scenario()
    bad = hazard.to_dict()
    bad['geometry'] = {'type': 'LineString', 'coordinates': [[0, 0]]}
    result = run([bad, replace(hazard, severity='unmapped')])
    assert result['hazard_data']['coverage_status'] == 'unavailable'
    assert result['baseline']['route'] == result['safer']['route']
    assert len([w for w in result['hazard_data']['warnings'] if w['code'] == 'INVALID_HAZARD']) == 2
    assert run(None)['hazard_data']['coverage_status'] == 'unavailable'


def test_invalid_adapter_output_is_degraded_without_losing_valid_hazards():
    _, _, _, hazard = scenario()
    result = run([hazard], adapters={'broken': lambda: None})
    assert result['safer']['route']['route_node_ids'] == [1, 4, 3]
    assert result['hazard_data']['coverage_status'] == 'incomplete'
