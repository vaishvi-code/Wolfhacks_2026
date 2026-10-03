"""Local routing and deterministic route reevaluation via the existing engine."""
from shapely.geometry import box

from ..destination_safety import filter_destinations
from ..errors import NoRouteAvailable
from ..flood.adapter import FloodPolicy
from ..flood.freshness import Freshness, freshness, iso
from ..flood.routing import compare_flood_routes
from ..risk import LEVEL_ORDER, RiskLevel, evaluate_road_risks
from ..risk_routing import build_risk_route
from ..roads import load_road_graph, road_edges
from .snapshot import CacheError


def _check_coverage(graph, state):
    edges = road_edges(graph)
    if not box(*state.coverage.bbox).covers(box(*edges.total_bounds)):
        raise CacheError('COVERAGE_MISMATCH', 'Road graph extends outside configured snapshot coverage.')
    return edges


def route_with_state(graph, origin, destination, state, *, candidates=(), policy=None,
                     max_snap_distance_m=1000):
    """All inputs are local. Discovery/network/actual GPS hardware are not invoked."""
    _check_coverage(graph, state)
    policy = FloodPolicy() if policy is None else policy
    result = compare_flood_routes(
        graph, origin, destination, state.sources['nws'], state.sources['usgs'],
        policy=policy, now=state.evaluated_at, alert_freshness=state.alert_freshness,
        observation_freshness=state.observation_freshness, include_stale=state.include_stale,
        max_snap_distance_m=max_snap_distance_m)
    result['destinations'] = filter_destinations(candidates, state.hazards, policy,
                                                  evaluated_at=state.evaluated_at)
    result['data_state'] = state.to_dict()
    return result


def route_offline(service, graph_path, origin, destination, *, candidates=(), now=None, **kwargs):
    """Strictly disk-only: even a missing GraphML cannot trigger a download."""
    state = service.load_offline(now=now)
    graph = load_road_graph(*origin, graph_path=graph_path, allow_download=False)
    return route_with_state(graph, origin, destination, state, candidates=candidates, **kwargs)


def _edge_ids(route):
    try:
        nodes, edges = route['route_node_ids'], route['route_edges']
        ids = [(edge['u'], edge['v'], edge['key']) for edge in edges]
        if len(nodes) != len(ids) + 1 or any((u, v) != pair for (u, v, _), pair in zip(ids, zip(nodes, nodes[1:]))):
            raise ValueError('Route nodes and edges disagree.')
        return ids
    except (KeyError, TypeError) as exc:
        raise ValueError('Expected an existing backend route result.') from exc


def _summary(route, ids, risks):
    selected = [risks[edge] for edge in ids if edge in risks]
    missing = [dict(zip(('u', 'v', 'key'), edge)) for edge in ids if edge not in risks]
    return {'route': route, 'total_distance_m': route['total_distance_m'],
            'total_risk_penalty': sum(r.penalty for r in selected),
            'road_risks': [r.to_dict() for r in selected], 'missing_edges': missing,
            'hazard_ids': sorted({id for r in selected for id in r.hazard_ids}),
            'passable': not missing and all(r.passable for r in selected)}


def reevaluate_route(graph, route, previous_state, current_state, *, policy=None,
                     recalculate=True, current_position=None, max_snap_distance_m=1000):
    """Assess the supplied route (caller trims traversed edges if appropriate).

    Viability means graph/policy passability, never verified physical safety.
    Missing/aging evidence is distinguished from a confirmed changed snapshot.
    """
    if previous_state.coverage != current_state.coverage:
        raise CacheError('COVERAGE_MISMATCH', 'Cannot compare different snapshot regions.')
    edges = _check_coverage(graph, current_state)
    ids = _edge_ids(route)
    policy = FloodPolicy() if policy is None else policy
    old = evaluate_road_risks(edges, previous_state.hazards, policy, previous_state.evaluated_at)
    new = evaluate_road_risks(edges, current_state.hazards, policy, current_state.evaluated_at)
    old_summary, new_summary = _summary(route, ids, old), _summary(route, ids, new)
    newly_affected, triggers = [], []
    for edge in ids:
        if edge not in new:
            continue
        before, after = old[edge], new[edge]
        changed = (LEVEL_ORDER[after.risk_level] > LEVEL_ORDER[before.risk_level]
                   or after.penalty > before.penalty
                   or bool(set(after.hazard_ids) - set(before.hazard_ids)))
        if changed and after.risk_level != RiskLevel.SAFE:
            newly_affected.append(after.to_dict())
            if after.risk_level in (RiskLevel.HIGH_RISK, RiskLevel.IMPASSABLE):
                triggers.append('NEW_HIGH_RISK_CONDITION')
    if new_summary['missing_edges']:
        triggers.append('ROUTE_EDGE_MISSING')
    if not new_summary['passable'] and not new_summary['missing_edges']:
        triggers.append('IMPASSABLE_EDGE')
    old_ids, new_ids = set(old_summary['hazard_ids']), set(new_summary['hazard_ids'])
    removed, unknown = [], []
    alerts = {f'nws:{r.id}': r for r in current_state.sources['nws'].records}
    previous_alerts = {f'nws:{r.id}': r for r in previous_state.sources['nws'].records}
    source = current_state.sources['nws']
    authoritative_refresh = (source.status == 'available' and source.data_origin == 'live'
                             and freshness(observed_at=source.fetched_at, fetched_at=source.fetched_at,
                                           now=current_state.evaluated_at,
                                           policy=current_state.alert_freshness) == Freshness.CURRENT)
    mapped_current_ids = {h.id for h in current_state.hazards}
    for hazard_id in sorted(old_ids - new_ids):
        record = alerts.get(hazard_id, previous_alerts.get(hazard_id))
        if record and record.expires_at is not None and current_state.evaluated_at >= record.expires_at:
            removed.append({'hazard_id': hazard_id, 'reason_code': 'ALERT_EXPIRED'})
        elif authoritative_refresh and (
                hazard_id not in alerts or hazard_id in mapped_current_ids
                or (record and record.status == 'Actual' and record.message_type == 'Cancel')):
            removed.append({'hazard_id': hazard_id, 'reason_code': 'NO_LONGER_INTERSECTS_IN_COMPLETE_REFRESH'})
        else:
            unknown.append(hazard_id)
    reasons = list(dict.fromkeys(triggers))
    if unknown:
        reasons.append('HAZARD_APPLICABILITY_UNKNOWN')
    source_report = current_state.to_dict()
    if (source_report['mode'] != 'LIVE' or any(
            s['status'] != 'available' or s['fetch_freshness'] != 'current'
            or s['data_state'] not in ('LIVE_CURRENT', 'EXPIRED')
            for s in source_report['sources'].values())):
        reasons.append('DATA_LIMITED')
    if not triggers:
        reasons.append('NO_NEW_REROUTE_TRIGGER')
    alternative, alternative_status, updated = None, 'NOT_REQUESTED', False
    if triggers and recalculate:
        origin = current_position or (route['origin']['latitude'], route['origin']['longitude'])
        destination = (route['destination']['latitude'], route['destination']['longitude'])
        try:
            alternative = build_risk_route(graph, origin, destination, new,
                                           max_snap_distance_m=max_snap_distance_m)
            updated = alternative['route_edges'] != route['route_edges']
            alternative_status = 'UPDATED' if updated else 'SAME_ROUTE'
            reasons.append('ALTERNATIVE_ROUTE_FOUND' if updated else 'NO_BETTER_ROUTE')
        except NoRouteAvailable:
            alternative_status = 'NO_ROUTE'
            reasons.append('NO_PASSABLE_ROUTE')
    return {'route_still_viable': new_summary['passable'], 'reroute_recommended': bool(triggers),
            'route_updated': updated, 'newly_affected_edges': newly_affected,
            'newly_encountered_hazard_ids': sorted(new_ids - old_ids),
            'hazards_no_longer_applicable': removed, 'hazard_applicability_unknown': unknown,
            'old_state_timestamp': iso(previous_state.evaluated_at),
            'new_state_timestamp': iso(current_state.evaluated_at),
            'old_snapshot_timestamp': iso(previous_state.snapshot.created_at) if previous_state.snapshot else None,
            'new_snapshot_timestamp': iso(current_state.snapshot.created_at) if current_state.snapshot else None,
            'old_route_summary': old_summary, 'current_route_summary': new_summary,
            'alternative_status': alternative_status, 'alternative_route': alternative,
            'reason_codes': reasons, 'data_state': source_report,
            'explanation': 'Evaluated the supplied route against known policy constraints; physical safety is not established.'}
