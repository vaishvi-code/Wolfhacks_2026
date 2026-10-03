"""Parsed source records retain provenance without implying road flooding."""
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Mapping, Optional, Tuple

from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from .freshness import iso


@dataclass(frozen=True)
class FloodAlert:
    id: str
    event: str
    severity: str
    geometry: Optional[BaseGeometry]
    sent: Optional[datetime]
    effective: Optional[datetime]
    onset: Optional[datetime]
    expires: Optional[datetime]
    ends: Optional[datetime]
    fetched_at: datetime
    source: str
    status: str
    message_type: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def expires_at(self):
        return min((t for t in (self.expires, self.ends) if t is not None), default=None)

    def to_dict(self):
        return {'id': self.id, 'event': self.event, 'severity': self.severity,
                'geometry': mapping(self.geometry) if self.geometry is not None else None,
                'sent': iso(self.sent), 'effective': iso(self.effective), 'onset': iso(self.onset),
                'expires': iso(self.expires), 'ends': iso(self.ends),
                'fetched_at': iso(self.fetched_at), 'source': self.source,
                'status': self.status, 'message_type': self.message_type,
                'metadata': dict(self.metadata)}


@dataclass(frozen=True)
class WaterObservation:
    id: str
    site_id: str
    parameter_code: str
    unit: str
    value: float
    raw_value: str
    observed_at: datetime
    fetched_at: datetime
    geometry: Optional[BaseGeometry]
    source: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    expires_at: Optional[datetime] = None

    def to_dict(self):
        return {'id': self.id, 'site_id': self.site_id, 'parameter_code': self.parameter_code,
                'unit': self.unit, 'value': self.value, 'raw_value': self.raw_value,
                'observed_at': iso(self.observed_at), 'fetched_at': iso(self.fetched_at),
                'expires_at': iso(self.expires_at), 'source': self.source,
                'geometry': mapping(self.geometry) if self.geometry is not None else None,
                'metadata': dict(self.metadata), 'role': 'context_only'}


@dataclass(frozen=True)
class SourceResult:
    source: str
    endpoint: str
    status: str  # available, partial, unavailable
    records: Tuple[Any, ...]
    fetched_at: Optional[datetime]
    attempted_at: datetime
    query: Mapping[str, Any] = field(default_factory=dict)
    issues: Tuple[str, ...] = ()
    data_origin: str = 'live'  # live, local, cached, none

    def to_dict(self):
        return {'source': self.source, 'endpoint': self.endpoint, 'status': self.status,
                'records': [record.to_dict() for record in self.records],
                'fetched_at': iso(self.fetched_at), 'attempted_at': iso(self.attempted_at),
                'query': dict(self.query), 'issues': list(self.issues),
                'data_origin': self.data_origin}


def use_local_fallback(unavailable, local):
    """Explicit caller-managed fallback, preserving old fetch time and outage.

    Scope must match; this does not fetch, save, merge, or refresh cached records.
    """
    if unavailable.status != 'unavailable':
        return unavailable
    if (local.source != unavailable.source or local.endpoint != unavailable.endpoint
            or dict(local.query) != dict(unavailable.query)):
        raise ValueError('Fallback source and query scope must match the failed request.')
    return replace(unavailable, records=local.records, fetched_at=local.fetched_at,
                   data_origin='cached', issues=unavailable.issues + local.issues + (
                       'Using explicitly supplied local data; source remains unavailable.',))
