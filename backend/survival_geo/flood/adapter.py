"""Convert official alert areas to generic hazards, preserving uncertainty."""
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from ..hazards import Hazard
from ..risk import RiskLevel, RiskPolicy
from .freshness import ALERT_FRESHNESS, Freshness, freshness, iso, utc_now
from .models import FloodAlert
from .nws import NWS_SOURCE


@dataclass(frozen=True)
class FloodPolicy(RiskPolicy):
    """Routing preferences for alert areas, not evidence of physical inundation.

    Warnings map to HIGH_RISK; watches, advisories and statements to CAUTION.
    A warning alone cannot justify IMPASSABLE. Explicit closures need a separate
    evidence-backed adapter. Base penalties remain fully configurable.
    """
    event_levels: Mapping[str, RiskLevel] = field(default_factory=lambda: {
        f'{prefix} {suffix}': (RiskLevel.HIGH_RISK if suffix == 'Warning' else RiskLevel.CAUTION)
        for prefix in ('Flood', 'Flash Flood', 'Coastal Flood', 'Lakeshore Flood')
        for suffix in ('Warning', 'Watch', 'Advisory', 'Statement')
    })

    def __post_init__(self):
        super().__post_init__()
        levels = {key: RiskLevel(value) for key, value in self.event_levels.items()}
        if any(level == RiskLevel.IMPASSABLE for level in levels.values()):
            raise ValueError('Alert areas alone cannot mark a road IMPASSABLE.')
        object.__setattr__(self, 'event_levels', MappingProxyType(levels))

    def level_for(self, hazard):
        if hazard.hazard_type != 'flood' or hazard.metadata.get('evidence_kind') != 'official_alert_area':
            raise ValueError('FloodPolicy expects hazards from official alert areas.')
        # Unknown flood events remain cautionary, never interpreted as clearance.
        return self.event_levels.get(hazard.metadata.get('event'), RiskLevel.CAUTION)


@dataclass(frozen=True)
class FloodSnapshot:
    hazards: tuple
    alerts: tuple  # serialized records including exclusion/freshness explanations


def prepare_flood_hazards(result, *, now=None, freshness_policy=ALERT_FRESHNESS,
                         include_stale=False, source_confidence=1.0):
    """Pure adaptation: never downloads or fabricates official footprints.

    source_confidence is an explicit integration setting, not an NWS-provided
    flood probability. The official CAP certainty remains unaltered in metadata.
    """
    if result.source != NWS_SOURCE or any(not isinstance(r, FloodAlert) for r in result.records):
        raise ValueError('Expected a parsed NWS SourceResult.')
    now = utc_now() if now is None else now
    superseded = set()
    for alert in result.records:
        if alert.status == 'Actual' and alert.message_type in ('Cancel', 'Update'):
            for reference in alert.metadata.get('references') or []:
                if isinstance(reference, dict):
                    identifier = reference.get('identifier') or reference.get('@id')
                    if isinstance(identifier, str) and identifier:
                        superseded.add(identifier)
    hazards, alerts = [], []
    for alert in result.records:
        state = freshness(observed_at=alert.sent, fetched_at=alert.fetched_at,
                          expires_at=alert.expires_at, effective_at=alert.effective,
                          now=now, policy=freshness_policy)
        reason = None
        if alert.status != 'Actual' or alert.message_type not in ('Alert', 'Update'):
            reason = 'Not an actual active alert/update (test, exercise, or cancellation).'
        elif alert.id in superseded or alert.metadata.get('feature_url') in superseded:
            reason = 'Superseded or cancelled by another record in this snapshot.'
        elif alert.expires_at is None:
            state = Freshness.UNKNOWN
            reason = 'No expiration supplied; cannot establish current applicability.'
        elif state != Freshness.CURRENT and not (include_stale and state == Freshness.STALE):
            reason = f'Alert freshness is {state.value}; omitted from routing.'
        elif alert.geometry is None:
            reason = 'No usable official polygon; retained as context, not spatially mapped.'
        hazard = None
        if reason is None:
            metadata = {**alert.metadata, 'official_alert_id': alert.id,
                        'event': alert.event, 'official_severity': alert.severity,
                        'sent': iso(alert.sent), 'effective': iso(alert.effective),
                        'onset': iso(alert.onset), 'expires': iso(alert.expires), 'ends': iso(alert.ends),
                        'fetched_at': iso(alert.fetched_at), 'freshness': state.value,
                        'data_origin': result.data_origin, 'source_status': result.status,
                        'evidence_kind': 'official_alert_area',
                        'confidence_basis': 'configured source confidence; not a flood probability',
                        'interpretation': 'Alert-area intersection only; does not establish road flooding.'}
            hazard = Hazard(id=f'nws:{alert.id}', hazard_type='flood', geometry=alert.geometry,
                            severity=alert.severity, timestamp=alert.sent, source=alert.source,
                            confidence=source_confidence, metadata=metadata)
            hazards.append(hazard)
        alerts.append({**alert.to_dict(), 'freshness': state.value,
                       'hazard_id': hazard.id if hazard else None,
                       'used_for_routing': hazard is not None, 'exclusion_reason': reason})
    return FloodSnapshot(tuple(hazards), tuple(alerts))
