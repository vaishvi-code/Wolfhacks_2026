"""Flood route comparison composed from the unchanged generic risk engine."""
from ..risk_routing import compare_routes
from .adapter import FloodPolicy, prepare_flood_hazards
from .freshness import (
    ALERT_FRESHNESS, OBSERVATION_FRESHNESS, freshness, iso, utc_now,
)


def compare_flood_routes(graph, origin, destination, alerts, observations=None, *,
                         policy=None, now=None, alert_freshness=ALERT_FRESHNESS,
                         observation_freshness=OBSERVATION_FRESHNESS,
                         include_stale=False, source_confidence=1.0, max_snap_distance_m=1000):
    now = utc_now() if now is None else now
    snapshot = prepare_flood_hazards(alerts, now=now, freshness_policy=alert_freshness,
                                   include_stale=include_stale, source_confidence=source_confidence)
    comparison = compare_routes(graph, origin, destination, snapshot.hazards,
                                 FloodPolicy() if policy is None else policy,
                                 evaluated_at=now, max_snap_distance_m=max_snap_distance_m)
    comparison['flood_aware'] = comparison.pop('risk_aware')
    catalog = {hazard.id: hazard.to_dict() for hazard in snapshot.hazards}
    # Annotate serialized outputs only. Generic policy/graph code is untouched.
    collections = [comparison['road_risks'], comparison['avoided_roads'] or []]
    for mode in ('distance', 'flood_aware'):
        route = comparison[mode]['route']
        if route is not None:
            route['flood_hazards_encountered'] = [catalog[id] for id in route['hazard_ids']]
            collections.append(route['road_risks'])
    for collection in collections:
        for road in collection:
            contributions = [catalog[id] for id in road['hazard_ids']]
            road['sources'] = [{'hazard_id': h['id'], 'source': h['source'],
                               'official_alert_id': h['metadata']['official_alert_id'],
                               'endpoint': h['metadata']['source_endpoint'],
                               'freshness': h['metadata']['freshness']} for h in contributions]
            road['reasons'] = [
                f"Road intersects {'active' if h['metadata']['freshness'] == 'current' else 'stale'} "
                f"{h['metadata']['event']} area ({h['metadata']['official_alert_id']}); "
                'this does not establish physical road flooding.' for h in contributions
            ] or ['No mapped flood-alert intersection in the supplied data; road safety is unknown.']
    comparison['flood_hazards_avoided'] = (
        [catalog[id] for id in comparison['avoided_hazard_ids']]
        if comparison['avoided_hazard_ids'] is not None else None)
    source_results = [alerts] + ([observations] if observations is not None else [])
    source_info = []
    for source in source_results:
        source_policy = alert_freshness if source is alerts else observation_freshness
        source_info.append({key: value for key, value in source.to_dict().items() if key != 'records'})
        source_info[-1]['fetch_freshness'] = freshness(
            observed_at=source.fetched_at, fetched_at=source.fetched_at, now=now,
            policy=source_policy).value
    gauge_records = []
    if observations is not None:
        for observation in observations.records:
            state = freshness(observed_at=observation.observed_at, fetched_at=observation.fetched_at,
                              expires_at=observation.expires_at, now=now, policy=observation_freshness)
            gauge_records.append({**observation.to_dict(), 'freshness': state.value})
    degraded = (any(source['status'] != 'available' or source['fetch_freshness'] != 'current'
                    for source in source_info)
                or any(a['freshness'] not in ('current', 'expired') or
                       (a['freshness'] == 'current' and not a['used_for_routing']) for a in snapshot.alerts)
                or any(o['freshness'] != 'current' for o in gauge_records))
    intersecting = {id for road in comparison['road_risks'] for id in road['hazard_ids']}
    comparison['flood_data'] = {
        'evaluated_at': iso(now), 'assessment_status': 'degraded' if degraded else 'current_inputs',
        'sources': source_info, 'alerts': list(snapshot.alerts),
        'hazards': list(catalog.values()), 'observations': gauge_records,
        'applicable_hazard_ids': sorted(intersecting),
        'freshness_policy': {'alerts': alert_freshness.to_dict(),
                             'observations': observation_freshness.to_dict(),
                             'include_stale': include_stale},
        'notice': ('Flood routing uses supplied official alert areas; road inundation and safety are not established.'
                   if intersecting else
                   'No applicable active flood hazard was returned. This is not evidence of safe roads.'),
    }
    if alerts.status == 'unavailable':
        comparison['flood_data']['notice'] = (
            'NWS source unavailable; flood assessment is incomplete. '
            + ('Explicitly supplied local records were evaluated.' if snapshot.hazards
               else 'No usable mapped alert is available; distance routing remains available.'))
    return comparison
