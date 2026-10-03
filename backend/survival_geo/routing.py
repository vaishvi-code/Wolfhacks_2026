"""Directed routing with replaceable per-edge costs and edge exclusion."""
import math

import networkx as nx
import osmnx as ox
from shapely.geometry import LineString, mapping

from .errors import LocationOutsideGraph, NoRouteAvailable
from .roads import check_coverage, validate_graph
from .validation import location, positive


def distance_cost(u, v, key, attributes):
    """Default cost in meters. Future policies may return None to exclude an edge."""
    return attributes['length']


def build_route(graph, origin, destination, edge_cost=distance_cost, max_snap_distance_m=1000):
    """Route between (latitude, longitude) pairs, returning JSON-compatible data.

    Costs must be finite and nonnegative, or None for an impassable edge.
    Policies receive (u, v, key, attributes). The input graph is never modified.
    Distance describes road travel between snapped nodes, excluding connectors.
    """
    validate_graph(graph)
    origin = location(*origin)
    destination = location(*destination)
    max_snap_distance_m = positive(max_snap_distance_m, 'max_snap_distance_m')
    snapped = []
    for lat, lon in (origin, destination):
        check_coverage(graph, lat, lon)
        node, distance = ox.distance.nearest_nodes(graph, X=lon, Y=lat, return_dist=True)
        if distance > max_snap_distance_m:
            raise LocationOutsideGraph('Location is too far from a road node.')
        snapped.append(int(node))
    # Collapse parallel edges by policy cost, retaining the exact selected edge.
    working = nx.DiGraph()
    working.add_nodes_from(graph.nodes)
    for u, v, key, attributes in graph.edges(keys=True, data=True):
        length = float(attributes['length'])
        if not math.isfinite(length) or length < 0:
            raise ValueError('Road lengths must be finite and nonnegative.')
        cost = edge_cost(u, v, key, attributes)
        if cost is None:
            continue
        cost = float(cost)
        if not math.isfinite(cost) or cost < 0:
            raise ValueError('Edge costs must be finite and nonnegative, or None.')
        if not working.has_edge(u, v) or cost < working[u][v]['cost']:
            working.add_edge(u, v, cost=cost, key=key)
    try:
        nodes = nx.shortest_path(working, *snapped, weight='cost', method='dijkstra')
    except nx.NetworkXNoPath as exc:
        raise NoRouteAvailable('No directed road route connects these locations.') from exc
    coordinates, edges, distance = [], [], 0.0
    for u, v in zip(nodes, nodes[1:]):
        key = working[u][v]['key']
        attributes = graph[u][v][key]
        start = (graph.nodes[u]['x'], graph.nodes[u]['y'])
        end = (graph.nodes[v]['x'], graph.nodes[v]['y'])
        geometry = attributes.get('geometry', LineString([start, end]))
        segment = list(geometry.coords)
        # GraphML/custom graphs can store geometry in reverse travel order.
        if math.dist(segment[-1], start) < math.dist(segment[0], start):
            segment.reverse()
        coordinates.extend(segment if not coordinates else segment[1:])
        distance += float(attributes['length'])
        edges.append({'u': int(u), 'v': int(v), 'key': int(key)})
    if not coordinates:
        data = graph.nodes[snapped[0]]
        coordinates = [(data['x'], data['y'])] * 2
    def endpoint(requested, node):
        return {'latitude': requested[0], 'longitude': requested[1],
                'snapped_node_id': node,
                'snapped_location': {'latitude': graph.nodes[node]['y'], 'longitude': graph.nodes[node]['x']}}
    return {'route_node_ids': [int(node) for node in nodes], 'route_edges': edges,
            'route_geometry': mapping(LineString(coordinates)), 'total_distance_m': distance,
            'origin': endpoint(origin, snapped[0]), 'destination': endpoint(destination, snapped[1])}
