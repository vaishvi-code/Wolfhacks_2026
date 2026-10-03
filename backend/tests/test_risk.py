"""Synthetic integration and policy tests; all OSM downloads are forbidden."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json

import geopandas as gpd
import networkx as nx
import pandas as pd
import pytest
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box, mapping

from survival_geo import (
    Hazard, RiskLevel, RiskPolicy, build_risk_route, build_route, compare_routes,
    evaluate_road_risks, filter_destinations, risk_edge_cost, road_edges,
)
from survival_geo.demo import demo_scenario
from survival_geo.errors import LocationOutsideGraph, NoRouteAvailable


NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_downloads(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Risk tests must not download OSM data.')
    monkeypatch.setattr('osmnx.graph_from_point', forbidden)
    monkeypatch.setattr('osmnx.features_from_point', forbidden)


@pytest.fixture
def scenario():
    return demo_scenario()


def make_hazard(**changes):
    values = dict(id='test', hazard_type='flood',
                  geometry=box(-78.637, 35.7698, -78.633, 35.7702),
                  severity='high', source='synthetic', confidence=0.8,
                  timestamp=NOW, metadata={'note': 'not a real event'})
    return Hazard(**{**values, **changes})


@pytest.mark.parametrize('geometry', [
    Point(-78.636, 35.770),
    LineString([(-78.636, 35.769), (-78.636, 35.7701)]),
    box(-78.637, 35.7698, -78.633, 35.7702),
    MultiPolygon([box(-78.637, 35.7698, -78.636, 35.7702),
                  box(-78.634, 35.7698, -78.633, 35.7702)]),
])
def test_supported_geometry_intersections(scenario, geometry):
    graph, _, _, _ = scenario
    risks = evaluate_road_risks(road_edges(graph), [make_hazard(geometry=geometry)])
    assert risks[1, 2, 0].risk_level == RiskLevel.HIGH_RISK
    assert risks[1, 2, 0].hazard_ids == ('test',)
    assert risks[1, 4, 0].risk_level == RiskLevel.SAFE
    assert risks[1, 4, 0].penalty == 0
    assert risks[1, 4, 0].confidence is None


def test_boundary_touch_counts(scenario):
    edges = road_edges(scenario[0])
    risks = evaluate_road_risks(edges, [make_hazard(geometry=Point(-78.640, 35.770))])
    assert risks[1, 2, 0].hazard_ids == ('test',)
    assert risks[1, 4, 0].hazard_ids == ('test',)


@pytest.mark.parametrize('severity,level,penalty,passable', [
    ('none', RiskLevel.SAFE, 0, True),
    ('low', RiskLevel.CAUTION, 250, True),
    ('moderate', RiskLevel.CAUTION, 250, True),
    ('high', RiskLevel.HIGH_RISK, 2500, True),
    ('critical', RiskLevel.IMPASSABLE, 0, False),
])
def test_default_assignment(scenario, severity, level, penalty, passable):
    risk = evaluate_road_risks(road_edges(scenario[0]), [make_hazard(severity=severity)])[1, 2, 0]
    assert (risk.risk_level, risk.penalty, risk.passable) == (level, penalty, passable)


def test_multiple_hazards_aggregate_and_preserve_provenance(scenario):
    hazards = [make_hazard(id='flood', severity='moderate'),
               make_hazard(id='heat', hazard_type='heat', confidence=0.4)]
    risk = evaluate_road_risks(road_edges(scenario[0]), hazards,
                              RiskPolicy(uncertainty_penalty_scale=100), NOW)[1, 2, 0]
    assert risk.risk_level == RiskLevel.HIGH_RISK
    assert risk.hazard_penalty == 2750
    assert risk.uncertainty_penalty == pytest.approx(80)
    assert risk.penalty == pytest.approx(2830)
    assert risk.confidence == 0.4
    assert risk.hazard_ids == ('flood', 'heat')
    assert len(risk.reasons) == 2
    assert risk.to_dict()['evaluated_at'] == NOW.isoformat()
    json.dumps(risk.to_dict(), allow_nan=False)


def test_impassable_dominates_low_confidence_and_other_hazards(scenario):
    hazards = [make_hazard(), make_hazard(id='closure', severity='critical', confidence=0)]
    risk = evaluate_road_risks(road_edges(scenario[0]), hazards)[1, 2, 0]
    assert risk.risk_level == RiskLevel.IMPASSABLE
    assert not risk.passable


def test_projected_edges_reproject_hazards(scenario):
    edges = road_edges(scenario[0])
    geographic = evaluate_road_risks(edges, [make_hazard()])
    projected = evaluate_road_risks(edges.to_crs('EPSG:32617'), [make_hazard()])
    assert {k: v.hazard_ids for k, v in geographic.items()} == {k: v.hazard_ids for k, v in projected.items()}


def test_empty_hazards_and_empty_roads(scenario):
    edges = road_edges(scenario[0])
    risks = evaluate_road_risks(edges, [])
    assert len(risks) == 4
    assert all(r.risk_level == RiskLevel.SAFE and r.passable and r.penalty == 0 for r in risks.values())
    assert evaluate_road_risks(edges.iloc[:0], [make_hazard()]) == {}


def test_inputs_not_mutated(scenario):
    graph, origin, destination, hazards = scenario
    original = deepcopy(graph)
    edges = road_edges(graph)
    original_edges = edges.copy(deep=True)
    evaluate_road_risks(edges, hazards)
    compare_routes(graph, origin, destination, hazards)
    assert nx.utils.graphs_equal(graph, original)
    pd.testing.assert_frame_equal(edges, original_edges)


@pytest.mark.parametrize('hazards', [None, {}, 'bad', [None], [{'id': 'bad'}], 42])
def test_invalid_hazard_collections(scenario, hazards):
    with pytest.raises(ValueError):
        evaluate_road_risks(road_edges(scenario[0]), hazards)


def test_duplicate_and_unknown_severity_rejected(scenario):
    for hazards in [[make_hazard(), make_hazard()], [make_hazard(severity='unmapped')]]:
        with pytest.raises(ValueError):
            evaluate_road_risks(road_edges(scenario[0]), hazards)


@pytest.mark.parametrize('changes', [
    {'id': ''}, {'hazard_type': ''}, {'source': ''}, {'severity': ''},
    {'confidence': -0.1}, {'confidence': 1.1}, {'confidence': float('nan')},
    {'confidence': True}, {'geometry': None}, {'geometry': Point()},
    {'geometry': Point(200, 0)}, {'geometry': Point(0, 0, 1)},
    {'geometry': Polygon([(0, 0), (1, 1), (0, 1), (1, 0), (0, 0)])},
    {'timestamp': datetime(2026, 1, 1)}, {'timestamp': '2026-01-01'},
    {'metadata': {'invalid': float('nan')}}, {'metadata': []},
])
def test_invalid_hazard_fields(changes):
    with pytest.raises(ValueError):
        make_hazard(**changes)


def test_hazard_serialization_and_metadata_snapshot():
    metadata = {'measurements': [1, 2]}
    hazard = make_hazard(hazard_type='future_type', timestamp=None, metadata=metadata)
    metadata['measurements'].append(3)
    assert hazard.metadata == {'measurements': [1, 2]}
    assert hazard.to_dict()['timestamp'] is None
    assert hazard.to_dict()['geometry']['type'] == 'Polygon'
    json.dumps(hazard.to_dict(), allow_nan=False)


@pytest.mark.parametrize('changes', [
    {'uncertainty_penalty_scale': -1}, {'uncertainty_penalty_scale': float('nan')},
    {'severity_levels': {}}, {'severity_levels': {'high': 'INVALID'}},
    {'penalties': {RiskLevel.SAFE: 0}},
    {'penalties': {**RiskPolicy().penalties, RiskLevel.SAFE: 1}},
    {'penalties': {**RiskPolicy().penalties, RiskLevel.CAUTION: -1}},
    {'penalties': {**RiskPolicy().penalties, RiskLevel.HIGH_RISK: float('inf')}},
    {'penalties': {**RiskPolicy().penalties, RiskLevel.HIGH_RISK: 1}},
])
def test_invalid_policies(changes):
    with pytest.raises(ValueError):
        RiskPolicy(**changes)


def test_policy_extension_and_mapping_override(scenario):
    policy = RiskPolicy(severity_levels={'custom': RiskLevel.CAUTION})
    risk = evaluate_road_risks(road_edges(scenario[0]), [make_hazard(severity='custom')], policy)[1, 2, 0]
    assert risk.risk_level == RiskLevel.CAUTION

    class ClosurePolicy(RiskPolicy):
        def level_for(self, hazard):
            return RiskLevel.IMPASSABLE if hazard.hazard_type == 'road_closure' else super().level_for(hazard)

    risk = evaluate_road_risks(road_edges(scenario[0]),
                              [make_hazard(hazard_type='road_closure', severity='low')], ClosurePolicy())[1, 2, 0]
    assert not risk.passable


def test_invalid_edge_frame(scenario):
    edges = road_edges(scenario[0])
    with pytest.raises(ValueError):
        evaluate_road_risks(edges.reset_index(), [])
    with pytest.raises(ValueError):
        evaluate_road_risks(pd.concat([edges, edges]), [])
    with pytest.raises(ValueError):
        evaluate_road_risks(gpd.GeoDataFrame(geometry=list(edges.geometry), index=edges.index), [])
    edges.loc[(1, 2, 0), 'geometry'] = None
    with pytest.raises(ValueError):
        evaluate_road_risks(edges, [])


def test_demo_comparison(scenario):
    graph, origin, destination, hazards = scenario
    result = compare_routes(graph, origin, destination, hazards, evaluated_at=NOW)
    normal = result['distance']['route']
    safer = result['risk_aware']['route']
    assert result['distance']['exists'] and result['risk_aware']['exists']
    assert normal['route_node_ids'] == [1, 2, 3]
    assert normal['total_distance_m'] == 900
    assert normal['passable'] is False
    assert safer['route_node_ids'] == [1, 4, 3]
    assert safer['total_distance_m'] == 1100
    assert safer['passable'] is True
    assert safer['total_risk_penalty'] == 0
    assert len(result['avoided_roads']) == 2
    assert result['avoided_hazard_ids'] == ['demo-closure']
    assert safer['route_geometry']['type'] == 'LineString'
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('penalty,expected_nodes', [(10, [1, 2, 3]), (200, [1, 4, 3])])
def test_configurable_caution_penalty(scenario, penalty, expected_nodes):
    graph, origin, destination, _ = scenario
    policy = RiskPolicy(penalties={**RiskPolicy().penalties, RiskLevel.CAUTION: penalty})
    result = compare_routes(graph, origin, destination, [make_hazard(severity='low')], policy)
    assert result['risk_aware']['route']['route_node_ids'] == expected_nodes
    assert result['distance']['route']['total_risk_penalty'] == 2 * penalty


def test_high_risk_is_penalized_but_still_usable(scenario):
    graph, origin, destination, _ = scenario
    result = compare_routes(graph, origin, destination, [make_hazard()])
    assert result['risk_aware']['route']['route_node_ids'] == [1, 4, 3]
    graph.remove_edge(1, 4, 0)
    result = compare_routes(graph, origin, destination, [make_hazard()])
    assert result['risk_aware']['exists']
    assert result['risk_aware']['route']['total_hazard_penalty'] == 5000


def test_uncertainty_cost_is_separate_and_configurable(scenario):
    graph, origin, destination, _ = scenario
    policy = RiskPolicy(penalties={**RiskPolicy().penalties, RiskLevel.CAUTION: 0}, uncertainty_penalty_scale=1000)
    result = compare_routes(graph, origin, destination, [make_hazard(severity='low')], policy)
    assert result['distance']['route']['total_hazard_penalty'] == 0
    assert result['distance']['route']['total_uncertainty_penalty'] == pytest.approx(400)
    assert result['risk_aware']['route']['route_node_ids'] == [1, 4, 3]
    safe = compare_routes(graph, origin, destination, [make_hazard(severity='none', confidence=0)], policy)
    assert safe['risk_aware']['route']['total_risk_penalty'] == 0


def test_no_passable_route_and_no_baseline(scenario):
    graph, origin, destination, hazards = scenario
    graph.remove_edge(1, 4, 0)
    result = compare_routes(graph, origin, destination, hazards)
    assert result['distance']['exists']
    assert result['risk_aware'] == {'exists': False, 'route': None, 'reason': 'No directed road route connects these locations.'}
    assert result['avoided_roads'] is None and result['avoided_hazard_ids'] is None
    risks = evaluate_road_risks(road_edges(graph), hazards)
    with pytest.raises(NoRouteAvailable):
        build_risk_route(graph, origin, destination, risks)
    reverse = compare_routes(graph, destination, origin, hazards)
    assert not reverse['distance']['exists'] and not reverse['risk_aware']['exists']


def test_no_hazards_and_same_node_comparison(scenario):
    graph, origin, destination, _ = scenario
    result = compare_routes(graph, origin, destination, iter([]))
    assert result['distance']['route'] == result['risk_aware']['route']
    assert result['avoided_roads'] == [] and result['avoided_hazard_ids'] == []
    result = compare_routes(graph, origin, origin, [])
    assert result['risk_aware']['route']['total_risk_penalty'] == 0


def test_comparison_preserves_input_errors(scenario):
    with pytest.raises(LocationOutsideGraph):
        compare_routes(scenario[0], (0, 0), scenario[2], [])


def test_parallel_edges_use_exact_risk_and_geometry(scenario):
    graph, origin, destination, hazards = scenario
    # A longer parallel road curves around the hazard footprint.
    graph.add_edge(1, 2, key=7, length=600, osmid=999,
                   geometry=LineString([(-78.640, 35.770), (-78.638, 35.771),
                                        (-78.635, 35.771), (-78.635, 35.770)]))
    # A point affects only the straight first edge, not the parallel curve.
    hazards = [make_hazard(geometry=Point(-78.636, 35.770), severity='critical')]
    result = compare_routes(graph, origin, destination, hazards)
    safer = result['risk_aware']['route']
    assert safer['route_edges'][0]['key'] == 7
    assert safer['total_distance_m'] == 1050
    assert (-78.638, 35.771) in safer['route_geometry']['coordinates']
    assert result['avoided_roads'][0]['edge_id'] == {'u': 1, 'v': 2, 'key': 0}


def test_cost_hook_rejects_missing_risks_and_invalid_base(scenario):
    graph, origin, destination, _ = scenario
    with pytest.raises(ValueError, match='Missing'):
        build_risk_route(graph, origin, destination, {})
    risks = evaluate_road_risks(road_edges(graph), [make_hazard(severity='low')])
    with pytest.raises(ValueError, match='Base travel'):
        build_risk_route(graph, origin, destination, risks, base_travel_cost=lambda *args: -1)
    assert risk_edge_cost(risks, lambda *args: None)(1, 2, 0, graph[1][2][0]) is None
    risks[1, 2, 0] = risks[1, 4, 0]
    with pytest.raises(ValueError, match='identifier mismatch'):
        build_risk_route(graph, origin, destination, risks)


def test_invalid_road_risk_rejected(scenario):
    risk = evaluate_road_risks(road_edges(scenario[0]), [make_hazard()])[1, 2, 0]
    for changes in ({'passable': False}, {'hazard_penalty': -1}, {'confidence': float('nan')},
                    {'evaluated_at': None}, {'risk_level': RiskLevel.SAFE}):
        with pytest.raises(ValueError):
            replace(risk, **changes)


def test_destination_filter_footprints_and_explanations():
    candidates = [
        {'osm_id': 1, 'latitude': 35.770, 'longitude': -78.635},
        {'osm_id': 2, 'geometry': mapping(box(-78.638, 35.7702, -78.637, 35.771))},
        {'osm_id': 3, 'latitude': 35.773, 'longitude': -78.635},
    ]
    original = deepcopy(candidates)
    hazards = [make_hazard(), make_hazard(id='closure', severity='critical')]
    result = filter_destinations(candidates, hazards, evaluated_at=NOW)
    assert [d['osm_id'] for d in result['accepted']] == [3]
    assert [d['destination']['osm_id'] for d in result['excluded']] == [1, 2]
    assert result['excluded'][0]['hazard_ids'] == ['test', 'closure']
    assert len(result['excluded'][0]['reasons']) == 2
    assert result['excluded'][0]['evaluated_at'] == NOW.isoformat()
    assert candidates == original
    json.dumps(result, allow_nan=False)


def test_destination_policy_and_empty_inputs():
    candidates = [{'latitude': 35.770, 'longitude': -78.635}]
    hazards = [make_hazard(severity='low')]
    assert filter_destinations(candidates, hazards)['accepted'] == candidates
    assert not filter_destinations(candidates, hazards, unsafe_levels={RiskLevel.CAUTION})['accepted']
    assert filter_destinations(candidates, []) == {'accepted': candidates, 'excluded': []}
    assert filter_destinations([], hazards) == {'accepted': [], 'excluded': []}


@pytest.mark.parametrize('candidate', [{}, {'geometry': None}, {'geometry': Point()},
                                       {'latitude': 91, 'longitude': 0}, 'bad'])
def test_invalid_destinations(candidate):
    with pytest.raises(ValueError):
        filter_destinations([candidate], [])
