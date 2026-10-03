"""Optional real-data demo: python -m survival_geo.flood.demo --help."""
import argparse
import json
import os

from ..errors import GeospatialError
from ..roads import load_road_graph
from .freshness import utc_now
from .nws import NWSClient
from .routing import compare_flood_routes
from .usgs import USGSClient


def main(argv=None):
    parser = argparse.ArgumentParser(description='Official flood-data and road-routing demo (network optional via GraphML).')
    parser.add_argument('--latitude', type=float, default=35.7796)
    parser.add_argument('--longitude', type=float, default=-78.6382)
    parser.add_argument('--destination-latitude', type=float)
    parser.add_argument('--destination-longitude', type=float)
    parser.add_argument('--radius-m', type=float, default=5000)
    parser.add_argument('--gauge-radius-m', type=float, default=25000)
    parser.add_argument('--area', default='NC', help='NWS state area covering the graph (default NC).')
    parser.add_argument('--graph-path', help='Load an existing GraphML or save the first road download here.')
    parser.add_argument('--user-agent', default=os.environ.get('NWS_USER_AGENT', 'Wolfhacks-NC-Survival/0.1'))
    parser.add_argument('--sources-only', action='store_true', help='Fetch source data without downloading roads.')
    args = parser.parse_args(argv)
    if (args.destination_latitude is None) != (args.destination_longitude is None):
        parser.error('Supply both destination coordinates or neither.')
    origin = (args.latitude, args.longitude)
    alerts = NWSClient(user_agent=args.user_agent).fetch(area=args.area)
    observations = USGSClient(api_key=os.environ.get('USGS_API_KEY')).fetch(
        point=origin, radius_m=args.gauge_radius_m)
    result = {'mode': 'live_official_sources', 'nws': alerts.to_dict(), 'usgs': observations.to_dict()}
    if not args.sources_only:
        try:
            graph = load_road_graph(*origin, radius_m=args.radius_m, graph_path=args.graph_path)
            if args.destination_latitude is not None:
                destination = (args.destination_latitude, args.destination_longitude)
            else:
                # Demonstration endpoint only, not a recommended safe destination.
                node = max(graph.nodes, key=lambda n: (graph.nodes[n]['y'] - origin[0]) ** 2
                           + (graph.nodes[n]['x'] - origin[1]) ** 2)
                destination = (graph.nodes[node]['y'], graph.nodes[node]['x'])
                result['destination_note'] = 'Automatically selected graph node for demonstration, not a safe destination recommendation.'
            result['comparison'] = compare_flood_routes(graph, origin, destination, alerts, observations, now=utc_now())
        except (GeospatialError, ValueError) as exc:
            result['routing_status'] = 'unavailable'
            result['routing_reason'] = str(exc)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
