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
    """Preserve the flood API around shared official CAP lifecycle adaptation."""
    return FloodSnapshot(*prepare_alert_hazards(
        result, hazard_type='flood',
        interpretation='Alert-area intersection only; does not establish road flooding.',
        now=now, freshness_policy=freshness_policy, include_stale=include_stale,
        source_confidence=source_confidence))


def prepare_alert_hazards(result, *, hazard_type, interpretation, now=None,
                          freshness_policy=ALERT_FRESHNESS, include_stale=False, source_confidence=1.0):
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
                        'confidence_basis': f'configured source confidence; not a {hazard_type} probability',
                        'interpretation': interpretation}
            hazard = Hazard(id=f'nws:{alert.id}', hazard_type=hazard_type, geometry=alert.geometry,
                            severity=alert.severity, timestamp=alert.sent, source=alert.source,
                            confidence=source_confidence, metadata=metadata)
            hazards.append(hazard)
        alerts.append({**alert.to_dict(), 'freshness': state.value,
                       'hazard_id': hazard.id if hazard else None,
                       'used_for_routing': hazard is not None, 'exclusion_reason': reason})
    return tuple(hazards), tuple(alerts)


def flood_hazard_batch(alerts, observations=None, *, now=None,
                       alert_freshness=ALERT_FRESHNESS, observation_freshness=None,
                       source_confidence=1.0):
    """Unified adapter result using existing NWS adaptation and freshness checks.

    Preserve official severity in metadata; map event policy to generic severity.
    Include stale alerts as visible, non-authoritative geometric evidence. USGS
    measurements remain context only: gauge height is not road inundation depth.
    """
    from dataclasses import replace
    from ..pipeline import HazardBatch
    from .freshness import OBSERVATION_FRESHNESS
    now = utc_now() if now is None else now
    observation_freshness = (OBSERVATION_FRESHNESS if observation_freshness is None
                             else observation_freshness)
    snapshot = prepare_flood_hazards(alerts, now=now, freshness_policy=alert_freshness,
                                    include_stale=True, source_confidence=source_confidence)
    labels = {RiskLevel.SAFE: 'none', RiskLevel.CAUTION: 'moderate',
              RiskLevel.HIGH_RISK: 'high'}
    policy = FloodPolicy()
    hazards = tuple(replace(h, severity=labels[policy.level_for(h)], metadata={
        **h.metadata, 'freshness_policy': alert_freshness.to_dict(), 'reason': f"Intersects {h.metadata['event']} official alert area; "
                               'does not establish physical road flooding.'}) for h in snapshot.hazards)
    context = [{'kind': 'nws_alert', **a} for a in snapshot.alerts]
    issues = list(alerts.issues)
    degraded = alerts.status != 'available'
    sources = [alerts] + ([observations] if observations is not None else [])
    for source in sources:
        age_policy = alert_freshness if source is alerts else observation_freshness
        state = freshness(observed_at=source.fetched_at, fetched_at=source.fetched_at,
                          now=now, policy=age_policy).value
        context.append({'kind': 'source', **{k: v for k, v in source.to_dict().items()
                                            if k != 'records'}, 'freshness': state})
        if source.status != 'available' or state != 'current':
            degraded = True
            issues.append(f'{source.source}: status={source.status}, freshness={state}')
    if observations is not None:
        issues.extend(observations.issues)
        for observation in observations.records:
            state = freshness(observed_at=observation.observed_at, fetched_at=observation.fetched_at,
                              expires_at=observation.expires_at, now=now,
                              policy=observation_freshness).value
            context.append({'kind': 'water_observation', **observation.to_dict(), 'freshness': state,
                            'used_for_routing': False})
            if state != 'current':
                degraded = True
                issues.append(f'{observation.id}: {state} observation; context only, not authoritative.')
    if any(a['freshness'] not in ('current', 'expired') or
           (a['freshness'] == 'current' and not a['used_for_routing']) for a in snapshot.alerts):
        degraded = True
        issues.append('Some alert evidence is stale, unknown, future, or unmappable; inspect context.')
    removed = tuple(f'nws:{a["id"]}' for a in snapshot.alerts
                    if a['freshness'] == 'expired' or
                    (a['status'] == 'Actual' and a['message_type'] == 'Cancel') or
                    (a['exclusion_reason'] or '').startswith('Superseded or cancelled'))
    status = ('unavailable' if alerts.status == 'unavailable' and observations is None
              else 'partial' if degraded else 'available')
    return HazardBatch(hazards, status, tuple(issues),
                       tuple(context), {'nws_query': dict(alerts.query),
                                        'notice': 'Requested source scope; completeness is not guaranteed.'},
                       fetched_at=alerts.fetched_at, attempted_at=alerts.attempted_at,
                       data_origin=alerts.data_origin, freshness_policy=alert_freshness.to_dict(),
                       removed_hazard_ids=removed)



def registered_flood_adapters(coverage, *, nws_client=None, usgs_client=None,
                               nws_area='NC', gauge_radius_m=25000,
                               alert_freshness=ALERT_FRESHNESS, observation_freshness=None):
    """Independent NWS/USGS batches for the generic RefreshService registry.

    Only refresh invokes these callables. Offline loading never touches clients.
    USGS remains context-only. Uses the existing source clients and adaptation.
    """
    from ..pipeline import HazardBatch
    from .nws import NWSClient
    from .usgs import USGSClient
    from .freshness import OBSERVATION_FRESHNESS
    observation_freshness = observation_freshness or OBSERVATION_FRESHNESS
    nws_client = nws_client or NWSClient()
    usgs_client = usgs_client or USGSClient()

    def nws(now):
        alerts = nws_client.fetch(area=nws_area)
        return flood_hazard_batch(alerts, now=now, alert_freshness=alert_freshness)

    def usgs(now):
        result = usgs_client.fetch(point=coverage.center, radius_m=gauge_radius_m)
        context = [{'kind': 'source', **{k: v for k, v in result.to_dict().items() if k != 'records'}}]
        context.extend({'kind': 'water_observation', **o.to_dict(), 'used_for_routing': False,
                        'freshness_policy': observation_freshness.to_dict(),
                        'freshness': freshness(observed_at=o.observed_at, fetched_at=o.fetched_at,
                            expires_at=o.expires_at, now=now, policy=observation_freshness).value}
                       for o in result.records)
        return HazardBatch(status=result.status, issues=result.issues, context=tuple(context),
                           coverage={'usgs_query': dict(result.query)}, fetched_at=result.fetched_at,
                           attempted_at=result.attempted_at, data_origin=result.data_origin,
                           freshness_policy=observation_freshness.to_dict())
    return {'nws': nws, 'usgs': usgs}
