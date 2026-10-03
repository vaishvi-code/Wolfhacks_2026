"""Risk-aware adapters around the existing routing engine."""
import math

from .errors import NoRouteAvailable
from .risk import RiskLevel, evaluate_road_risks
from .roads import road_edges
from .routing import build_route, distance_cost


def risk_edge_cost(risks, base_travel_cost=distance_cost):
    """Build a cost hook; missing evaluations raise instead of assuming safety."""
    def cost(u, v, key, attributes):
        edge_id = (u, v, key)
        if edge_id not in risks:
            raise ValueError(f'Missing road-risk evaluation for edge {edge_id}.')
        risk = risks[edge_id]
        if risk.edge_id != edge_id:
            raise ValueError(f'Road-risk identifier mismatch for edge {edge_id}.')
        if not risk.passable:
            return None
        base = base_travel_cost(u, v, key, attributes)
        if base is None:
            return None
        base = float(base)
        if not math.isfinite(base) or base < 0:
            raise ValueError('Base travel costs must be finite and nonnegative, or None.')
        return base + risk.penalty
    return cost


def route_risk_summary(route, risks):
    """Annotate either routing mode using exactly the selected parallel edges."""
    selected = [risks[(edge['u'], edge['v'], edge['key'])] for edge in route['route_edges']]
    hazard_penalty = sum(r.hazard_penalty for r in selected)
    uncertainty_penalty = sum(r.uncertainty_penalty for r in selected)
    return {**route, 'total_hazard_penalty': hazard_penalty,
            'total_uncertainty_penalty': uncertainty_penalty,
            'total_risk_penalty': hazard_penalty + uncertainty_penalty,
            'passable': all(r.passable for r in selected),
            'hazard_ids': sorted({h for r in selected for h in r.hazard_ids}),
            'road_risks': [r.to_dict() for r in selected]}


def build_risk_route(graph, origin, destination, risks, *,
                     base_travel_cost=distance_cost, max_snap_distance_m=1000):
    """Use a complete evaluation for this graph snapshot; no graph mutation."""
    route = build_route(graph, origin, destination,
                        edge_cost=risk_edge_cost(risks, base_travel_cost),
                        max_snap_distance_m=max_snap_distance_m)
    return route_risk_summary(route, risks)


def compare_routes(graph, origin, destination, hazards, policy=None, *,
                   max_snap_distance_m=1000, evaluated_at=None):
    """Compare distance-only and risk-aware paths under one hazard snapshot.

    NoRouteAvailable becomes exists=False. Invalid inputs/coverage errors still
    propagate. Avoidance is relative to the normal route, never claimed when
    either route is absent. Costs here use distance as the base in both modes.
    """
    risks = evaluate_road_risks(road_edges(graph), hazards, policy, evaluated_at)
    results = {}
    for mode in ('distance', 'risk_aware'):
        try:
            if mode == 'distance':
                route = route_risk_summary(build_route(
                    graph, origin, destination, max_snap_distance_m=max_snap_distance_m), risks)
            else:
                route = build_risk_route(graph, origin, destination, risks,
                                         max_snap_distance_m=max_snap_distance_m)
            results[mode] = {'exists': True, 'route': route, 'reason': None}
        except NoRouteAvailable as exc:
            results[mode] = {'exists': False, 'route': None, 'reason': str(exc)}
    normal = results['distance']['route']
    safer = results['risk_aware']['route']
    results['avoided_roads'] = None
    results['avoided_hazard_ids'] = None
    if normal is not None and safer is not None:
        used = {(e['u'], e['v'], e['key']) for e in safer['route_edges']}
        results['avoided_roads'] = [r for r in normal['road_risks']
                                    if tuple(r['edge_id'][k] for k in ('u', 'v', 'key')) not in used
                                    and r['risk_level'] != RiskLevel.SAFE.value]
        results['avoided_hazard_ids'] = sorted(set(normal['hazard_ids']) - set(safer['hazard_ids']))
    results['road_risks'] = [risk.to_dict() for risk in risks.values()]
    return results
