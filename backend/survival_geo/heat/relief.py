"""Relief discovery/evaluation around existing destination and routing APIs.

Candidates remain ordinary destination dictionaries, suitable for local JSON.
Authoritative sources must be explicitly trusted by the integration caller.
"""
from collections import Counter
from dataclasses import dataclass
import json
import math

from shapely.geometry import Point, mapping, shape
from shapely.errors import ShapelyError

from ..destination_safety import filter_destinations
from ..destinations import discover_destinations
from ..errors import DataAccessError, EmptyOSMResults, LocationOutsideGraph
from ..flood.freshness import ALERT_FRESHNESS, freshness, iso, parse_time
from ..hazards import validate_geometry
from ..offline.routing import route_with_state
from ..pipeline import EvidencePolicy, normalize_hazard
from ..risk import RiskLevel
from ..roads import load_road_graph
from ..validation import location
from .adapter import HeatPolicy

RELIEF_CATEGORIES = {
    'cooling_centre': {'amenity': ['cooling_centre', 'cooling_center'],
                       'social_facility': ['cooling_centre', 'cooling_center']},
    'library': {'amenity': ['library']},
    'community_centre': {'amenity': ['community_centre']},
    'public_facility': {'amenity': ['townhall']},
    'shelter': {'social_facility': ['shelter']},
    'medical': {'amenity': ['hospital', 'clinic']},
}
POTENTIAL = 'potential_heat_relief_location'
OFFICIAL = 'official_cooling_center'


@dataclass(frozen=True)
class ReliefPolicy:
    ranking: tuple = ('official', 'risk_penalty', 'affected_distance', 'distance')
    unsafe_levels: tuple = (RiskLevel.HIGH_RISK, RiskLevel.IMPASSABLE)

    def __post_init__(self):
        allowed = {'official', 'risk_penalty', 'affected_distance', 'distance'}
        if not self.ranking or len(set(self.ranking)) != len(self.ranking) or set(self.ranking) - allowed:
            raise ValueError('Supply unique supported ranking criteria.')
        object.__setattr__(self, 'ranking', tuple(self.ranking))
        object.__setattr__(self, 'unsafe_levels', tuple(RiskLevel(x) for x in self.unsafe_levels))


def discover_relief_candidates(latitude, longitude, *, radius_m=5000, categories=None):
    """Reuse OSMnx discovery; even cooling-centre tags remain potential only."""
    try:
        rows = discover_destinations(latitude, longitude, radius_m=radius_m,
            categories=RELIEF_CATEGORIES if categories is None else categories)
    except (EmptyOSMResults, DataAccessError) as exc:
        return {'candidates': [], 'status': 'unavailable', 'issues': [str(exc)]}
    return {'status': 'available', 'issues': [], 'candidates': [
        {**row, 'id': f"osm:{row['element_type']}:{row['osm_id']}",
         'source': 'OpenStreetMap', 'classification': POTENTIAL} for row in rows]}


def ingest_official_centers(records, *, source_id, authority, source_url, fetched_at):
    """Integration boundary for designated centers from a verified structured feed.

    No feed is implicitly trusted. Evaluation also requires trusted_sources to
    match source_id -> source_url. Raw OSM tags or classification flags cannot
    promote a location. Records must explicitly say designated_cooling_center.
    """
    if not all(isinstance(x, str) and x.strip() for x in (source_id, authority, source_url)):
        raise ValueError('Official source identity, authority and URL required.')
    if fetched_at is None:
        raise ValueError('Original fetch time required.')
    fetched = iso(fetched_at)
    result = []
    for raw in records:
        row = dict(raw)
        evidence = {'source_id': source_id, 'authority': authority, 'source_url': source_url,
            'fetched_at': fetched, 'observed_at': row.get('observed_at'),
            'expires_at': row.get('expires_at'),
            'designated_cooling_center': row.get('designated_cooling_center') is True}
        result.append({**row, 'source': source_id, 'official_evidence': evidence})
    return result


def _candidate(raw, now, trusted_sources):
    row = dict(raw)
    identifier = row.get('id')
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError('Candidate requires a stable string ID.')
    lat, lon = location(row['latitude'], row['longitude'])
    geometry = shape(row['geometry']) if row.get('geometry') else Point(lon, lat)
    validate_geometry(geometry)
    if not geometry.covers(Point(lon, lat)):
        raise ValueError('Routing point must lie in the candidate footprint.')
    evidence = row.get('official_evidence') or {}
    state, verified = 'unknown', False
    if evidence:
        state = freshness(observed_at=parse_time(evidence.get('observed_at')),
            fetched_at=parse_time(evidence.get('fetched_at')),
            expires_at=parse_time(evidence.get('expires_at')), now=now, policy=ALERT_FRESHNESS).value
        verified = (row.get('source') != 'OpenStreetMap' and
            row.get('source') == evidence.get('source_id') and
            evidence.get('source_id') in trusted_sources and
            trusted_sources[evidence['source_id']] == evidence.get('source_url') and
            bool(evidence.get('authority')) and evidence.get('designated_cooling_center') is True and
            state == 'current')
    result = {**row, 'latitude': lat, 'longitude': lon, 'geometry': mapping(geometry),
        'name': row.get('name'), 'categories': row.get('categories', []),
        'source': row.get('source', 'unknown'), 'classification': OFFICIAL if verified else POTENTIAL,
        'designation_freshness': state, 'open': None, 'air_conditioned': None,
        'accepting_people': None, 'safety_verified': False}
    json.dumps(result, allow_nan=False)
    return result


def route_exposure(graph, route):
    """Whole selected-edge lengths, not clipped polygon distance or heat dose.

    Count each selected edge once per hazard type, even with overlapping alerts.
    Stale/informational intersections stay visible separately from active cost.
    """
    if route is None:
        return None
    groups = {}
    for road in route['road_risks']:
        edge = road['edge_id']
        length = graph.edges[edge['u'], edge['v'], edge['key']].get('length')
        length = float(length) if length is not None else None
        if length is not None and (not math.isfinite(length) or length < 0):
            length = None
        active_types = set()
        for contribution in road['contributions']:
            kind = contribution['hazard_type']
            group = groups.setdefault(kind, {'hazards': {}, 'affected_edge_count': 0,
                'affected_edge_distance_m': 0.0, 'distance_complete': True})
            group['hazards'][contribution['hazard_id']] = contribution
            if contribution['freshness'] == 'current' and contribution['risk_level'] != 'SAFE':
                active_types.add(kind)
        for kind in active_types:
            group = groups[kind]
            group['affected_edge_count'] += 1
            if length is None:
                group['distance_complete'] = False
            else:
                group['affected_edge_distance_m'] += length
    for group in groups.values():
        group['hazards'] = [group['hazards'][key] for key in sorted(group['hazards'])]
        if not group['distance_complete']:
            group['affected_edge_distance_m'] = None
    return {'by_hazard_type': dict(sorted(groups.items())),
            'distance_basis': 'full length of intersecting selected edges; not clipped exposure distance'}


def evaluate_relief_candidates(graph, origin, candidates, state, *, policy=None,
                               relief_policy=None, trusted_sources=None, max_snap_distance_m=1000):
    """Route each local candidate through the existing multi-hazard pipeline."""
    policy = HeatPolicy() if policy is None else policy
    selection = ReliefPolicy() if relief_policy is None else relief_policy
    trusted_sources = {} if trusted_sources is None else trusted_sources
    candidates = list(candidates)
    ids = Counter(row.get('id') for row in candidates if isinstance(row, dict)
                  and isinstance(row.get('id'), str))
    results, ranked = [], []
    for index, raw in enumerate(candidates):
        try:
            candidate = _candidate(raw, state.evaluated_at, trusted_sources)
            if ids[candidate['id']] > 1:
                raise ValueError('Duplicate candidate ID; all conflicting candidates excluded.')
        except (ValueError, TypeError, KeyError, AttributeError, ShapelyError) as exc:
            results.append({'input_index': index, 'eligible': False,
                            'exclusion_reasons': ['INVALID_CANDIDATE'], 'detail': str(exc)})
            continue
        record = {'destination': candidate, 'eligible': False, 'exclusion_reasons': []}
        try:
            comparison = route_with_state(graph, origin,
                (candidate['latitude'], candidate['longitude']), state, policy=policy,
                max_snap_distance_m=max_snap_distance_m)
        except LocationOutsideGraph as exc:
            record.update(exclusion_reasons=['OUTSIDE_GRAPH'], detail=str(exc))
            results.append(record)
            continue
        hazards = tuple(normalize_hazard(h) for h in comparison['hazard_data']['hazards'])
        evidence_policy = EvidencePolicy(policy)
        filtered = filter_destinations([candidate], hazards, evidence_policy,
            unsafe_levels=selection.unsafe_levels, evaluated_at=state.evaluated_at)
        footprint = shape(candidate['geometry'])
        record['hazard_intersections'] = [
            {'hazard_id': h.id, 'hazard_type': h.hazard_type,
             'freshness': h.metadata.get('freshness', 'unknown'),
             'risk_level': evidence_policy.level_for(h).value,
             'active': h.metadata.get('freshness') == 'current'}
            for h in hazards if footprint.intersects(h.geometry)]
        record['comparison'] = comparison
        record['exposure'] = {mode: route_exposure(graph, comparison[mode]['route'])
                              for mode in ('baseline', 'safer')}
        record['weather_context'] = [item for item in comparison['hazard_data']['context']
                                     if item.get('kind') == 'heat_weather_observation']
        if filtered['excluded']:
            record['exclusion_reasons'].append('DESTINATION_HAZARD')
        route = comparison['safer']['route']
        if route is None:
            record['exclusion_reasons'].append('UNREACHABLE')
        if not record['exclusion_reasons']:
            record['eligible'] = True
            # Total risk reflects all hazards; exposure distance is a secondary
            # comparison and may count an edge under multiple hazard types.
            distances = [g['affected_edge_distance_m'] for g in
                         record['exposure']['safer']['by_hazard_type'].values()]
            metrics = {'official': int(candidate['classification'] != OFFICIAL),
                'risk_penalty': route['total_risk_penalty'],
                'affected_distance': sum(distances) if None not in distances else math.inf,
                'distance': route['total_distance_m']}
            key = tuple(metrics[name] for name in selection.ranking) + (candidate['id'],)
            record['ranking_values'] = {k: (v if math.isfinite(v) else None) for k, v in metrics.items()}
            ranked.append((key, record))
        results.append(record)
    ranked.sort(key=lambda item: item[0])
    for rank, (_, record) in enumerate(ranked, 1):
        record['rank'] = rank
    reasons = ['NO_ELIGIBLE_CANDIDATE']
    if ranked:
        reasons = ['ELIGIBLE_REACHABLE', 'LEXICOGRAPHIC_POLICY']
        if len(ranked) == 1:
            reasons.append('ONLY_ELIGIBLE_CANDIDATE')
        else:
            criteria = (*selection.ranking, 'stable_destination_id')
            decisive = next(name for name, first, second in
                            zip(criteria, ranked[0][0], ranked[1][0]) if first != second)
            reasons.append('PREFERRED_' + decisive.upper())
    return {'selected_destination_id': ranked[0][1]['destination']['id'] if ranked else None,
        'selection_reason_codes': reasons,
        'ranking_order': [*selection.ranking, 'stable_destination_id'],
        'ranked_destination_ids': [row['destination']['id'] for _, row in ranked],
        'candidates': results, 'data_state': state.to_dict(),
        'warnings': ['OPEN_STATUS_AND_RELIEF_CAPABILITY_UNVERIFIED', 'NO_SAFETY_GUARANTEE'] +
                    ([] if candidates else ['NO_CANDIDATES'])}


def relief_offline(service, graph_path, origin, candidates, *, now=None, **kwargs):
    """Candidates supplied in memory/from local JSON; no discovery or refresh."""
    state = service.load_offline(now=now)
    graph = load_road_graph(*origin, graph_path=graph_path, allow_download=False)
    return evaluate_relief_candidates(graph, origin, candidates, state, **kwargs)
