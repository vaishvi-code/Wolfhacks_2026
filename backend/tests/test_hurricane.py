"""Official-schema synthetic fixtures, no live internet required or allowed."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import Mock

import osmnx as ox
import pytest
import requests
from shapely.geometry import box

from survival_geo import RiskLevel, compare_hazard_routes
from survival_geo.demo import demo_scenario
from survival_geo.flood import parse_nws_alerts, flood_hazard_batch
from survival_geo.flood.models import SourceResult
from survival_geo.flood.nws import NWS_ENDPOINT, NWS_SOURCE
from survival_geo.hurricane import (
    HurricanePolicy, TropicalNWSClient, parse_tropical_alerts, tropical_event,
    hurricane_hazard_batch, registered_hurricane_adapters, NHCClient, parse_nhc_storms,
)
from survival_geo.hurricane.demo import run_demo, synthetic_payload
from survival_geo.hurricane.nhc import NHC_ENDPOINT
from survival_geo.offline import (
    Coverage, SnapshotStore, RefreshService, make_hazard_snapshot, route_offline,
    route_with_state, reevaluate_route,
)

NOW = datetime(2026,10,3,14,10,tzinfo=timezone.utc)
FIXTURES = Path(__file__).parent/'fixtures'/'hurricane'
COVERAGE = Coverage('synthetic-test',(-78.641,35.769,-78.629,35.774))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args,**kwargs):
        pytest.fail('Hurricane tests must not make external network calls.')
    monkeypatch.setattr('requests.sessions.Session.request',forbidden)
    monkeypatch.setattr('socket.socket.connect',forbidden)


def fixture(name='nws_warning.json'):
    return json.loads((FIXTURES/name).read_text())


def alerts(name='nws_warning.json',**kwargs):
    return parse_tropical_alerts(fixture(name),fetched_at=NOW,query={'area':'NC'},**kwargs)


def batch(name='nws_warning.json',**kwargs):
    return hurricane_hazard_batch(alerts(name),now=NOW,**kwargs)


def compare(tropical=None,**kwargs):
    graph,origin,destination,_=demo_scenario()
    return compare_hazard_routes(graph,origin,destination,adapters={
        'hurricane':lambda:batch() if tropical is None else tropical},evaluated_at=NOW,**kwargs)


def session_for(*payloads):
    session=Mock()
    responses=[]
    for payload in payloads:
        if isinstance(payload,Exception):
            responses.append(payload)
        else:
            response=Mock()
            response.json.return_value=payload
            responses.append(response)
    session.get.side_effect=responses
    return session


@pytest.mark.parametrize('event,accepted',[
    ('Hurricane Warning',True),('Hurricane Watch',True),('Tropical Storm Warning',True),
    ('Tropical Storm Watch',True),('Storm Surge Warning',True),('Storm Surge Watch',True),
    ('Hurricane Local Statement',True),('Typhoon Warning',True),('Tropical Cyclone Local Statement',True),
    ('Tropical Storm Advisory',True),('Flood Warning',False),('Heat Advisory',False),
    ('Severe Thunderstorm Warning',False),('Tornado Warning',False),('High Wind Warning',False),
    ('Hurricane Force Wind Warning',False),('Extreme Wind Warning',False),
    ('Not a Hurricane Warning',False)])
def test_tropical_event_filter(event,accepted):
    assert tropical_event(event) == accepted


def test_parser_all_supported_examples_and_normalized_evidence():
    source=alerts('nws_events.json')
    assert len(source.records)==8
    result=hurricane_hazard_batch(source,now=NOW)
    by_event={h.metadata['event']:h for h in result.hazards}
    warning=by_event['Hurricane Warning']
    assert warning.hazard_type=='hurricane' and warning.severity=='high'
    assert warning.source=='NOAA/NWS' and warning.timestamp==datetime(2026,10,3,14,tzinfo=timezone.utc)
    assert warning.geometry.geom_type=='Polygon'
    assert warning.metadata['official_severity']=='Extreme'
    assert warning.metadata['source_endpoint']==NWS_ENDPOINT
    assert warning.metadata['parameters']['stormName']==['SYNTHETIC TEST STORM']
    assert warning.metadata['freshness']=='current'
    assert warning.metadata['effect']=='tropical_cyclone_wind'
    assert 'Road intersects active Hurricane Warning area' in warning.metadata['reason']
    assert by_event['Hurricane Watch'].severity=='moderate'
    assert by_event['Storm Surge Warning'].metadata['effect']=='storm_surge'
    assert by_event['Hurricane Local Statement'].severity=='none'
    assert all(h.severity!='critical' for h in result.hazards)
    json.dumps([h.to_dict() for h in result.hazards],allow_nan=False)


def test_shared_client_transport_query_headers_and_filter():
    session=session_for(fixture('nws_events.json'))
    result=TropicalNWSClient(session=session,user_agent='test/contact',timeout=(1,2)).fetch(area='NC')
    assert len(result.records)==8 and result.data_origin=='live'
    assert session.get.call_args.args[0]==NWS_ENDPOINT
    assert session.get.call_args.kwargs['params']=={'area':'NC'}
    assert session.get.call_args.kwargs['headers']['User-Agent']=='test/contact'
    assert session.get.call_args.kwargs['timeout']==(1,2)


def test_partial_pagination_and_timeout_preserve_status():
    payload=fixture()
    payload['pagination']={'next':NWS_ENDPOINT+'?cursor=next'}
    partial=TropicalNWSClient(session=session_for(payload,requests.Timeout())).fetch()
    assert partial.status=='partial' and len(partial.records)==1
    failed=TropicalNWSClient(session=session_for(requests.Timeout())).fetch()
    result=hurricane_hazard_batch(failed,now=NOW)
    assert result.status=='unavailable' and not result.hazards and result.issues


@pytest.mark.parametrize('name,count,status',[
    ('nws_warning.json',1,'available'),('nws_empty.json',0,'available'),
    ('nws_no_geometry.json',0,'partial'),('nws_expired.json',0,'available'),
    ('nws_partial.json',1,'partial'),('nws_duplicate.json',1,'partial')])
def test_fixture_degraded_cases(name,count,status):
    result=batch(name)
    assert len(result.hazards)==count and result.status==status
    if name=='nws_no_geometry.json':
        assert result.context[0]['geometry'] is None
        assert not result.context[0]['used_for_routing']
        assert 'No usable official polygon' in result.context[0]['exclusion_reason']
    if name=='nws_expired.json':
        assert result.context[0]['freshness']=='expired'
        assert result.removed_hazard_ids


@pytest.mark.parametrize('field,value',[
    ('sent','bad'),('expires','2026-10-03T20:00:00'),('references',{}),
    ('severity',{}),('event',None),('id',None)])
def test_malformed_records_skip_without_crashing(field,value):
    payload=fixture()
    payload['features'][0]['properties'][field]=value
    source=parse_tropical_alerts(payload,fetched_at=NOW)
    result=hurricane_hazard_batch(source,now=NOW)
    assert not result.hazards and result.status=='partial'


def test_missing_optional_fields_and_malformed_geometry():
    payload=fixture()
    for field in ('parameters','headline','description','certainty'):
        payload['features'][0]['properties'].pop(field,None)
    result=hurricane_hazard_batch(parse_tropical_alerts(payload,fetched_at=NOW),now=NOW)
    assert len(result.hazards)==1
    payload['features'][0]['geometry']={'type':'Polygon','coordinates':[]}
    result=hurricane_hazard_batch(parse_tropical_alerts(payload,fetched_at=NOW),now=NOW)
    assert not result.hazards and result.status=='partial'


def test_unknown_freshness_retains_context_only():
    source=alerts()
    source=replace(source,records=(replace(source.records[0],expires=None),))
    result=hurricane_hazard_batch(source,now=NOW)
    assert not result.hazards and result.context[0]['freshness']=='unknown'
    assert compare(result)['hazard_data']['coverage_status']=='unavailable'


def test_stale_warning_visible_without_penalty():
    result=hurricane_hazard_batch(alerts(),now=NOW+timedelta(minutes=16))
    assert result.hazards[0].metadata['freshness']=='stale'
    compared=compare(result)
    assert compared['baseline']['route']['route_node_ids']==compared['safer']['route']['route_node_ids']
    assert compared['safer']['route']['total_risk_penalty']==0
    assert compared['hazard_data']['coverage_status']=='unavailable'


def test_cancellation_and_supersession_removal():
    source=alerts()
    previous=source.records[0]
    update=replace(previous,id='new-update',message_type='Update',metadata={
        **previous.metadata,'references':[{'identifier':previous.id}]})
    result=hurricane_hazard_batch(replace(source,records=(previous,update)),now=NOW)
    assert [h.id for h in result.hazards]==['nws:new-update']
    assert 'nws:'+previous.id in result.removed_hazard_ids
    cancelled=replace(update,message_type='Cancel')
    result=hurricane_hazard_batch(replace(source,records=(cancelled,)),now=NOW)
    assert not result.hazards and 'nws:'+previous.id in result.removed_hazard_ids


def test_stale_cancellation_does_not_withdraw_current_evidence():
    source=alerts()
    previous=source.records[0]
    cancelled=replace(previous,id='cancel',message_type='Cancel',fetched_at=NOW-timedelta(hours=1),metadata={
        **previous.metadata,'references':[{'identifier':previous.id}]})
    result=hurricane_hazard_batch(replace(source,records=(previous,cancelled)),now=NOW)
    assert [h.id for h in result.hazards]==['nws:'+previous.id]
    assert 'nws:'+previous.id not in result.removed_hazard_ids


def test_warning_penalty_changes_route_and_unaffected_edge_has_no_risk():
    result=compare()
    assert result['baseline']['route']['route_node_ids']==[1,2,3]
    assert result['safer']['route']['route_node_ids']==[1,4,3]
    assert result['baseline']['route']['total_risk_penalty']==2500
    assert result['baseline']['route']['passable']
    assert result['avoided_edge_count']==1
    assert all(not r['hazard_ids'] for r in result['safer']['route']['road_risks'])


def test_no_tropical_alerts_is_not_a_safety_claim():
    result=compare(batch('nws_empty.json'))
    assert result['baseline']['route']==result['safer']['route']
    assert result['hazard_data']['coverage_status']=='unavailable'


def test_duplicate_alert_counted_once():
    result=compare(batch('nws_duplicate.json'))
    assert result['baseline']['route']['total_risk_penalty']==2500
    assert len(result['baseline']['route']['hazard_ids'])==1


def test_explicit_policy_blocking_requires_justification():
    with pytest.raises(ValueError):
        HurricanePolicy(event_levels={'Hurricane Warning':RiskLevel.IMPASSABLE})
    policy=HurricanePolicy(event_levels={'Hurricane Warning':RiskLevel.IMPASSABLE},blocking_reason='Explicit demo routing exclusion policy')
    result=compare(batch(policy=policy))
    assert not result['baseline']['route']['passable']
    assert result['safer']['route']['route_node_ids']==[1,4,3]
    assert result['hazard_data']['hazards'][0]['metadata']['risk_mapping']['blocking_reason']


def test_configurable_penalties_and_informational_override():
    policy=HurricanePolicy(event_levels={'Hurricane Warning':RiskLevel.SAFE})
    result=compare(batch(policy=policy),policy=policy)
    assert result['baseline']['route']==result['safer']['route']
    small=HurricanePolicy(penalties={RiskLevel.SAFE:0,RiskLevel.CAUTION:10,RiskLevel.HIGH_RISK:20,RiskLevel.IMPASSABLE:0})
    result=compare(batch(policy=small),policy=small)
    assert result['safer']['route']['route_node_ids']==[1,2,3]
    assert result['safer']['route']['total_risk_penalty']==20


def test_flood_hurricane_surge_combination_is_deterministic():
    payload=synthetic_payload(NOW)
    tropical=hurricane_hazard_batch(parse_tropical_alerts(payload,fetched_at=NOW),now=NOW)
    flood=flood_hazard_batch(parse_nws_alerts(payload,fetched_at=NOW),now=NOW)
    graph,origin,destination,_=demo_scenario()
    def run():
        return compare_hazard_routes(graph,origin,destination,adapters={'tropical':lambda:tropical,'flood':lambda:flood},evaluated_at=NOW)
    result=run()
    assert result==run()
    affected=result['baseline']['route']['road_risks'][1]
    assert affected['hazard_penalty']==7500 and len(affected['contributions'])==3
    assert {c['hazard_type'] for c in affected['contributions']}=={'flood','hurricane'}
    assert {c['evidence']['event'] for c in affected['contributions']}=={'Flood Warning','Hurricane Warning','Storm Surge Warning'}


def test_generic_snapshot_roundtrip_and_offline_no_adapter_calls(tmp_path):
    store=SnapshotStore(tmp_path/'hazards.json')
    original=make_hazard_snapshot(COVERAGE,{'hurricane':batch()},now=NOW)
    store.save(original)
    loaded=store.load()
    assert loaded.schema_version==2 and loaded.to_dict()==original.to_dict()
    assert loaded.hazards[0].geometry.equals_exact(original.hazards[0].geometry,0)
    graph,origin,destination,_=demo_scenario()
    graph_path=tmp_path/'roads.graphml'
    ox.save_graphml(graph,graph_path)
    def forbidden(now):
        pytest.fail('Offline path invoked a hurricane adapter.')
    service=RefreshService(store,COVERAGE,adapters={'hurricane':forbidden})
    result=route_offline(service,graph_path,origin,destination,now=NOW+timedelta(seconds=30))
    assert result['safer']['route']['route_node_ids']==[1,4,3]
    assert result['data_state']['mode']=='CACHED'
    assert result['data_state']['hazards'][0]['metadata']['data_state']=='CACHED_CURRENT'
    expired=route_offline(service,graph_path,origin,destination,now=NOW+timedelta(hours=7))
    assert expired['safer']['route']['route_node_ids']==[1,2,3]
    assert expired['data_state']['hazards'][0]['metadata']['data_state']=='EXPIRED'


def test_registered_refresh_mixed_failure_preserves_flood_and_cached_hurricane(tmp_path):
    client=Mock()
    client.fetch.return_value=alerts(data_origin='live')
    registry=registered_hurricane_adapters(nws_client=client,include_nhc=False)
    payload=synthetic_payload(NOW)
    registry['flood']=lambda now:flood_hazard_batch(parse_nws_alerts(payload,fetched_at=now,data_origin='live'),now=now)
    service=RefreshService(SnapshotStore(tmp_path/'hazards.json'),COVERAGE,adapters=registry)
    first=service.refresh(now=NOW)
    assert {h.hazard_type for h in first.hazards}=={'flood','hurricane'}
    assert first.cache_status=='SAVED'
    client.fetch.side_effect=requests.Timeout('Synthetic hurricane source outage')
    current=service.refresh(now=NOW+timedelta(minutes=1))
    assert current.mode=='MIXED'
    assert current.sources['hurricane_nws'].status=='unavailable'
    assert current.sources['hurricane_nws'].fetched_at==NOW
    assert current.sources['flood'].fetched_at==NOW+timedelta(minutes=1)
    assert {h.hazard_type for h in current.hazards}=={'flood','hurricane'}
    assert current.attempts['hurricane_nws']['issues']
    graph,origin,destination,_=demo_scenario()
    assert route_with_state(graph,origin,destination,current)['safer']['route']['route_node_ids']==[1,4,3]


def test_hurricane_failure_alone_preserves_cache_bytes(tmp_path):
    client=Mock()
    client.fetch.return_value=alerts(data_origin='live')
    service=RefreshService(SnapshotStore(tmp_path/'hazards.json'),COVERAGE,
        adapters=registered_hurricane_adapters(nws_client=client,include_nhc=False))
    service.refresh(now=NOW)
    before=service.store.path.read_bytes()
    client.fetch.side_effect=requests.Timeout()
    failed=service.refresh(now=NOW+timedelta(minutes=1))
    assert service.store.path.read_bytes()==before
    assert failed.mode=='CACHED' and failed.hazards


def test_nhc_structured_context_wind_and_no_routing_hazard():
    result=parse_nhc_storms(fixture('nhc_storms.json'),fetched_at=NOW)
    assert result.status=='available' and not result.hazards
    storm=result.context[0]
    assert storm['id']=='al992026' and storm['name']=='SYNTHETIC TEST STORM'
    assert storm['geometry']=={'type':'Point','coordinates':(-74.0,29.0)}
    assert storm['intensity']=={'value':85.0,'unit':'knots','raw_value':'85'}
    assert storm['classification']=='HU' and storm['category'] is None
    assert storm['products']['forecastAdvisory']['advNum']=='001'
    assert not storm['used_for_routing'] and storm['freshness']=='current'
    assert compare(result)['baseline']['route']==compare(result)['safer']['route']


def test_nhc_old_summary_and_unknown_time_stay_non_authoritative():
    old=parse_nhc_storms(fixture('nhc_storms.json'),fetched_at=NOW+timedelta(hours=7))
    assert old.context[0]['freshness']=='stale' and old.status=='partial'
    payload=fixture('nhc_storms.json')
    del payload['activeStorms'][0]['lastUpdate']
    result=parse_nhc_storms(payload,fetched_at=NOW)
    assert result.context[0]['freshness']=='unknown'


def test_nhc_empty_malformed_duplicates_and_missing_optional_fields():
    assert parse_nhc_storms({'activeStorms':[]},fetched_at=NOW).status=='available'
    assert parse_nhc_storms({},fetched_at=NOW).status=='unavailable'
    payload=fixture('nhc_storms.json')
    payload['activeStorms'].extend([deepcopy(payload['activeStorms'][0]),None,{'id':'minimal'}])
    result=parse_nhc_storms(payload,fetched_at=NOW)
    assert len(result.context)==2 and result.status=='partial'
    minimal=next(c for c in result.context if c['id']=='minimal')
    assert minimal['geometry'] is None and minimal['name'] is None


def test_nhc_client_timeout_and_live_parser(monkeypatch):
    monkeypatch.setattr('survival_geo.hurricane.nhc.utc_now',lambda:NOW)
    session=session_for(fixture('nhc_storms.json'))
    result=NHCClient(session=session).fetch(now=NOW)
    assert result.data_origin=='live' and result.context[0]['freshness']=='current'
    assert session.get.call_args.args[0]==NHC_ENDPOINT
    failed=NHCClient(session=session_for(requests.Timeout())).fetch(now=NOW)
    assert failed.status=='unavailable' and failed.fetched_at is None


def test_nhc_failure_does_not_invalidate_nws(tmp_path):
    nws=Mock()
    nws.fetch.return_value=alerts(data_origin='live')
    nhc=Mock()
    nhc.fetch.side_effect=requests.Timeout()
    service=RefreshService(SnapshotStore(tmp_path/'hazards.json'),COVERAGE,
        adapters=registered_hurricane_adapters(nws_client=nws,nhc_client=nhc))
    state=service.refresh(now=NOW)
    assert state.hazards and state.sources['hurricane_nhc'].status=='unavailable'
    assert state.to_dict()['hazard_coverage_state']=='incomplete'


def test_demo_exact_routes_snapshot_and_zero_network(tmp_path):
    result=run_demo(tmp_path)
    summary=result['summary']
    assert summary['baseline_route']==[1,2,3] and summary['safer_route']==[1,4,3]
    assert summary['baseline_distance_m']==900 and summary['safer_distance_m']==1100
    assert summary['baseline_risk_penalty']==7500 and summary['safer_risk_penalty']==0
    assert summary['baseline_passable'] and summary['affected_edge_count']==summary['avoided_edge_count']==1
    assert len(summary['hurricane_hazards_encountered'])==len(summary['hurricane_hazards_avoided'])==2
    assert summary['offline_route']==[1,4,3] and summary['snapshot_schema']==2
    assert summary['external_network_attempts']==0


def test_unknown_optional_metadata_and_bad_feature_url_do_not_crash():
    payload=fixture()
    payload['features'][0]['id']={'bad':'feature URL'}
    result=hurricane_hazard_batch(parse_tropical_alerts(payload,fetched_at=NOW),now=NOW)
    assert result.status=='partial' and not result.hazards
    source=replace(alerts(),records=(None,alerts().records[0]))
    result=hurricane_hazard_batch(source,now=NOW)
    assert len(result.hazards)==1 and result.status=='partial'


def test_future_test_and_non_tropical_parsed_records_not_routed():
    source=alerts()
    original=source.records[0]
    for record in (replace(original,effective=NOW+timedelta(hours=1)),replace(original,status='Test'),
                   replace(original,event='Flood Warning')):
        assert not hurricane_hazard_batch(replace(source,records=(record,)),now=NOW).hazards


def test_nhc_context_roundtrip_and_offline_staleness(tmp_path):
    source=parse_nhc_storms(fixture('nhc_storms.json'),fetched_at=NOW)
    store=SnapshotStore(tmp_path/'hazards.json')
    store.save(make_hazard_snapshot(COVERAGE,{'nhc':source,'hurricane':batch()},now=NOW))
    service=RefreshService(store,COVERAGE,adapters={})
    state=service.load_offline(now=NOW+timedelta(minutes=16))
    context=state.hazard_batches['nhc'].context[0]
    assert context['data_state']=='CACHED_STALE'
    assert context['intensity']['value']==85 and not context['used_for_routing']


def test_reconnect_hurricane_warning_uses_existing_reevaluation(tmp_path):
    client=Mock()
    client.fetch.return_value=alerts('nws_empty.json',data_origin='live')
    service=RefreshService(SnapshotStore(tmp_path/'hazards.json'),COVERAGE,
        adapters=registered_hurricane_adapters(nws_client=client,include_nhc=False))
    previous=service.refresh(now=NOW)
    graph,origin,destination,_=demo_scenario()
    route=route_with_state(graph,origin,destination,previous)['safer']['route']
    client.fetch.return_value=alerts(data_origin='live')
    current,change=service.refresh_and_reevaluate(graph,route,previous,now=NOW+timedelta(seconds=30))
    assert change['reroute_recommended'] and change['route_still_viable']
    assert change['alternative_route']['route_node_ids']==[1,4,3]
    assert change['newly_encountered_hazards'][0]['hazard_type']=='hurricane'


def test_live_cli_failure_reports_unknown_counts(monkeypatch,capsys):
    from survival_geo.hurricane.live import main
    failed=SourceResult(NWS_SOURCE,NWS_ENDPOINT,'unavailable',(),None,NOW,issues=('Synthetic failure',),data_origin='none')
    monkeypatch.setattr('survival_geo.hurricane.live.TropicalNWSClient.fetch',lambda *a,**k:failed)
    monkeypatch.setattr('survival_geo.hurricane.live.NHCClient.fetch',lambda *a,**k:parse_nhc_storms({},fetched_at=NOW))
    main()
    report=json.loads(capsys.readouterr().out)
    assert report['returned_tropical_alerts'] is None and report['alerts_with_official_geometry'] is None
    assert report['nws_status']=='unavailable'
