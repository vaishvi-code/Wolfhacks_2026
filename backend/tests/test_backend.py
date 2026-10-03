import json
from unittest.mock import patch
import geopandas as gpd
import networkx as nx
import pandas as pd
import pytest
from osmnx._errors import InsufficientResponseError
from shapely.geometry import LineString, Point, Polygon
from survival_geo import build_route, discover_destinations, load_road_graph, road_edges
from survival_geo.errors import DataAccessError, EmptyOSMResults, LocationOutsideGraph, NoRouteAvailable

A = (35.77, -78.64)
B = (35.78, -78.62)

@pytest.fixture
def graph():
    g = nx.MultiDiGraph(crs='EPSG:4326')
    for n, x, y in [(1, -78.64, 35.77), (2, -78.63, 35.77), (3, -78.62, 35.78), (4, -78.64, 35.78)]:
        g.add_node(n, x=x, y=y)
    g.add_edge(1, 2, key=0, osmid=100, length=100, geometry=LineString([(-78.63, 35.77), (-78.635, 35.772), (-78.64, 35.77)]))
    g.add_edge(1, 2, key=1, osmid=101, length=150)
    g.add_edge(2, 3, key=0, osmid=102, length=200)
    g.add_edge(1, 3, key=0, osmid=103, length=500)
    return g

def test_route(graph):
    r = build_route(graph, A, B)
    assert r['route_node_ids'] == [1, 2, 3]
    assert r['total_distance_m'] == 300
    assert r['route_edges'][0] == {'u': 1, 'v': 2, 'key': 0}
    assert r['route_geometry']['coordinates'][0] == (-78.64, 35.77)
    assert r['route_geometry']['coordinates'][1] == (-78.635, 35.772)
    assert r['route_geometry']['coordinates'][-1] == (-78.62, 35.78)
    assert r['origin']['latitude'] == A[0]
    json.dumps(r, allow_nan=False)

def test_policy_parallel_edges(graph):
    r = build_route(graph, A, B, edge_cost=lambda u,v,k,d: None if d['osmid'] == 100 else d['length'])
    assert r['route_edges'][0]['key'] == 1
    assert r['total_distance_m'] == 350
    assert graph.number_of_edges() == 4

def test_policy_detour(graph):
    r = build_route(graph, A, B, edge_cost=lambda u,v,k,d: None if v == 2 else d['length'])
    assert r['route_node_ids'] == [1, 3]

@pytest.mark.parametrize('cost', [-1, float('nan'), float('inf')])
def test_invalid_cost(graph, cost):
    with pytest.raises(ValueError):
        build_route(graph, A, B, edge_cost=lambda *args: cost)

def test_no_routes(graph):
    for origin, destination in [(B,A), (A,(35.78,-78.64))]:
        with pytest.raises(NoRouteAvailable):
            build_route(graph, origin, destination)
    with pytest.raises(NoRouteAvailable):
        build_route(graph, A, B, edge_cost=lambda *args: None)

def test_same_node(graph):
    r = build_route(graph, A, A)
    assert r['total_distance_m'] == 0
    assert r['route_node_ids'] == [1]
    assert len(r['route_geometry']['coordinates']) == 2

def test_coverage_and_snapping(graph):
    with pytest.raises(LocationOutsideGraph):
        build_route(graph, (36,-79), B)
    with pytest.raises(LocationOutsideGraph):
        build_route(graph, (35.775,-78.635), B, max_snap_distance_m=10)

@pytest.mark.parametrize('origin', [(91,0), (0,181), (float('nan'),0)])
def test_invalid_location(graph, origin):
    with pytest.raises(ValueError):
        build_route(graph, origin, B)

def test_edges(graph):
    edges = road_edges(graph)
    assert isinstance(edges, gpd.GeoDataFrame)
    assert edges.index.names == ['u','v','key']
    assert {'osmid','length','geometry'} <= set(edges.columns)
    assert edges.loc[(1,2,1), 'geometry'].geom_type == 'LineString'

def test_graphml_roundtrip(graph, tmp_path):
    path = tmp_path / 'roads.graphml'
    with patch('survival_geo.roads.ox.graph_from_point', return_value=graph) as download:
        loaded = load_road_graph(*A, graph_path=path)
        assert download.call_args.kwargs['network_type'] == 'drive'
        assert download.call_args.kwargs['retain_all'] is True
    with patch('survival_geo.roads.ox.graph_from_point', side_effect=AssertionError('No download')):
        saved = load_road_graph(*A, graph_path=path)
    assert saved.graph['coverage_bounds'] == list(loaded.graph['coverage_bounds'])
    assert build_route(saved, A, B)['total_distance_m'] == 300
    with pytest.raises(LocationOutsideGraph):
        load_road_graph(40,-80,graph_path=path)

@pytest.mark.parametrize('failure,expected', [(RuntimeError('network'),DataAccessError), (InsufficientResponseError('empty'),EmptyOSMResults)])
def test_download_errors(failure, expected):
    for target, function in [('survival_geo.roads.ox.graph_from_point',load_road_graph), ('survival_geo.destinations.ox.features_from_point',discover_destinations)]:
        with patch(target, side_effect=failure), pytest.raises(expected):
            function(*A)

def test_empty_graph_and_corrupt_file(tmp_path):
    with patch('survival_geo.roads.ox.graph_from_point', return_value=nx.MultiDiGraph()), pytest.raises(EmptyOSMResults):
        load_road_graph(*A)
    path = tmp_path / 'bad.graphml'
    path.write_text('not xml')
    with pytest.raises(DataAccessError):
        load_road_graph(*A,graph_path=path)

def test_destinations():
    polygon = Polygon([(-78.64,35.77),(-78.63,35.77),(-78.63,35.78),(-78.64,35.78)])
    frame = gpd.GeoDataFrame({'amenity':['hospital','library'],'name':['Hospital',None]}, geometry=[polygon,Point(-78.63,35.77)], crs='EPSG:4326', index=pd.MultiIndex.from_tuples([('way',100),('node',200)]))
    with patch('survival_geo.destinations.ox.features_from_point', return_value=frame) as request:
        results = discover_destinations(*A,categories={'medical':{'amenity':'hospital'},'public':{'amenity':['hospital','library']}})
    assert set(request.call_args.kwargs['tags']['amenity']) == {'hospital','library'}
    assert results[0]['categories'] == ['medical','public']
    assert polygon.covers(Point(results[0]['longitude'],results[0]['latitude']))
    assert results[1]['name'] is None
    json.dumps(results,allow_nan=False)

def test_empty_destinations():
    with patch('survival_geo.destinations.ox.features_from_point', return_value=gpd.GeoDataFrame()), pytest.raises(EmptyOSMResults):
        discover_destinations(*A)

def test_invalid_options():
    with pytest.raises(ValueError):
        load_road_graph(*A,radius_m=-1)
    with pytest.raises(ValueError):
        discover_destinations(*A,categories={})

def test_policy_cost_can_change_route_without_changing_distance(graph):
    def policy(u, v, key, attributes):
        return attributes['length'] + (1000 if v == 2 else 0)
    route = build_route(graph, A, B, edge_cost=policy)
    assert route['route_node_ids'] == [1, 3]
    assert route['total_distance_m'] == 500


def test_projected_graph_rejected(graph):
    graph.graph['crs'] = 'EPSG:32617'
    with pytest.raises(ValueError, match='EPSG:4326'):
        build_route(graph, A, B)
