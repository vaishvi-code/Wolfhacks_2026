"""Explicit data provenance/freshness at evaluation time, not connectivity guesses."""
from dataclasses import dataclass, field

from ..flood.adapter import prepare_flood_hazards
from ..flood.freshness import ALERT_FRESHNESS, OBSERVATION_FRESHNESS, freshness, iso
from ..flood.models import FloodAlert
from .snapshot import SCHEMA_VERSION


def record_freshness(record, now, policy):
    if isinstance(record, FloodAlert):
        if record.expires_at is None:
            return 'unknown'
        return freshness(observed_at=record.sent, fetched_at=record.fetched_at,
                         expires_at=record.expires_at, effective_at=record.effective,
                         now=now, policy=policy).value
    return freshness(observed_at=record.observed_at, fetched_at=record.fetched_at,
                     expires_at=record.expires_at, now=now, policy=policy).value


def data_label(origin, value):
    if value == 'expired':
        return 'EXPIRED'
    if origin == 'none':
        return 'UNAVAILABLE'
    return f'{origin.upper()}_{value.upper()}'


@dataclass(frozen=True)
class DataState:
    coverage: object
    sources: dict
    evaluated_at: object
    snapshot: object = None
    attempts: dict = field(default_factory=dict)
    record_origins: dict = field(default_factory=dict)
    cache_status: str = 'NOT_WRITTEN'
    cache_error: object = None
    alert_freshness: object = ALERT_FRESHNESS
    observation_freshness: object = OBSERVATION_FRESHNESS
    include_stale: bool = False

    @property
    def mode(self):
        origins = {s.data_origin for s in self.sources.values() if s.fetched_at is not None}
        if not origins:
            return 'UNAVAILABLE'
        if origins <= {'cached', 'local'}:
            return 'CACHED'
        if origins == {'live'} and all(s.fetched_at is not None for s in self.sources.values()):
            return 'LIVE'
        return 'MIXED'

    @property
    def hazards(self):
        return prepare_flood_hazards(self.sources['nws'], now=self.evaluated_at,
                                   freshness_policy=self.alert_freshness,
                                   include_stale=self.include_stale).hazards

    def to_dict(self):
        sources = {}
        for name, source in self.sources.items():
            policy = self.alert_freshness if name == 'nws' else self.observation_freshness
            origin = source.data_origin
            records = []
            for record in source.records:
                record_origin = self.record_origins.get(name, {}).get(record.id, origin)
                value = record_freshness(record, self.evaluated_at, policy)
                records.append({**record.to_dict(), 'data_origin': record_origin, 'freshness': value,
                                'data_state': data_label(record_origin, value),
                                'fetch_age_seconds': (self.evaluated_at - record.fetched_at).total_seconds()})
            fetch_state = freshness(observed_at=source.fetched_at, fetched_at=source.fetched_at,
                                    now=self.evaluated_at, policy=policy).value
            labels = {r['data_state'] for r in records}
            summary = (next(iter(labels)) if len(labels) == 1 else 'MIXED') if records else data_label(origin, fetch_state)
            sources[name] = {**source.to_dict(), 'records': records, 'data_state': summary,
                             'fetch_freshness': fetch_state,
                             'fetch_age_seconds': ((self.evaluated_at - source.fetched_at).total_seconds()
                                                   if source.fetched_at else None),
                             'freshness_policy': policy.to_dict()}
        return {'schema_version': SCHEMA_VERSION, 'mode': self.mode, 'evaluated_at': iso(self.evaluated_at),
                'snapshot_id': self.snapshot.id if self.snapshot else None,
                'snapshot_created_at': iso(self.snapshot.created_at) if self.snapshot else None,
                'snapshot_age_seconds': ((self.evaluated_at - self.snapshot.created_at).total_seconds()
                                         if self.snapshot else None),
                'coverage': self.coverage.to_dict(), 'sources': sources,
                'sources_represented': [name for name, s in self.sources.items() if s.fetched_at is not None],
                'attempts': self.attempts,
                'unavailable_during_attempt': [name for name, attempt in self.attempts.items()
                                               if attempt['status'] == 'unavailable'],
                'cache_status': self.cache_status, 'cache_error': self.cache_error,
                'snapshot_persisted': self.snapshot is not None and self.cache_status in ('SAVED', 'LOADED', 'PRESERVED'),
                'hazards': [h.to_dict() for h in self.hazards], 'include_stale': self.include_stale,
                'notice': 'Data availability and policy passability do not establish physical road safety.'}
