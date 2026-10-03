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
    if state.generic:
        from ..pipeline import EvidencePolicy, compare_hazard_routes, normalize_hazard
        from ..risk import RiskPolicy
        policy = RiskPolicy() if policy is None else policy
        batches = state.hazard_batches
        result = compare_hazard_routes(graph, origin, destination, hazards=None,
            adapters={name: (lambda batch=batch: batch) for name, batch in batches.items()},
            policy=policy, evaluated_at=state.evaluated_at, max_snap_distance_m=max_snap_distance_m)
        accepted_hazards = tuple(normalize_hazard(item) for item in result['hazard_data']['hazards'])
        result['destinations'] = filter_destinations(candidates, accepted_hazards, EvidencePolicy(policy),
                                                     evaluated_at=state.evaluated_at)
        report = state.to_dict()
        if len(accepted_hazards) != len(state.hazards):
            report['hazard_coverage_state'] = result['hazard_data']['coverage_status']
        if report['hazard_coverage_state'] != 'available':
            result['hazard_data']['coverage_status'] = report['hazard_coverage_state']
        if state.cache_error:
            result['hazard_data']['warnings'].append({'code': 'CACHE_ERROR', **state.cache_error})
        result['hazard_data']['warnings'].extend(report['warnings'])
        result['data_state'] = report
        return result
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
    if previous_state.generic or current_state.generic:
        if previous_state.generic != current_state.generic:
            raise ValueError('Cannot compare legacy and unified data states.')
        return _reevaluate_normalized(graph, route, previous_state, current_state, policy=policy,
            recalculate=recalculate, current_position=current_position,
            max_snap_distance_m=max_snap_distance_m)
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



def _reevaluate_normalized(graph, route, previous_state, current_state, *, policy=None,
                           recalculate=True, current_position=None, max_snap_distance_m=1000):
    """Generic reevaluation over existing exact-edge risks and cost hooks."""
    from ..pipeline import EvidencePolicy
    from ..risk import RiskPolicy
    from .normalized import hazard_freshness
    if previous_state.coverage != current_state.coverage:
        raise CacheError('COVERAGE_MISMATCH', 'Cannot compare different snapshot regions.')
    edges = _check_coverage(graph, current_state)
    ids = _edge_ids(route)
    policy = EvidencePolicy(RiskPolicy() if policy is None else policy)
    old_batches, new_batches = previous_state.hazard_batches, current_state.hazard_batches
    old_hazards = {h.id: (name, h) for name, batch in old_batches.items() for h in batch.hazards}
    new_hazards = {h.id: (name, h) for name, batch in new_batches.items() for h in batch.hazards}
    invalid_current_sources, validation_warnings = set(), []
    def classified(hazards, owners, current):
        valid = []
        for hazard in hazards:
            try:
                RiskLevel(policy.policy.level_for(hazard))
                valid.append(hazard)
            except (ValueError, TypeError, KeyError) as exc:
                name = owners[hazard.id][0]
                validation_warnings.append({'code': 'INVALID_POLICY_HAZARD', 'adapter': name,
                                            'hazard_id': hazard.id, 'reason': str(exc)})
                if current:
                    invalid_current_sources.add(name)
        return tuple(valid)
    old_valid = classified(previous_state.hazards, old_hazards, False)
    new_valid = classified(current_state.hazards, new_hazards, True)
    old = evaluate_road_risks(edges, old_valid, policy, previous_state.evaluated_at)
    new = evaluate_road_risks(edges, new_valid, policy, current_state.evaluated_at)
    before, after = _summary(route, ids, old), _summary(route, ids, new)
    def active_ids(summary):
        return {c['hazard_id'] for r in summary['road_risks'] for c in r['contributions']
                if c['risk_level'] != RiskLevel.SAFE.value}
    old_ids, new_ids = active_ids(before), active_ids(after)
    changes, affected, triggers, triggering_ids = [], [], [], set()
    for edge in ids:
        if edge not in new or edge not in old:
            continue
        previous, current = old[edge], new[edge]
        added = sorted({c['hazard_id'] for c in current.contributions if c['risk_level'] != 'SAFE'} -
                       {c['hazard_id'] for c in previous.contributions if c['risk_level'] != 'SAFE'})
        before_contributions = {c['hazard_id']: c for c in previous.contributions}
        increased_ids = sorted(c['hazard_id'] for c in current.contributions
            if c['risk_level'] != 'SAFE' and (c['hazard_id'] not in before_contributions
                or LEVEL_ORDER[RiskLevel(c['risk_level'])] >
                   LEVEL_ORDER[RiskLevel(before_contributions[c['hazard_id']]['risk_level'])]
                or c['hazard_penalty'] + c['uncertainty_penalty'] >
                   before_contributions[c['hazard_id']]['hazard_penalty'] +
                   before_contributions[c['hazard_id']]['uncertainty_penalty']))
        if (current.risk_level != previous.risk_level or current.penalty != previous.penalty
                or set(current.hazard_ids) != set(previous.hazard_ids)):
            changes.append({'edge_id': current.to_dict()['edge_id'], 'previous': previous.to_dict(),
                            'current': current.to_dict(), 'triggering_hazard_ids': increased_ids})
        increased = (current.penalty > previous.penalty
                     or LEVEL_ORDER[current.risk_level] > LEVEL_ORDER[previous.risk_level] or bool(added))
        if increased and current.risk_level != RiskLevel.SAFE:
            affected.append(current.to_dict())
            triggering_ids.update(increased_ids)
            triggers.append('NEW_PENALIZED_EDGE' if current.passable else 'NEW_BLOCKED_EDGE')
    if after['missing_edges']:
        triggers.append('ROUTE_EDGE_MISSING')
    if not after['passable']:
        triggers.append('ROUTE_NOT_PASSABLE')
    resolved, unknown = [], []
    for hazard_id in sorted(old_ids - new_ids):
        name, hazard = old_hazards[hazard_id]
        batch = new_batches.get(name)
        value = hazard_freshness(hazard, old_batches[name], current_state.evaluated_at)
        if value == 'expired':
            resolved.append({'hazard_id': hazard_id, 'reason_code': 'HAZARD_EXPIRED',
                             'evidence': hazard.to_dict()})
        elif (batch is not None and hazard_id in batch.removed_hazard_ids
              and current_state.to_dict()['sources'][name]['fetch_freshness'] == 'current'):
            resolved.append({'hazard_id': hazard_id, 'reason_code': 'HAZARD_WITHDRAWN_BY_SOURCE',
                             'evidence': hazard.to_dict()})
        elif (batch is not None and batch.status == 'available' and not batch.issues
              and name not in invalid_current_sources
              and batch.data_origin == 'live'
              and current_state.to_dict()['sources'][name]['fetch_freshness'] == 'current'
              and not (current_state.snapshot and any(w.get('adapter') in (None, name)
                       for w in current_state.snapshot.warnings))
              and (hazard_id not in new_hazards or
                   new_hazards[hazard_id][1].metadata.get('freshness') == 'current')):
            resolved.append({'hazard_id': hazard_id, 'reason_code': 'NO_LONGER_AFFECTS_COMPLETE_REFRESH',
                             'evidence': hazard.to_dict()})
        else:
            unknown.append(hazard_id)
    reasons = sorted(set(triggers))
    if unknown:
        reasons.append('HAZARD_APPLICABILITY_UNKNOWN')
    report = current_state.to_dict()
    report['warnings'].extend(validation_warnings)
    if invalid_current_sources:
        report['hazard_coverage_state'] = 'incomplete' if any(
            h.metadata.get('freshness') == 'current' and h.source != 'unknown' for h in new_valid) else 'unavailable'
    if report['hazard_coverage_state'] != 'available':
        reasons.append('DATA_LIMITED')
    if not triggers:
        reasons.append('NO_NEW_REROUTE_TRIGGER')
    alternative, status, updated, distance_difference, risk_difference = None, 'NOT_REQUESTED', False, None, None
    avoided_edges, avoided_hazards = None, None
    affected_difference, blocked_difference, exposure_difference = None, None, None
    if triggers and recalculate:
        origin = current_position or (route['origin']['latitude'], route['origin']['longitude'])
        destination = (route['destination']['latitude'], route['destination']['longitude'])
        comparison = route_with_state(graph, origin, destination, current_state,
                                      policy=policy.policy, max_snap_distance_m=max_snap_distance_m)
        alternative = comparison['safer']['route']
        if alternative is None:
            status = 'NO_ROUTE'
            reasons.append('NO_PASSABLE_ROUTE')
        else:
            updated = alternative['route_edges'] != route['route_edges']
            status = 'UPDATED' if updated else 'SAME_ROUTE'
            reasons.append('ALTERNATIVE_ROUTE_FOUND' if updated else 'NO_BETTER_ROUTE')
            distance_difference = alternative['total_distance_m'] - after['total_distance_m']
            risk_difference = alternative['total_risk_penalty'] - after['total_risk_penalty']
            used = {(e['u'], e['v'], e['key']) for e in alternative['route_edges']}
            avoided_edges = [r for r in after['road_risks'] if r['risk_level'] != 'SAFE'
                             and tuple(r['edge_id'][k] for k in ('u', 'v', 'key')) not in used]
            alt_active = {c['hazard_id'] for r in alternative['road_risks'] for c in r['contributions']
                          if c['risk_level'] != 'SAFE'}
            avoided_hazards = sorted(new_ids - alt_active)
            affected_difference = sum(r['risk_level'] != 'SAFE' for r in alternative['road_risks']) - \
                                  sum(r['risk_level'] != 'SAFE' for r in after['road_risks'])
            blocked_difference = sum(not r['passable'] for r in alternative['road_risks']) - \
                                 sum(not r['passable'] for r in after['road_risks'])
            exposure_difference = len(alt_active) - len(new_ids)
    return {'route_still_viable': after['passable'], 'reroute_recommended': bool(triggers),
            'newly_affected_edges': affected, 'changed_edges': changes,
            'newly_encountered_hazard_ids': sorted(new_ids - old_ids),
            'hazards_causing_reroute': [new_hazards[id][1].to_dict() for id in sorted(triggering_ids)],
            'newly_encountered_hazards': [new_hazards[id][1].to_dict() for id in sorted(new_ids - old_ids)],
            'resolved_hazards': resolved, 'hazard_applicability_unknown': unknown,
            'old_snapshot_timestamp': iso(previous_state.snapshot.created_at) if previous_state.snapshot else None,
            'new_snapshot_timestamp': iso(current_state.snapshot.created_at) if current_state.snapshot else None,
            'old_state_timestamp': iso(previous_state.evaluated_at), 'new_state_timestamp': iso(current_state.evaluated_at),
            'old_route_summary': before, 'current_route_summary': after,
            'alternative_route': alternative, 'alternative_status': status, 'route_updated': updated,
            'distance_difference_m': distance_difference, 'risk_penalty_difference': risk_difference,
            'affected_edge_count_difference': affected_difference,
            'blocked_edge_count_difference': blocked_difference,
            'hazard_exposure_difference': exposure_difference,
            'affected_edge_count': sum(r['risk_level'] != 'SAFE' for r in after['road_risks']),
            'avoided_edge_count': len(avoided_edges) if avoided_edges is not None else None,
            'avoided_edges': avoided_edges, 'avoided_hazard_ids': avoided_hazards,
            'reason_codes': reasons, 'data_state': report}
