"""Official tropical alert areas -> existing normalized hazards and batches."""
from dataclasses import dataclass, field, replace
import json
from types import MappingProxyType
from typing import Mapping

from ..flood.adapter import prepare_alert_hazards
from ..flood.freshness import ALERT_FRESHNESS, freshness, utc_now
from ..flood.models import FloodAlert
from ..flood.nws import NWS_SOURCE
from ..pipeline import HazardBatch
from ..risk import RiskLevel, RiskPolicy
from .nws import TropicalNWSClient, canonical_event, tropical_event


@dataclass(frozen=True)
class HurricanePolicy(RiskPolicy):
    """Warnings penalize exposure, watches caution; statements are informational.

    Blocking requires BOTH an explicit event override and a documented policy
    justification. Regional wind values never automatically block an edge.
    Non-hurricane hazards use the inherited severity mapping.
    """
    event_levels: Mapping = field(default_factory=dict)
    blocking_reason: str = ''

    def __post_init__(self):
        super().__post_init__()
        levels = {canonical_event(key): RiskLevel(value) for key, value in self.event_levels.items()
                  if tropical_event(key)}
        if len(levels) != len(self.event_levels):
            raise ValueError('Overrides must use unique supported tropical event names.')
        if RiskLevel.IMPASSABLE in levels.values() and (
                not isinstance(self.blocking_reason, str) or not self.blocking_reason.strip()):
            raise ValueError('An explicit blocking_reason is required for IMPASSABLE event overrides.')
        object.__setattr__(self, 'event_levels', MappingProxyType(levels))

    def level_for_event(self, event):
        key = canonical_event(event)
        if key in self.event_levels:
            return self.event_levels[key]
        if key.endswith('warning'):
            return RiskLevel.HIGH_RISK
        if key.endswith(('watch', 'advisory')):
            return RiskLevel.CAUTION
        return RiskLevel.SAFE

    def level_for(self, hazard):
        if hazard.hazard_type == 'hurricane' and hazard.metadata.get('evidence_kind') == 'official_alert_area':
            return self.level_for_event(hazard.metadata['event'])
        return super().level_for(hazard)


def _effect(event):
    key = canonical_event(event)
    if key.startswith('storm surge '):
        return 'storm_surge'
    if key.endswith(('statement', 'advisory')):
        return 'tropical_cyclone_context'
    return 'tropical_cyclone_wind'


def hurricane_hazard_batch(result, *, now=None, policy=None,
                            freshness_policy=ALERT_FRESHNESS, source_confidence=1.0):
    """Pure adaptation; no downloads, invented buffers, or inferred road depths.

    IDs are the canonical NWS alert IDs, shared across all NWS adapter families.
    The shared CAP parser keeps one record per ID and reports duplicates.
    """
    now = utc_now() if now is None else now
    policy = HurricanePolicy() if policy is None else policy
    if result.source != NWS_SOURCE:
        raise ValueError('Expected a parsed NOAA/NWS SourceResult.')
    rows, issues = [], list(result.issues)
    for row in result.records:
        try:
            if not isinstance(row, FloodAlert):  # Existing parsed CAP record model.
                raise ValueError('Expected a parsed NWS alert record.')
            if not tropical_event(row.event):
                continue
            json.dumps(row.to_dict(), allow_nan=False)
            if row.metadata.get('feature_url') is not None and not isinstance(row.metadata['feature_url'], str):
                raise ValueError('Invalid official feature URL.')
            state = freshness(observed_at=row.sent, fetched_at=row.fetched_at,
                expires_at=row.expires_at, effective_at=row.effective, now=now, policy=freshness_policy).value
            # An old/future cancellation cannot withdraw current source evidence.
            if state != 'current' and row.message_type in ('Cancel', 'Update'):
                row = replace(row, metadata={**row.metadata,
                    'ignored_noncurrent_references': row.metadata.get('references'), 'references': []})
            rows.append(row)
        except (TypeError, ValueError, AttributeError) as exc:
            issues.append(f'Invalid tropical alert skipped: {exc}')
    # Defensive adaptation also handles callers combining equivalent parsed rows.
    unique = {}
    for row in rows:
        if row.id in unique:
            issues.append(f'Duplicate tropical alert ID {row.id}; counted once.')
        else:
            unique[row.id] = row
    filtered = replace(result, records=tuple(unique[key] for key in sorted(unique)), issues=tuple(issues))
    hazards, alerts = prepare_alert_hazards(filtered, hazard_type='hurricane',
        interpretation='Official tropical alert area; physical road impassability is not established.',
        now=now, freshness_policy=freshness_policy, include_stale=True, source_confidence=source_confidence)
    labels = {RiskLevel.SAFE: 'none', RiskLevel.CAUTION: 'moderate',
              RiskLevel.HIGH_RISK: 'high', RiskLevel.IMPASSABLE: 'critical'}
    normalized = []
    for hazard in hazards:
        level = policy.level_for_event(hazard.metadata['event'])
        state = hazard.metadata['freshness']
        parameters = hazard.metadata.get('parameters')
        parameters = parameters if isinstance(parameters, dict) else {}
        metadata = {**hazard.metadata, 'effect': _effect(hazard.metadata['event']),
            'storm_name': parameters.get('stormName'), 'storm_id': parameters.get('stormId'),
            'alert_stage': canonical_event(hazard.metadata['event']).rsplit(' ', 1)[-1],
            'freshness_policy': freshness_policy.to_dict(),
            'risk_mapping': {'level': level.value, 'blocking_reason': policy.blocking_reason if level == RiskLevel.IMPASSABLE else None},
            'reason': f"Road intersects {'active' if state == 'current' else state} {hazard.metadata['event']} area; "
                      'this does not establish physical road blockage.'}
        normalized.append(replace(hazard, severity=labels[level], metadata=metadata))
    removed = set()
    id_map = {r.metadata.get('feature_url'): r.id for r in filtered.records
              if isinstance(r.metadata.get('feature_url'), str)}
    for alert in alerts:
        if alert['status'] != 'Actual':
            continue
        current = alert['freshness'] == 'current'
        if alert['freshness'] == 'expired' or (current and alert['message_type'] == 'Cancel'):
            removed.add(f"nws:{alert['id']}")
        if current and alert['message_type'] in ('Cancel', 'Update'):
            for reference in alert['metadata'].get('references') or []:
                if isinstance(reference, dict):
                    identifier = reference.get('identifier') or reference.get('@id')
                    if isinstance(identifier, str) and identifier:
                        # Resolve supplied feature URLs back to their CAP IDs.
                        removed.add('nws:' + id_map.get(identifier, identifier))
    fetch_state = freshness(observed_at=result.fetched_at, fetched_at=result.fetched_at,
                            now=now, policy=freshness_policy).value
    limited = any(a['freshness'] not in ('current', 'expired') or
                  (a['freshness'] == 'current' and not a['used_for_routing']
                   and a['message_type'] != 'Cancel') for a in alerts)
    if limited or fetch_state != 'current':
        issues.append('Tropical evidence is stale, unknown, future, or unmappable; inspect alert context.')
    status = result.status
    if status == 'available' and (issues or limited):
        status = 'partial'
    source_context = {k: v for k, v in filtered.to_dict().items() if k != 'records'}
    return HazardBatch(tuple(normalized), status, tuple(issues),
        tuple({'kind': 'nws_tropical_alert', **alert} for alert in alerts) + (
            {'kind': 'source', **source_context, 'freshness': fetch_state},),
        {'nws_query': dict(result.query), 'events': 'tropical_cyclone'},
        fetched_at=result.fetched_at, attempted_at=result.attempted_at, data_origin=result.data_origin,
        freshness_policy=freshness_policy.to_dict(), removed_hazard_ids=tuple(sorted(removed)))


def registered_hurricane_adapters(*, nws_client=None, area='NC', nhc_client=None,
                                  include_nhc=True, policy=None, freshness_policy=ALERT_FRESHNESS):
    """Independent tropical NWS and NHC entries for the generic RefreshService."""
    client = nws_client if nws_client is not None else TropicalNWSClient()
    def nws(now):
        return hurricane_hazard_batch(client.fetch(area=area), now=now, policy=policy,
                                      freshness_policy=freshness_policy)
    adapters = {'hurricane_nws': nws}
    if include_nhc:
        from .nhc import NHCClient
        nhc = nhc_client if nhc_client is not None else NHCClient()
        adapters['hurricane_nhc'] = lambda now: nhc.fetch(now=now)
    return adapters
