"""Run with python -m survival_geo.demo. Entirely synthetic and offline."""
import json

import networkx as nx
from shapely.geometry import box

from .hazards import Hazard
from .risk_routing import compare_routes


def demo_scenario():
    graph = nx.MultiDiGraph(crs='EPSG:4326')
    coordinates = {1: (-78.640, 35.770), 2: (-78.635, 35.770),
                   3: (-78.630, 35.770), 4: (-78.635, 35.773)}
    for node, (lon, lat) in coordinates.items():
        graph.add_node(node, x=lon, y=lat)
    # Deliberately assigned demo distances, not surveyed road measurements.
    for u, v, length in [(1, 2, 450), (2, 3, 450), (1, 4, 550), (4, 3, 550)]:
        graph.add_edge(u, v, key=0, length=length, osmid=u * 10 + v)
    hazards = [Hazard(id='demo-closure', hazard_type='road_closure',
                      geometry=box(-78.637, 35.7698, -78.633, 35.7702),
                      severity='critical', source='synthetic demo', confidence=1.0)]
    return graph, (35.770, -78.640), (35.770, -78.630), hazards


def main():
    graph, origin, destination, hazards = demo_scenario()
    result = compare_routes(graph, origin, destination, hazards)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
