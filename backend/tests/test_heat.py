"""Synthetic official-schema evidence; all tests prohibit network access."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from shapely.geometry import box, mapping

from survival_geo import RiskLevel, compare_hazard_routes
from survival_geo.demo import demo_scenario
from survival_geo.errors import EmptyOSMResults
from survival_geo.flood import flood_hazard_batch, parse_nws_alerts
from survival_geo.hurricane import hurricane_hazard_batch, parse_tropical_alerts
from survival_geo.heat import (HeatPolicy, HeatNWSClient, heat_event, parse_heat_alerts,
    heat_hazard_batch, registered_heat_adapters, WeatherClient, parse_weather_observation,
    ReliefPolicy, discover_relief_candidates, evaluate_relief_candidates)
from survival_geo.heat.demo import DEMO_TIME as NOW, COVERAGE, TRUSTED_SOURCES, scenario, synthetic_payload, run_demo
from survival_geo.heat.relief import OFFICIAL, POTENTIAL
from survival_geo.offline import SnapshotStore, RefreshService

FIXTURES = Path(__file__).parent/'fixtures'/'heat'

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Heat tests must never use the network.')
    monkeypatch.setattr('requests.sessions.Session.request', forbidden)
    monkeypatch.setattr('socket.socket.connect', forbidden)

def fixture(name='warning.json'):
    return json.loads((FIXTURES/name).read_text())

def batch(name='warning.json', now=NOW, **kwargs):
    return heat_hazard_batch(parse_heat_alerts(fixture(name), fetched_at=NOW, query={'area':'NC'}), now=now, **kwargs)

def compare(b=None, **kwargs):
    graph, origin, dest, _ = demo_scenario()
    return compare_hazard_routes(graph, origin, dest, adapters={'heat':lambda: batch() if b is None else b}, evaluated_at=NOW, **kwargs)

def service(tmp_path, adapters=None):
    return RefreshService(SnapshotStore(tmp_path/'hazards.json'), COVERAGE,
        adapters=adapters or {'heat':lambda now:batch(now=now)})

def evaluate(tmp_path, candidates=None, **kwargs):
    graph, origin, default = scenario()
    state = service(tmp_path).refresh(now=NOW)
    return evaluate_relief_candidates(graph, origin, default if candidates is None else candidates,
        state, trusted_sources=TRUSTED_SOURCES, **kwargs)

@pytest.mark.parametrize('event,expected', [
    ('Excessive Heat Warning',True),('Excessive Heat Watch',True),('Heat Advisory',True),
    ('Extreme Heat Warning',True),('Extreme Heat Watch',True),('  extreme HEAT watch ',True),
    ('Flood Warning',False),('Hurricane Warning',False),('Extreme Wind Warning',False),
    ('Not a Heat Advisory',False),('Heat',False),(None,False)])
def test_events(event, expected):
    assert heat_event(event) is expected

@pytest.mark.parametrize('name,severity', [('warning.json','high'),('advisory.json','moderate')])
def test_normalized_provenance(name,severity):
    h = batch(name).hazards[0]
    assert h.hazard_type == 'heat' and h.severity == severity
    assert h.source == 'NOAA/NWS' and h.timestamp == NOW
    assert h.metadata['official_severity'] == 'Severe'
    assert h.metadata['certainty'] == 'Likely'
    assert h.metadata['urgency'] == 'Immediate'
    assert h.metadata['onset'] == NOW.isoformat()
    assert h.metadata['source_endpoint'] == 'https://api.weather.gov/alerts/active'
    assert h.metadata['freshness'] == 'current'
    assert h.metadata['risk_mapping']['blocking_reason'] is None

@pytest.mark.parametrize('name', ['missing_geometry.json','expired.json','empty.json'])
def test_nonspatial_or_inactive_no_routing_change(name):
    b = batch(name)
    assert not b.hazards
    result = compare(b)
    assert result['baseline']['route']['route_node_ids'] == result['safer']['route']['route_node_ids']
    assert result['hazard_data']['coverage_status'] != 'available'
    if name != 'empty.json':
        assert b.context[0]['exclusion_reason']

def test_duplicate_and_malformed_partial():
    b = batch('partial_duplicate.json')
    assert b.status == 'partial' and len(b.hazards) == 1 and b.issues

def test_stale_visible_unpenalized():
    b = batch(now=NOW+timedelta(minutes=16))
    assert b.hazards[0].metadata['freshness'] == 'stale'
    result = compare(b)
    assert result['baseline']['route']['hazard_ids']
    assert result['baseline']['route']['total_risk_penalty'] == 0

def test_heat_changes_route_without_blocking():
    result = compare()
    assert result['baseline']['route']['route_node_ids'] == [1,2,3]
    assert result['safer']['route']['route_node_ids'] == [1,4,3]
    assert result['baseline']['route']['passable'] is True
    assert result['baseline']['route']['total_risk_penalty'] == 2500
    assert result['safer']['route']['total_risk_penalty'] == 0
    assert result['avoided_edge_count'] == 1

def test_explicit_blocking_policy():
    with pytest.raises(ValueError):
        HeatPolicy(event_levels={'Excessive Heat Warning':RiskLevel.IMPASSABLE})
    p = HeatPolicy(event_levels={'Excessive Heat Warning':RiskLevel.IMPASSABLE}, blocking_reason='External closure policy fixture')
    assert batch(policy=p).hazards[0].severity == 'critical'
    p = HeatPolicy(event_levels={'Excessive Heat Warning':RiskLevel.SAFE})
    assert compare(batch(policy=p))['baseline']['route']['total_risk_penalty'] == 0

@pytest.mark.parametrize('event', ['Tornado Warning','Heat Warning','Excessive Heat Warning '])
def test_invalid_or_duplicate_policy(event):
    mapping_ = {'Excessive Heat Warning':RiskLevel.CAUTION,event:RiskLevel.HIGH_RISK}
    with pytest.raises(ValueError):
        HeatPolicy(event_levels=mapping_)

@pytest.mark.parametrize('name,expected', [('weather.json','current'),('stale_weather.json','stale')])
def test_weather_context(name,expected):
    b = parse_weather_observation(fixture(name), station='KRDU', fetched_at=NOW)
    assert not b.hazards
    c = b.context[0]
    assert c['freshness'] == expected and c['used_for_routing'] is False
    assert c['measurements']['heatIndex']['value'] == 40
    assert c['measurements']['temperature']['unitCode'] == 'wmoUnit:degC'

def test_weather_missing_humidity_no_inference():
    raw = fixture('weather.json')
    del raw['properties']['relativeHumidity']
    del raw['properties']['heatIndex']
    b = parse_weather_observation(raw, station='KRDU', fetched_at=NOW)
    assert b.context[0]['measurements']['relativeHumidity'] is None
    assert b.context[0]['measurements']['heatIndex'] is None

@pytest.mark.parametrize('raw', [None,{}, {'properties':None}, {'properties':{'timestamp':'broken'}}])
def test_weather_bad_envelopes(raw):
    assert parse_weather_observation(raw, station='KRDU', fetched_at=NOW).status == 'unavailable'

def test_source_outages_and_registration():
    session = Mock()
    session.get.side_effect = requests.Timeout()
    client = HeatNWSClient(session=session)
    weather = WeatherClient('KRDU',session=session)
    adapters = registered_heat_adapters(nws_client=client, observation_client=weather)
    assert set(adapters) == {'heat_nws','heat_weather'}
    assert all(fn(NOW).status == 'unavailable' for fn in adapters.values())

def test_discovery_reuses_categories_and_no_official_claim(monkeypatch):
    discover = Mock(return_value=[{'osm_id':1,'element_type':'node','latitude':35.77,'longitude':-78.63}])
    monkeypatch.setattr('survival_geo.heat.relief.discover_destinations',discover)
    custom = {'custom':{'amenity':['library']}}
    result = discover_relief_candidates(35.77,-78.64,categories=custom)
    assert discover.call_args.kwargs['categories'] == custom
    assert result['candidates'][0]['classification'] == POTENTIAL
    discover.side_effect = EmptyOSMResults('No candidates')
    assert discover_relief_candidates(35.77,-78.64)['candidates'] == []

def test_official_vs_potential_and_rank(tmp_path):
    result = evaluate(tmp_path)
    assert result['selected_destination_id'] == 'center-a'
    a,b = result['candidates']
    assert a['destination']['classification'] == OFFICIAL
    assert b['destination']['classification'] == POTENTIAL
    assert a['destination']['open'] is None
    assert a['exposure']['baseline']['by_hazard_type']['heat']['affected_edge_distance_m'] == 450
    assert a['exposure']['baseline']['by_hazard_type']['heat']['affected_edge_count'] == 1
    assert not a['exposure']['safer']['by_hazard_type']

def test_osm_cannot_spoof_official(tmp_path):
    _,_,rows = scenario()
    rows[0]['source'] = 'OpenStreetMap'
    rows[0]['classification'] = OFFICIAL
    result = evaluate(tmp_path,rows)
    assert result['candidates'][0]['destination']['classification'] == POTENTIAL

def test_untrusted_or_stale_official_evidence(tmp_path):
    graph,origin,rows = scenario()
    state = service(tmp_path).refresh(now=NOW)
    result = evaluate_relief_candidates(graph,origin,rows,state)
    assert result['candidates'][0]['destination']['classification'] == POTENTIAL
    rows[0]['official_evidence']['fetched_at'] = (NOW-timedelta(hours=1)).isoformat()
    result = evaluate(tmp_path,rows)
    assert result['candidates'][0]['destination']['designation_freshness'] == 'stale'
    assert result['candidates'][0]['destination']['classification'] == POTENTIAL

def test_unreachable_candidate(tmp_path):
    graph,origin,rows = scenario()
    graph.add_node(6,x=-78.639,y=35.773)
    rows.append({'id':'unreachable','latitude':35.773,'longitude':-78.639})
    state = service(tmp_path).refresh(now=NOW)
    result = evaluate_relief_candidates(graph,origin,rows,state)
    assert result['candidates'][-1]['exclusion_reasons'] == ['UNREACHABLE']

def test_hazardous_destination_excluded(tmp_path):
    raw = synthetic_payload()
    raw['features'][1]['geometry'] = mapping(box(-78.6301,35.7699,-78.6299,35.7701))
    graph,origin,rows = scenario()
    state = service(tmp_path, {'flood':lambda now:flood_hazard_batch(parse_nws_alerts(raw,fetched_at=NOW),now=now)}).refresh(now=NOW)
    result = evaluate_relief_candidates(graph,origin,rows,state,trusted_sources=TRUSTED_SOURCES)
    assert 'DESTINATION_HAZARD' in result['candidates'][0]['exclusion_reasons']
    assert result['selected_destination_id'] == 'library-b'

def test_no_candidates_invalid_missing_optional_duplicates(tmp_path):
    assert evaluate(tmp_path,[])['selected_destination_id'] is None
    rows = [{'id':'minimal','latitude':35.773,'longitude':-78.63}, {'id':'bad'}]
    result = evaluate(tmp_path,rows)
    assert result['selected_destination_id'] == 'minimal'
    assert result['candidates'][0]['destination']['name'] is None
    assert result['candidates'][1]['exclusion_reasons'] == ['INVALID_CANDIDATE']
    assert evaluate(tmp_path,[rows[0],rows[0]])['selected_destination_id'] is None

def test_distance_ties_and_order_independence(tmp_path):
    rows = [{'id':id,'latitude':35.773,'longitude':-78.63} for id in ('z','a')]
    assert evaluate(tmp_path,rows)['selected_destination_id'] == 'a'
    assert evaluate(tmp_path,list(reversed(rows)))['selected_destination_id'] == 'a'
    rows.append({'id':'short','latitude':35.773,'longitude':-78.635})
    assert evaluate(tmp_path,rows)['selected_destination_id'] == 'short'

def test_risk_precedes_shorter_distance(tmp_path):
    graph,origin,rows = scenario()
    graph.remove_edge(4,3,0)
    state = service(tmp_path).refresh(now=NOW)
    result = evaluate_relief_candidates(graph,origin,rows,state,
        relief_policy=ReliefPolicy(ranking=('risk_penalty','distance')))
    assert result['selected_destination_id'] == 'library-b'
    assert result['candidates'][0]['comparison']['safer']['route']['total_distance_m'] == 900
    assert result['candidates'][1]['comparison']['safer']['route']['total_distance_m'] == 1150

@pytest.mark.parametrize('event,parser,adapter', [('Flood Warning',parse_nws_alerts,flood_hazard_batch),
                                               ('Hurricane Warning',parse_tropical_alerts,hurricane_hazard_batch)])
def test_additive_multihazard(event,parser,adapter):
    raw = fixture(); raw['features'][0]['properties'].update(id='other',event=event)
    other = adapter(parser(raw,fetched_at=NOW),now=NOW)
    b = batch()
    combined = replace(b,hazards=b.hazards+other.hazards)
    r = compare(combined)
    assert r['baseline']['route']['total_risk_penalty'] == 5000
    assert r['safer']['route']['route_node_ids'] == [1,4,3]
    assert compare(replace(combined,hazards=tuple(reversed(combined.hazards))))['baseline'] == r['baseline']

def test_offline_demo_round_trip_and_zero_calls(tmp_path):
    r = run_demo(tmp_path)
    s = r['summary']
    assert s['snapshot_schema'] == 2
    assert s['external_or_offline_adapter_calls'] == 0
    assert s['selected_destination_id'] == s['offline_selected_destination_id'] == 'center-a'
    assert s['candidates'][0]['baseline']['risk_penalty'] == 5000
    assert s['candidates'][0]['safer']['distance_m'] == 1100
    assert r['offline']['candidates'][0]['comparison']['hazard_data']['hazards'][0]['metadata']['data_origin'] == 'cached'

def test_refresh_partial_failure_retains_all_sources(tmp_path):
    raw = synthetic_payload()
    raw['features'][1]['properties'].update(event='Hurricane Warning',id='hurricane')
    hurricane = hurricane_hazard_batch(parse_tropical_alerts(raw,fetched_at=NOW),now=NOW)
    flood = flood_hazard_batch(parse_nws_alerts(synthetic_payload(),fetched_at=NOW),now=NOW)
    s = service(tmp_path, {'heat':lambda now:batch(), 'flood':lambda now:flood,'hurricane':lambda now:hurricane})
    s.refresh(now=NOW)
    def failure(now): raise RuntimeError('Synthetic outage')
    s.adapters['heat'] = failure
    state = s.refresh(now=NOW+timedelta(seconds=30))
    assert {h.hazard_type for h in state.hazards} == {'heat','flood','hurricane'}
    assert next(h for h in state.hazards if h.hazard_type=='heat').metadata['fetched_at'] == NOW.isoformat()
    s.adapters = {key:failure for key in s.adapters}
    before = (tmp_path/'hazards.json').read_bytes()
    s.refresh(now=NOW+timedelta(seconds=60))
    assert (tmp_path/'hazards.json').read_bytes() == before

def test_expiry_on_offline_load_and_weather_cache(tmp_path):
    weather = parse_weather_observation(fixture('weather.json'),station='KRDU',fetched_at=NOW)
    s = service(tmp_path, {'heat':lambda now:batch(), 'weather':lambda now:weather})
    s.refresh(now=NOW)
    state = s.load_offline(now=NOW+timedelta(hours=3))
    assert all(h.metadata['freshness']=='expired' for h in state.hazards)
    assert state.hazard_batches['weather'].context[0]['freshness']=='stale'

def test_current_cancel_withdraws_cached_alert(tmp_path):
    s = service(tmp_path)
    s.refresh(now=NOW)
    raw = fixture()
    raw['features'][0]['properties']['messageType']='Cancel'
    s.adapters['heat'] = lambda now:heat_hazard_batch(parse_heat_alerts(raw,fetched_at=now),now=now)
    state = s.refresh(now=NOW+timedelta(seconds=10))
    assert not state.hazards

@pytest.mark.parametrize('value', [True,float('nan'),'hot'])
def test_invalid_measurements_not_used(value):
    raw=fixture('weather.json')
    raw['properties']['temperature']['value']=value
    b=parse_weather_observation(raw,station='KRDU',fetched_at=NOW)
    assert b.status=='partial' and b.context[0]['measurements']['temperature'] is None
    json.dumps(b.context,allow_nan=False)

def test_successful_mock_clients():
    session=Mock()
    response=Mock(status_code=200)
    response.json.return_value=fixture('weather.json')
    session.get.return_value=response
    b=WeatherClient('KRDU',session=session).fetch(now=NOW)
    assert b.context[0]['measurements']['heatIndex']['value']==40
    assert session.get.call_args.kwargs['allow_redirects'] is False
    response.json.return_value=fixture()
    source=HeatNWSClient(session=session).fetch(area='NC')
    assert len(source.records)==1

def test_missing_expiration_unknown_context():
    raw=fixture()
    del raw['features'][0]['properties']['expires']
    b=heat_hazard_batch(parse_heat_alerts(raw,fetched_at=NOW),now=NOW)
    assert not b.hazards and b.context[0]['freshness']=='unknown'

def test_stale_cancellation_cannot_remove_current_alert():
    raw=fixture()
    cancellation=deepcopy(raw['features'][0])
    cancellation['properties'].update(id='cancel',messageType='Cancel',
        effective=(NOW+timedelta(hours=1)).isoformat(),
        references=[{'identifier':raw['features'][0]['properties']['id']}])
    raw['features'].append(cancellation)
    b=heat_hazard_batch(parse_heat_alerts(raw,fetched_at=NOW),now=NOW)
    assert len(b.hazards)==1 and not b.removed_hazard_ids

def test_overlapping_heat_counts_edge_once(tmp_path):
    b=batch()
    second=replace(b.hazards[0],id='second-warning')
    s=service(tmp_path,{'heat':lambda now:replace(b,hazards=b.hazards+(second,))})
    graph,origin,rows=scenario()
    result=evaluate_relief_candidates(graph,origin,rows,s.refresh(now=NOW))
    exposure=result['candidates'][0]['exposure']['baseline']['by_hazard_type']['heat']
    assert exposure['affected_edge_count']==1 and exposure['affected_edge_distance_m']==450
    assert len(exposure['hazards'])==2

def test_stale_route_exposure_retains_evidence_without_active_distance(tmp_path):
    s=service(tmp_path)
    s.refresh(now=NOW)
    graph,origin,rows=scenario()
    result=evaluate_relief_candidates(graph,origin,rows,s.load_offline(now=NOW+timedelta(minutes=16)))
    group=result['candidates'][0]['exposure']['baseline']['by_hazard_type']['heat']
    assert group['affected_edge_count']==0 and group['affected_edge_distance_m']==0
    assert group['hazards'][0]['freshness']=='stale'

def test_weather_context_in_candidate_output(tmp_path):
    weather=parse_weather_observation(fixture('weather.json'),station='KRDU',fetched_at=NOW)
    s=service(tmp_path,{'heat':lambda now:batch(),'weather':lambda now:weather})
    graph,origin,rows=scenario()
    result=evaluate_relief_candidates(graph,origin,rows,s.refresh(now=NOW))
    assert result['candidates'][0]['weather_context'][0]['measurements']['heatIndex']['value']==40

def test_complete_empty_refresh_retires_heat(tmp_path):
    s=service(tmp_path)
    s.refresh(now=NOW)
    s.adapters['heat']=lambda now:batch('empty.json',now=now)
    assert not s.refresh(now=NOW+timedelta(seconds=20)).hazards
