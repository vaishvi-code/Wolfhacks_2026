"""Official-schema fixtures; no test in this module accesses a live service."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from shapely.geometry import MultiPolygon, box, mapping

from survival_geo import RiskLevel, compare_routes, evaluate_road_risks, road_edges
from survival_geo.demo import demo_scenario
from survival_geo.flood import (
    FloodPolicy, Freshness, FreshnessPolicy, NWSClient, USGSClient,
    compare_flood_routes, freshness, parse_nws_alerts, parse_usgs_observations,
    prepare_flood_hazards, use_local_fallback,
)
from survival_geo.flood.demo import main as flood_demo
from survival_geo.flood.freshness import OBSERVATION_FRESHNESS, parse_time
from survival_geo.flood.nws import NWS_ENDPOINT
from survival_geo.flood.transport import download_collection
from survival_geo.flood.usgs import USGS_ENDPOINT

NOW = datetime(2026, 10, 3, 14, 10, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parent / 'fixtures' / 'flood'


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail('Flood tests must use fixtures/mocks, not external services.')
    monkeypatch.setattr(requests.sessions.Session, 'request', fail)


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def nws(name='nws_active.json', **kwargs):
    return parse_nws_alerts(fixture(name), fetched_at=NOW, **kwargs)


def response(payload):
    result = Mock()
    result.json.return_value = payload
    return result


def session_for(*payloads):
    session = Mock()
    session.get.side_effect = [p if isinstance(p, Exception) else response(p) for p in payloads]
    return session


def test_nws_preserves_official_fields_and_geometry():
    source = nws()
    assert source.status == 'available'
    alert = source.records[0]
    assert alert.id == 'urn:oid:fixture-warning'
    assert alert.event == 'Flood Warning' and alert.severity == 'Severe'
    assert alert.geometry.geom_type == 'Polygon'
    assert alert.sent == datetime(2026, 10, 3, 14, tzinfo=timezone.utc)
    assert alert.onset == datetime(2026, 10, 3, 14, 15, tzinfo=timezone.utc)
    assert alert.expires == datetime(2026, 10, 3, 20, tzinfo=timezone.utc)
    assert alert.fetched_at == NOW and alert.source == 'NOAA/NWS'
    assert alert.metadata['certainty'] == 'Observed'
    assert alert.metadata['instruction'] and alert.metadata['description']
    hazard = prepare_flood_hazards(source, now=NOW).hazards[0]
    assert hazard.geometry.equals_exact(alert.geometry, 0)
    assert hazard.severity == 'Severe'
    assert hazard.timestamp == alert.sent
    assert hazard.metadata['evidence_kind'] == 'official_alert_area'
    assert hazard.metadata['source_endpoint'] == NWS_ENDPOINT
    json.dumps(hazard.to_dict(), allow_nan=False)


def test_nws_multipolygon():
    payload = fixture('nws_active.json')
    payload['features'][0]['geometry'] = mapping(MultiPolygon([
        box(-78.637, 35.7698, -78.636, 35.7702), box(-78.634, 35.7698, -78.633, 35.7702)]))
    hazard = prepare_flood_hazards(parse_nws_alerts(payload, fetched_at=NOW), now=NOW).hazards[0]
    assert hazard.geometry.geom_type == 'MultiPolygon'


def test_geometryless_alert_is_retained_but_never_buffered():
    source = nws('nws_no_geometry.json')
    assert source.status == 'available' and len(source.records) == 1
    snapshot = prepare_flood_hazards(source, now=NOW)
    assert not snapshot.hazards
    assert snapshot.alerts[0]['geometry'] is None
    assert 'No usable official polygon' in snapshot.alerts[0]['exclusion_reason']


@pytest.mark.parametrize('geometry', [
    {'type': 'Point', 'coordinates': [-78.635, 35.770]},
    {'type': 'Polygon', 'coordinates': []},
    {'type': 'Polygon', 'coordinates': [[[0, 0], [1, 1], [0, 1], [1, 0], [0, 0]]]},
    {'invalid': True},
])
def test_unusable_official_geometry_is_partial_context(geometry):
    payload = fixture('nws_active.json')
    payload['features'][0]['geometry'] = geometry
    source = parse_nws_alerts(payload, fetched_at=NOW)
    assert source.status == 'partial'
    assert len(source.records) == 1 and source.records[0].geometry is None
    assert not prepare_flood_hazards(source, now=NOW).hazards


def test_unrelated_alerts_are_filtered():
    payload = fixture('nws_active.json')
    payload['features'][0]['properties']['event'] = 'Heat Advisory'
    source = parse_nws_alerts(payload, fetched_at=NOW)
    assert source.status == 'available' and not source.records


@pytest.mark.parametrize('name,status,count', [
    ('nws_empty.json', 'available', 0), ('malformed.json', 'unavailable', 0),
    ('nws_partial.json', 'partial', 1),
])
def test_empty_malformed_and_partial_alerts(name, status, count):
    source = nws(name)
    assert source.status == status and len(source.records) == count


@pytest.mark.parametrize('key,value', [
    ('sent', 'bad'), ('expires', '2026-10-03T20:00:00'), ('severity', {}),
    ('references', 42), ('event', None), ('id', None),
])
def test_malformed_alert_fields_do_not_crash(key, value):
    payload = fixture('nws_active.json')
    payload['features'][0]['properties'][key] = value
    source = parse_nws_alerts(payload, fetched_at=NOW)
    assert source.status == 'partial' and source.issues
    assert not prepare_flood_hazards(source, now=NOW).hazards


def test_missing_expiry_is_not_current_routable_data():
    payload = fixture('nws_active.json')
    payload['features'][0]['properties']['expires'] = None
    snapshot = prepare_flood_hazards(parse_nws_alerts(payload, fetched_at=NOW), now=NOW)
    assert not snapshot.hazards and snapshot.alerts[0]['freshness'] == 'unknown'


def test_expired_and_ended_alerts_are_excluded():
    snapshot = prepare_flood_hazards(nws('nws_expired.json'), now=NOW, include_stale=True)
    assert not snapshot.hazards and snapshot.alerts[0]['freshness'] == 'expired'
    source = nws()
    source = replace(source, records=(replace(source.records[0], ends=NOW),))
    assert not prepare_flood_hazards(source, now=NOW).hazards


@pytest.mark.parametrize('status,message', [('Test', 'Alert'), ('Exercise', 'Alert'), ('Actual', 'Cancel')])
def test_non_actual_and_cancel_records_not_routed(status, message):
    source = nws()
    source = replace(source, records=(replace(source.records[0], status=status, message_type=message),))
    assert not prepare_flood_hazards(source, now=NOW).hazards


def test_cancellation_references_suppress_previous_alert():
    source = nws()
    alert = source.records[0]
    cancellation = replace(alert, id='cancel', message_type='Cancel',
                           metadata={**alert.metadata, 'references': [{'identifier': alert.id}]})
    snapshot = prepare_flood_hazards(replace(source, records=(alert, cancellation)), now=NOW)
    assert not snapshot.hazards
    assert 'Superseded' in snapshot.alerts[0]['exclusion_reason']


def test_future_onset_watch_remains_active_but_future_effective_is_excluded():
    source = nws()
    # Source fixture has a future onset; an already effective alert still applies.
    assert prepare_flood_hazards(source, now=NOW).hazards
    source = replace(source, records=(replace(source.records[0], effective=NOW + timedelta(hours=1)),))
    snapshot = prepare_flood_hazards(source, now=NOW)
    assert not snapshot.hazards and snapshot.alerts[0]['freshness'] == 'not_yet_active'


@pytest.mark.parametrize('changes,expected', [
    ({}, Freshness.CURRENT),
    ({'fetched_at': NOW - timedelta(minutes=16)}, Freshness.STALE),
    ({'expires_at': NOW}, Freshness.EXPIRED),
    ({'observed_at': None}, Freshness.UNKNOWN),
    ({'fetched_at': None}, Freshness.UNKNOWN),
    ({'observed_at': NOW + timedelta(minutes=3)}, Freshness.UNKNOWN),
    ({'effective_at': NOW + timedelta(seconds=1)}, Freshness.NOT_YET_ACTIVE),
])
def test_freshness_states(changes, expected):
    fields = {'observed_at': NOW, 'fetched_at': NOW, 'now': NOW}
    assert freshness(**{**fields, **changes}) == expected


def test_freshness_thresholds_are_explicit_and_timezone_aware():
    assert freshness(observed_at=NOW, fetched_at=NOW - timedelta(minutes=15), now=NOW) == Freshness.CURRENT
    assert freshness(observed_at=NOW - timedelta(hours=3), fetched_at=NOW,
                     now=NOW, policy=OBSERVATION_FRESHNESS) == Freshness.STALE
    # Long-lived alerts are not stale merely because originally issued hours ago.
    assert freshness(observed_at=NOW - timedelta(days=1), fetched_at=NOW, now=NOW) == Freshness.CURRENT
    with pytest.raises(ValueError):
        FreshnessPolicy(max_fetch_age=timedelta(seconds=-1))
    with pytest.raises(ValueError):
        parse_time('2026-10-03T14:00:00')


def test_stale_opt_in_preserves_original_times_and_labels():
    source = nws()
    later = NOW + timedelta(minutes=30)
    assert not prepare_flood_hazards(source, now=later).hazards
    hazard = prepare_flood_hazards(source, now=later, include_stale=True).hazards[0]
    assert hazard.metadata['freshness'] == 'stale'
    assert hazard.metadata['fetched_at'] == NOW.isoformat()


def test_usgs_observations_preserve_measurements_without_hazards():
    source = parse_usgs_observations(fixture('usgs_observations.json'), fetched_at=NOW)
    assert source.status == 'available'
    discharge, stage = source.records
    assert discharge.site_id == 'USGS-02087324'
    assert (discharge.parameter_code, discharge.unit, discharge.value) == ('00060', 'ft^3/s', 125.25)
    assert (stage.parameter_code, stage.unit, stage.raw_value) == ('00065', 'ft', '3.42')
    assert stage.observed_at == NOW - timedelta(minutes=10)
    assert stage.fetched_at == NOW and stage.expires_at is None
    assert stage.geometry.geom_type == 'Point'
    assert stage.metadata['approval_status'] == 'Provisional'
    assert stage.to_dict()['role'] == 'context_only'
    with pytest.raises(ValueError):
        prepare_flood_hazards(source, now=NOW)
    json.dumps(source.to_dict(), allow_nan=False)


@pytest.mark.parametrize('key,value', [
    ('value', None), ('value', 'Ice'), ('value', '-999999'), ('value', 'NaN'),
    ('value', True), ('time', None), ('unit_of_measure', None),
])
def test_invalid_usgs_records_are_reported(key, value):
    payload = fixture('usgs_observations.json')
    payload['features'][0]['properties'][key] = value
    source = parse_usgs_observations(payload, fetched_at=NOW)
    assert source.status == 'partial' and len(source.records) == 1 and source.issues


def test_usgs_empty_and_malformed_payloads():
    assert parse_usgs_observations(fixture('nws_empty.json'), fetched_at=NOW).status == 'available'
    assert parse_usgs_observations(fixture('malformed.json'), fetched_at=NOW).status == 'unavailable'


def test_nws_client_point_and_area_queries():
    session = session_for(fixture('nws_active.json'), fixture('nws_empty.json'))
    client = NWSClient(session=session, user_agent='unit-test/contact')
    assert client.fetch(point=(35.77, -78.64)).status == 'available'
    call = session.get.call_args
    assert call.args == (NWS_ENDPOINT,)
    assert call.kwargs['params'] == {'point': '35.77,-78.64'}
    assert call.kwargs['headers']['User-Agent'] == 'unit-test/contact'
    assert call.kwargs['timeout'] == (5, 20)
    assert client.fetch(area='NC').records == ()
    assert session.get.call_args.kwargs['params'] == {'area': 'NC'}
    with pytest.raises(ValueError):
        client.fetch(point=(35, -78), area='NC')


def test_usgs_client_bbox_site_and_secret_header():
    payload = fixture('usgs_observations.json')
    session = session_for(*[{'type': 'FeatureCollection', 'features': [feature]} for feature in payload['features']])
    result = USGSClient(session=session, api_key='test-key').fetch(point=(35.77, -78.64))
    assert result.status == 'available' and len(result.records) == 2
    for call in session.get.call_args_list:
        assert call.args == (USGS_ENDPOINT,)
        assert call.kwargs['headers']['X-Api-Key'] == 'test-key'
        west, south, east, north = map(float, call.kwargs['params']['bbox'].split(','))
        assert west < -78.64 < east and south < 35.77 < north
    assert [call.kwargs['params']['parameter_code'] for call in session.get.call_args_list] == ['00060', '00065']
    assert 'test-key' not in json.dumps(result.to_dict())
    session = session_for(fixture('nws_empty.json'))
    USGSClient(session=session).fetch(site_id='USGS-02087324', parameters=['00065'])
    assert session.get.call_args.kwargs['params']['monitoring_location_id'] == 'USGS-02087324'


@pytest.mark.parametrize('failure', [requests.Timeout(), requests.ConnectionError(), requests.HTTPError(), ValueError('bad JSON')])
def test_source_failures_return_unavailable(failure):
    nws_result = NWSClient(session=session_for(failure)).fetch(area='NC')
    usgs_result = USGSClient(session=session_for(failure, failure)).fetch(point=(35.77, -78.64))
    for result in (nws_result, usgs_result):
        assert result.status == 'unavailable' and result.fetched_at is None
        assert result.issues and not result.records


def test_partial_usgs_failure_retains_successful_parameter():
    payload = fixture('usgs_observations.json')
    payload['features'] = payload['features'][:1]
    result = USGSClient(session=session_for(payload, requests.Timeout())).fetch(point=(35.77, -78.64))
    assert result.status == 'partial' and len(result.records) == 1


def test_pagination_and_truncation():
    first = fixture('nws_active.json')
    first['pagination'] = {'next': NWS_ENDPOINT + '?cursor=next'}
    second = fixture('nws_no_geometry.json')
    client = NWSClient(session=session_for(first, second))
    result = client.fetch(area='NC')
    assert result.status == 'available' and len(result.records) == 2
    assert client.session.get.call_args.kwargs['params'] is None
    result = NWSClient(session=session_for(first), max_pages=1).fetch(area='NC')
    assert result.status == 'partial' and 'limit' in result.issues[0]
    result = NWSClient(session=session_for(first, requests.Timeout())).fetch(area='NC')
    assert result.status == 'partial' and len(result.records) == 1


def test_usgs_next_links_and_cross_origin_rejected():
    first = fixture('usgs_observations.json')
    first['links'] = [{'rel': 'next', 'href': USGS_ENDPOINT + '?offset=2'}]
    result = download_collection(USGS_ENDPOINT, {}, headers={},
                                 session=session_for(first, fixture('nws_empty.json')))
    assert result.status == 'available' and len(result.payload['features']) == 2
    first['links'][0]['href'] = 'https://untrusted.example/collect'
    session = session_for(first)
    result = download_collection(USGS_ENDPOINT, {}, headers={'X-Api-Key': 'secret'}, session=session)
    assert result.status == 'partial' and session.get.call_count == 1


def test_explicit_local_fallback_keeps_outage_and_old_timestamp():
    local = nws(query={'area': 'NC'})
    outage = NWSClient(session=session_for(requests.Timeout())).fetch(area='NC')
    fallback = use_local_fallback(outage, local)
    assert fallback.status == 'unavailable' and fallback.data_origin == 'cached'
    assert fallback.fetched_at == NOW and fallback.records[0].fetched_at == NOW
    assert prepare_flood_hazards(fallback, now=NOW).hazards
    assert not prepare_flood_hazards(fallback, now=NOW + timedelta(hours=1)).hazards
    with pytest.raises(ValueError):
        use_local_fallback(outage, replace(local, query={'area': 'SC'}))


def test_flood_intersection_and_route_comparison_preserve_provenance():
    graph, origin, destination, _ = demo_scenario()
    original = deepcopy(graph)
    source = nws()
    hazards = prepare_flood_hazards(source, now=NOW).hazards
    risks = evaluate_road_risks(road_edges(graph), hazards, FloodPolicy(), NOW)
    assert risks[1, 2, 0].risk_level == RiskLevel.HIGH_RISK and risks[1, 2, 0].passable
    assert risks[1, 4, 0].penalty == 0
    result = compare_flood_routes(graph, origin, destination, source, now=NOW)
    normal, flood = result['distance']['route'], result['flood_aware']['route']
    assert normal['route_node_ids'] == [1, 2, 3] and normal['total_distance_m'] == 900
    assert normal['total_risk_penalty'] == 5000
    assert normal['passable']  # a warning polygon does not prove closure
    assert flood['route_node_ids'] == [1, 4, 3] and flood['total_distance_m'] == 1100
    assert flood['total_risk_penalty'] == 0
    assert result['flood_hazards_avoided'][0]['source'] == 'NOAA/NWS'
    reason = normal['road_risks'][0]['reasons'][0]
    assert 'Road intersects active Flood Warning area' in reason
    assert 'Road is flooded' not in reason
    assert result['flood_data']['sources'][0]['endpoint'] == NWS_ENDPOINT
    assert result['flood_data']['alerts'][0]['freshness'] == 'current'
    assert result['flood_data']['assessment_status'] == 'current_inputs'
    assert dict(graph.edges) == dict(original.edges)
    json.dumps(result, allow_nan=False)


def test_flood_policy_configurable_and_never_closes_warning_area():
    graph, origin, destination, _ = demo_scenario()
    policy = FloodPolicy(penalties={RiskLevel.SAFE: 0, RiskLevel.CAUTION: 10,
                                   RiskLevel.HIGH_RISK: 20, RiskLevel.IMPASSABLE: 0})
    result = compare_flood_routes(graph, origin, destination, nws(), now=NOW, policy=policy)
    assert result['flood_aware']['route']['route_node_ids'] == [1, 2, 3]
    with pytest.raises(ValueError):
        FloodPolicy(event_levels={'Flood Warning': RiskLevel.IMPASSABLE})


@pytest.mark.parametrize('name', ['nws_empty.json', 'nws_expired.json', 'nws_no_geometry.json'])
def test_no_routable_flood_data_does_not_assert_safety(name):
    graph, origin, destination, _ = demo_scenario()
    result = compare_flood_routes(graph, origin, destination, nws(name), now=NOW)
    assert result['distance']['route']['route_node_ids'] == result['flood_aware']['route']['route_node_ids']
    assert not result['flood_data']['hazards']
    assert 'No applicable active flood hazard was returned.' in result['flood_data']['notice']
    assert 'road safety is unknown' in result['road_risks'][0]['reasons'][0]
    if name == 'nws_no_geometry.json':
        assert result['flood_data']['assessment_status'] == 'degraded'


def test_state_alert_outside_graph_does_not_claim_local_hazard():
    graph, origin, destination, _ = demo_scenario()
    source = nws()
    source = replace(source, records=(replace(source.records[0], geometry=box(-80, 36, -79.9, 36.1)),))
    result = compare_flood_routes(graph, origin, destination, source, now=NOW)
    assert not result['flood_data']['applicable_hazard_ids']
    assert 'No applicable active flood hazard was returned.' in result['flood_data']['notice']


def test_source_outage_and_gauges_never_mean_safe():
    graph, origin, destination, _ = demo_scenario()
    source = NWSClient(session=session_for(requests.Timeout())).fetch(area='NC')
    payload = fixture('usgs_observations.json')
    payload['features'][0]['properties']['value'] = '999999999'  # no invented threshold
    observations = parse_usgs_observations(payload, fetched_at=NOW)
    result = compare_flood_routes(graph, origin, destination, source, observations, now=NOW)
    assert result['distance']['exists'] and result['flood_aware']['exists']
    assert not result['flood_data']['hazards']
    assert result['flood_data']['assessment_status'] == 'degraded'
    assert 'NWS source unavailable' in result['flood_data']['notice']
    assert result['flood_data']['observations'][0]['role'] == 'context_only'
    assert result['flood_data']['observations'][0]['freshness'] == 'current'


def test_old_usgs_measurement_stays_stale_after_new_fetch():
    graph, origin, destination, _ = demo_scenario()
    payload = fixture('usgs_observations.json')
    payload['features'][0]['properties']['time'] = '2026-10-02T14:00:00Z'
    observations = parse_usgs_observations(payload, fetched_at=NOW)
    result = compare_flood_routes(graph, origin, destination, nws('nws_empty.json'), observations, now=NOW)
    assert result['flood_data']['observations'][0]['freshness'] == 'stale'
    assert result['flood_data']['assessment_status'] == 'degraded'
    assert not result['flood_data']['hazards']


def test_usgs_unavailable_does_not_discard_nws_hazards():
    graph, origin, destination, _ = demo_scenario()
    unavailable = USGSClient(session=session_for(requests.Timeout(), requests.Timeout())).fetch(point=origin)
    result = compare_flood_routes(graph, origin, destination, nws(), unavailable, now=NOW)
    assert result['flood_aware']['route']['route_node_ids'] == [1, 4, 3]
    assert result['flood_data']['sources'][1]['status'] == 'unavailable'
    assert result['flood_data']['assessment_status'] == 'degraded'


def test_stale_routing_reasons_do_not_claim_active_warning():
    graph, origin, destination, _ = demo_scenario()
    result = compare_flood_routes(graph, origin, destination, nws(),
                                  now=NOW + timedelta(minutes=30), include_stale=True)
    road = result['distance']['route']['road_risks'][0]
    assert 'Road intersects stale Flood Warning area' in road['reasons'][0]
    assert road['sources'][0]['freshness'] == 'stale'
    assert result['flood_data']['assessment_status'] == 'degraded'


def test_flood_comparison_no_directed_route():
    graph, origin, destination, _ = demo_scenario()
    result = compare_flood_routes(graph, destination, origin, nws(), now=NOW)
    assert not result['distance']['exists'] and not result['flood_aware']['exists']
    assert result['flood_hazards_avoided'] is None
    assert result['flood_data']['sources'][0]['status'] == 'available'


def test_generic_risk_demo_remains_unchanged():
    graph, origin, destination, hazards = demo_scenario()
    result = compare_routes(graph, origin, destination, hazards)
    assert result['risk_aware']['route']['route_node_ids'] == [1, 4, 3]
    assert not result['distance']['route']['passable']


def test_live_demo_mocked_no_warning(monkeypatch, capsys):
    graph, origin, destination, _ = demo_scenario()
    monkeypatch.setattr('survival_geo.flood.demo.NWSClient.fetch', lambda *a, **k: nws('nws_empty.json'))
    observations = parse_usgs_observations(fixture('usgs_observations.json'), fetched_at=NOW)
    monkeypatch.setattr('survival_geo.flood.demo.USGSClient.fetch', lambda *a, **k: observations)
    monkeypatch.setattr('survival_geo.flood.demo.load_road_graph', lambda *a, **k: graph)
    monkeypatch.setattr('survival_geo.flood.demo.utc_now', lambda: NOW)
    flood_demo(['--latitude', str(origin[0]), '--longitude', str(origin[1]),
                '--destination-latitude', str(destination[0]), '--destination-longitude', str(destination[1])])
    output = json.loads(capsys.readouterr().out)
    assert output['mode'] == 'live_official_sources'
    assert 'No applicable active flood hazard was returned.' in output['comparison']['flood_data']['notice']


def test_live_demo_keeps_source_info_when_graph_unavailable(monkeypatch, capsys):
    from survival_geo.errors import DataAccessError
    monkeypatch.setattr('survival_geo.flood.demo.NWSClient.fetch', lambda *a, **k: nws('nws_empty.json'))
    monkeypatch.setattr('survival_geo.flood.demo.USGSClient.fetch', lambda *a, **k: parse_usgs_observations(fixture('nws_empty.json'), fetched_at=NOW))
    def failed_graph(*args, **kwargs):
        raise DataAccessError('OSM unavailable')
    monkeypatch.setattr('survival_geo.flood.demo.load_road_graph', failed_graph)
    flood_demo([])
    output = json.loads(capsys.readouterr().out)
    assert output['routing_status'] == 'unavailable' and output['nws']['status'] == 'available'
