"""Unified adapter boundary around the existing hazard and routing models.

Adapters are caller-named zero-argument callables returning HazardBatch. They may
fetch data or adapt supplied local records; the routing algorithm knows neither.
Freshness lives in Hazard.metadata, alongside source-specific evidence. Adapters
must evaluate validity at the same evaluation time passed to this pipeline.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping

from shapely.geometry import shape
from shapely.errors import ShapelyError

from .hazards import Hazard, validate_timestamp
from .risk import RiskLevel, RiskPolicy
from .risk_routing import compare_routes


@dataclass(frozen=True)
class HazardBatch:
    """An adapter result, not another hazard/risk model.

    available means the adapter completed its requested scope, never universal
    coverage. Context retains unmappable records, stale exclusions and gauges.
    """
    hazards: tuple = ()
    status: str = 'available'
    issues: tuple = ()
    context: tuple = ()
    coverage: Mapping = field(default_factory=dict)


def _normalize(record):
    if isinstance(record, Hazard):
        # Revalidate caller-owned mutable metadata and any altered geometry.
        return Hazard(**{name: getattr(record, name) for name in Hazard.__dataclass_fields__})
    if not isinstance(record, Mapping):
        raise ValueError('Expected Hazard or hazard mapping.')
    values = dict(record)
    if isinstance(values.get('geometry'), Mapping):
        values['geometry'] = shape(values['geometry'])
    if isinstance(values.get('timestamp'), str):
        values['timestamp'] = datetime.fromisoformat(values['timestamp'].replace('Z', '+00:00'))
    # Missing optional provenance is visible, never invented as authoritative.
    values.setdefault('source', 'unknown')
    values.setdefault('confidence', 0.0)
    return Hazard(**values)


class _EvidencePolicy:
    """Stale/unknown evidence remains spatially visible but cannot block a road.

    Current evidence uses the caller policy. Informational evidence contributes
    zero cost. Legacy strict APIs keep their existing behavior unchanged.
    """
    def __init__(self, policy):
        self.policy = policy
        self.penalties = policy.penalties
        self.uncertainty_penalty_scale = policy.uncertainty_penalty_scale

    def level_for(self, hazard):
        if hazard.metadata.get('freshness', 'unknown') != 'current' or hazard.source == 'unknown':
            return RiskLevel.SAFE
        return self.policy.level_for(hazard)


def compare_hazard_routes(graph, origin, destination, hazards=(), *, adapters=None,
                          policy=None, evaluated_at=None, max_snap_distance_m=1000):
    """Tolerant ingestion; strict graph/policy errors still propagate.

    Combination reuses evaluate_road_risks: maximum category controls blocking,
    additive penalties once per unique hazard per edge, minimum confidence.
    Duplicate IDs invalidate every conflicting record rather than choosing one.
    Fixed evaluated_at plus identical input gives identical serialized output.
    """
    now = datetime.now(timezone.utc) if evaluated_at is None else evaluated_at
    validate_timestamp(now)
    policy = RiskPolicy() if policy is None else policy
    records, sources, warnings, context = [], [], [], []

    def ingest(name, batch):
        if not isinstance(batch, HazardBatch) or batch.status not in ('available', 'partial', 'unavailable'):
            raise ValueError('Adapter must return a HazardBatch with a valid status.')
        source = {'adapter': name, 'status': batch.status, 'issues': list(batch.issues),
                  'coverage': dict(batch.coverage)}
        sources.append(source)
        context.extend(batch.context)
        if batch.status != 'available' or batch.issues:
            warnings.append({'code': 'ADAPTER_DEGRADED', 'adapter': name,
                             'issues': list(batch.issues)})
        for index, raw in enumerate(batch.hazards):
            try:
                hazard = _normalize(raw)
                state = hazard.metadata.get('freshness', 'unknown')
                if state not in ('current', 'stale', 'expired', 'unknown', 'not_yet_active'):
                    raise ValueError('Invalid freshness state.')
                # Check severity even for stale records, so policy errors cannot
                # turn into authoritative input if the record later becomes fresh.
                RiskLevel(policy.level_for(hazard))
                records.append((name, hazard))
                if state != 'current' or hazard.source == 'unknown':
                    source['status'] = 'partial'
                    warnings.append({'code': 'NON_AUTHORITATIVE_EVIDENCE', 'adapter': name,
                                     'hazard_id': hazard.id, 'freshness': state})
            except (ValueError, TypeError, KeyError, AttributeError, ShapelyError) as exc:
                source['status'] = 'partial'
                warnings.append({'code': 'INVALID_HAZARD', 'adapter': name,
                                 'record_index': index, 'reason': str(exc)})

    if hazards is not None:
        try:
            ingest('supplied', HazardBatch(tuple(hazards)))
        except (ValueError, TypeError) as exc:
            warnings.append({'code': 'INVALID_INPUT', 'reason': str(exc)})
    for name, adapter in sorted((adapters or {}).items()):
        try:
            ingest(name, adapter())
        except Exception as exc:
            sources.append({'adapter': name, 'status': 'unavailable', 'issues': [str(exc)], 'coverage': {}})
            warnings.append({'code': 'ADAPTER_FAILED', 'adapter': name, 'reason': str(exc)})
    counts = {}
    for _, hazard in records:
        counts[hazard.id] = counts.get(hazard.id, 0) + 1
    duplicates = sorted(id for id, count in counts.items() if count > 1)
    if duplicates:
        warnings.append({'code': 'DUPLICATE_HAZARDS', 'hazard_ids': duplicates})
    normalized = sorted((h for _, h in records if counts[h.id] == 1), key=lambda h: h.id)
    result = compare_routes(graph, origin, destination, normalized, _EvidencePolicy(policy),
                            evaluated_at=now, max_snap_distance_m=max_snap_distance_m)
    authoritative = [h for h in normalized if h.metadata.get('freshness') == 'current'
                     and h.source != 'unknown']
    status = 'unavailable' if not authoritative else ('incomplete' if warnings else 'available')
    if not authoritative:
        warnings.append({'code': 'NO_USABLE_HAZARD_DATA'})
    catalog = {h.id: h.to_dict() for h in normalized}
    for mode in ('distance', 'risk_aware'):
        route = result[mode]['route']
        if route is not None:
            route['hazards_encountered'] = [catalog[id] for id in route['hazard_ids']]
    result['baseline'] = result['distance']
    result['safer'] = result['risk_aware']
    result['avoided_edge_count'] = len(result['avoided_roads']) if result['avoided_roads'] is not None else None
    result['hazard_data'] = {'evaluated_at': now.isoformat(), 'coverage_status': status,
                             'sources': sources, 'hazards': [h.to_dict() for h in normalized],
                             'context': context, 'warnings': warnings,
                             'notice': 'Coverage describes supplied inputs only; no hazard intersection does not establish road safety.'}
    return result
