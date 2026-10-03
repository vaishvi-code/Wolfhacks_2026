"""OSM road downloads and explicit local GraphML persistence."""
from pathlib import Path

import networkx as nx
import osmnx as ox
from osmnx._errors import InsufficientResponseError
from shapely.geometry import Point, box

from .errors import DataAccessError, EmptyOSMResults, LocationOutsideGraph
from .validation import location, positive


def validate_graph(graph):
    if not isinstance(graph, nx.MultiDiGraph):
        raise ValueError('Expected an OSMnx MultiDiGraph.')
    if not graph.nodes or not graph.edges:
        raise EmptyOSMResults('No usable road network found.')
    if str(graph.graph.get('crs')).lower() != 'epsg:4326':
        raise ValueError('Expected a graph in EPSG:4326 (longitude/latitude).')
    return graph


def check_coverage(graph, latitude, longitude):
    lat, lon = location(latitude, longitude)
    bounds = graph.graph.get('coverage_bounds')
    if bounds is None:
        xs = [data['x'] for _, data in graph.nodes(data=True)]
        ys = [data['y'] for _, data in graph.nodes(data=True)]
        bounds = (min(xs), min(ys), max(xs), max(ys))
    if not box(*bounds).covers(Point(lon, lat)):
        raise LocationOutsideGraph('Location is outside the road graph coverage.')


def load_road_graph(latitude, longitude, radius_m=5000, graph_path=None):
    """Load an existing GraphML file, or download and optionally save roads.

    Existing files are used as-is: there is no refresh or synchronization policy.
    Coverage is a bounding box; routing additionally limits node snapping.
    """
    lat, lon = location(latitude, longitude)
    radius_m = positive(radius_m, 'radius_m')
    path = Path(graph_path) if graph_path is not None else None
    try:
        if path is not None and path.exists():
            graph = ox.load_graphml(path)
            if 'coverage_bounds' in graph.graph:
                import json
                graph.graph['coverage_bounds'] = json.loads(graph.graph['coverage_bounds'])
        else:
            graph = ox.graph_from_point((lat, lon), dist=radius_m, network_type='drive', retain_all=True)
            # OSMnx returns (left, bottom, right, top) in version 2.
            graph.graph['coverage_bounds'] = ox.utils_geo.bbox_from_point((lat, lon), dist=radius_m)
            validate_graph(graph)
            if path is not None:
                import json
                path.parent.mkdir(parents=True, exist_ok=True)
                saved = graph.copy()
                saved.graph['coverage_bounds'] = json.dumps(list(graph.graph['coverage_bounds']))
                ox.save_graphml(saved, path)
    except InsufficientResponseError as exc:
        raise EmptyOSMResults('OSM returned no roads.') from exc
    except (EmptyOSMResults, ValueError):
        raise
    except Exception as exc:
        raise DataAccessError('Unable to download or load the road graph.') from exc
    validate_graph(graph)
    check_coverage(graph, lat, lon)
    return graph


def road_edges(graph):
    """GeoDataFrame indexed by (u, v, key), including osmid, length, geometry."""
    validate_graph(graph)
    return ox.graph_to_gdfs(graph, nodes=False, fill_edge_geometry=True)
