"""Schema-2 codec and batch freshness helpers for the existing offline package.

No new Hazard, Risk, Snapshot, or DataState models. Source-independent timing
rules reuse the existing freshness utility, including inclusive expiration.
"""
from dataclasses import replace
from collections import Counter
from datetime import timedelta
import json
from uuid import uuid4

from shapely.errors import ShapelyError

from ..hazards import validate_timestamp
from ..pipeline import HazardBatch, normalize_hazard
from ..flood.freshness import FreshnessPolicy, freshness, iso, parse_time
from .snapshot import CacheError, Coverage, Snapshot

FRESHNESS_VALUES = {'current', 'stale', 'expired', 'unknown', 'not_yet_active'}
ORIGINS = {'live', 'cached', 'local', 'mixed', 'none'}


def policy_from_dict(data):
    if not isinstance(data, dict):
        raise ValueError('Freshness policy must be an object.')
    allowed = {'max_fetch_age_seconds', 'max_observation_age_seconds', 'future_clock_tolerance_seconds'}
    if set(data) - allowed:
        raise ValueError('Unknown freshness policy fields.')
    def duration(key, default):
        value = data.get(key, default)
        if value is None and key == 'max_observation_age_seconds':
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError('Freshness limits must be numeric seconds.')
        return timedelta(seconds=value)
    return FreshnessPolicy(max_fetch_age=duration('max_fetch_age_seconds', 900),
                           max_observation_age=duration('max_observation_age_seconds', None),
                           future_clock_tolerance=duration('future_clock_tolerance_seconds', 120))


def _json(value):
    return json.loads(json.dumps(value, allow_nan=False))


def normalize_batch(batch, *, now):
    """Skip invalid records while turning incomplete input into a partial batch."""
    if not isinstance(batch, HazardBatch):
        raise ValueError('Adapter must return HazardBatch.')
    if batch.status not in ('available', 'partial', 'unavailable') or batch.data_origin not in ORIGINS:
        raise ValueError('Invalid adapter status/origin.')
    validate_timestamp(now)
    for value in (batch.fetched_at, batch.attempted_at):
        validate_timestamp(value)
    if not isinstance(batch.coverage, dict) or not isinstance(batch.hazard_origins, dict):
        raise ValueError('Batch coverage and hazard origins must be objects.')
    policy_from_dict(dict(batch.freshness_policy))
    coverage, context = _json(dict(batch.coverage)), _json(list(batch.context))
    if any(not isinstance(item, dict) for item in context):
        raise ValueError('Context entries must be objects.')
    if any(not isinstance(issue, str) for issue in batch.issues):
        raise ValueError('Batch issues must be strings.')
    if any(not isinstance(id, str) or not id.strip() for id in batch.removed_hazard_ids):
        raise ValueError('Removal IDs must be nonempty text.')
    if any(value not in ORIGINS for value in batch.hazard_origins.values()):
        raise ValueError('Invalid hazard origin.')
    hazards, issues = [], list(batch.issues)
    invalid = False
    for index, raw in enumerate(batch.hazards):
        try:
            hazard = normalize_hazard(raw)
            if hazard.metadata.get('freshness', 'unknown') not in FRESHNESS_VALUES:
                raise ValueError('Invalid freshness state.')
            # Time fields must round-trip and cannot bypass expiry with bad strings.
            for key in ('fetched_at', 'expires_at', 'expires', 'ends', 'effective_at', 'effective'):
                if key in hazard.metadata:
                    parse_time(hazard.metadata[key])
            policy_from_dict(hazard.metadata.get('freshness_policy', dict(batch.freshness_policy)))
            if 'fetched_at' not in hazard.metadata and batch.fetched_at is not None:
                hazard = replace(hazard, metadata={**hazard.metadata, 'fetched_at': iso(batch.fetched_at)})
            hazards.append(hazard)
        except (ValueError, TypeError, KeyError, AttributeError, ShapelyError, OverflowError) as exc:
            invalid = True
            issues.append(f'INVALID_HAZARD[{index}]: {exc}')
    duplicates = {id for id, count in Counter(h.id for h in hazards).items() if count > 1}
    if duplicates:
        invalid = True
        issues.append(f'DUPLICATE_HAZARDS: {sorted(duplicates)}')
        hazards = [h for h in hazards if h.id not in duplicates]
    return replace(batch, hazards=tuple(sorted(hazards, key=lambda h: h.id)),
                   status='partial' if invalid and batch.status != 'unavailable' else batch.status,
                   issues=tuple(issues), context=tuple(context), coverage=coverage,
                   freshness_policy=dict(batch.freshness_policy), hazard_origins=dict(batch.hazard_origins))


def hazard_freshness(hazard, batch, now):
    metadata = hazard.metadata
    try:
        expiry = min((parse_time(metadata[key]) for key in ('expires_at', 'expires', 'ends')
                      if metadata.get(key) is not None), default=None)
        effective = parse_time(metadata.get('effective_at', metadata.get('effective')))
        fetched = parse_time(metadata['fetched_at']) if 'fetched_at' in metadata else batch.fetched_at
        value = freshness(observed_at=hazard.timestamp, fetched_at=fetched, expires_at=expiry,
                          effective_at=effective, now=now,
                          policy=policy_from_dict(metadata.get('freshness_policy', dict(batch.freshness_policy)))).value
        declared = metadata.get('freshness', 'unknown')
        # No cache load can rejuvenate previously stale/unknown/expired evidence.
        if value == 'current' and declared in ('stale', 'expired', 'unknown'):
            return declared
        return value
    except (TypeError, ValueError, OverflowError):
        return 'unknown'


def evaluated_batch(batch, now, *, cached=False):
    """Recompute times without overwriting original observation/fetch timestamps."""
    origins, hazards = {}, []
    for hazard in batch.hazards:
        origin = 'cached' if cached else batch.hazard_origins.get(hazard.id, batch.data_origin)
        origins[hazard.id] = origin
        value = hazard_freshness(hazard, batch, now)
        from .state import data_label
        hazards.append(replace(hazard, metadata={**hazard.metadata, 'freshness': value,
            'data_origin': origin, 'data_state': data_label(origin, value)}))
    context = []
    for raw in batch.context:
        item = dict(raw)
        origin = 'cached' if cached else item.get('data_origin', batch.data_origin)
        item['data_origin'] = origin
        try:
            if any(key in item for key in ('observed_at', 'sent', 'fetched_at')):
                observed = parse_time(item.get('observed_at', item.get('sent', item.get('fetched_at'))))
                fetched = parse_time(item['fetched_at']) if 'fetched_at' in item else batch.fetched_at
                expiry = min((parse_time(item[k]) for k in ('expires_at', 'expires', 'ends')
                              if item.get(k) is not None), default=None)
                value = freshness(observed_at=observed, fetched_at=fetched, expires_at=expiry,
                                  effective_at=parse_time(item.get('effective')), now=now,
                                  policy=policy_from_dict(item.get('freshness_policy', dict(batch.freshness_policy)))).value
                if value == 'current' and item.get('freshness', 'current') != 'current':
                    value = item['freshness']
                item['freshness'] = value
                from .state import data_label
                item['data_state'] = data_label(origin, value)
        except (TypeError, ValueError, OverflowError):
            item.update(freshness='unknown', data_state=f'{origin.upper()}_UNKNOWN')
        context.append(item)
    limited = any(item.get('freshness') in ('stale', 'unknown', 'not_yet_active') for item in context)
    return replace(batch, status='partial' if limited and batch.status == 'available' else batch.status,
                   hazards=tuple(hazards), context=tuple(context), hazard_origins=origins,
                   data_origin='cached' if cached and batch.fetched_at is not None else batch.data_origin)


def coverage_state(sources):
    current = any(h.metadata.get('freshness') == 'current' and h.source != 'unknown'
                  for batch in sources.values() for h in batch.hazards)
    if not current:
        return 'unavailable'
    limited = any(batch.status != 'available' or batch.issues
                  or any(h.metadata.get('freshness') != 'current' for h in batch.hazards)
                  for batch in sources.values())
    return 'incomplete' if limited else 'available'


def make_snapshot(coverage, sources, *, now, snapshot_id=None, warnings=()):
    validate_timestamp(now)
    if now is None or not isinstance(coverage, Coverage):
        raise ValueError('Snapshot requires coverage and creation timestamp.')
    if not isinstance(sources, dict) or any(not isinstance(name, str) or not name.strip() for name in sources):
        raise ValueError('Sources must be keyed by adapter name.')
    prepared = {name: evaluated_batch(normalize_batch(batch, now=now), now)
                for name, batch in sorted(sources.items())}
    # Cross-adapter ID collisions are not silently attributed to either source.
    owners = {}
    for name, batch in prepared.items():
        for hazard in batch.hazards:
            owners.setdefault(hazard.id, []).append(name)
    conflicts = {id for id, names in owners.items() if len(names) > 1}
    if conflicts:
        prepared = {name: replace(batch, hazards=tuple(h for h in batch.hazards if h.id not in conflicts),
                     status='partial', issues=batch.issues + (f'DUPLICATE_HAZARDS: {sorted(conflicts)}',))
                    for name, batch in prepared.items()}
    hazards = tuple(h for batch in prepared.values() for h in batch.hazards)
    return Snapshot(snapshot_id or str(uuid4()), now, coverage, prepared, hazards, schema_version=2,
                    warnings=tuple(_json(list(warnings))), hazard_coverage_state=coverage_state(prepared))


def batch_to_dict(batch):
    return {'status': batch.status, 'issues': list(batch.issues), 'context': list(batch.context),
            'coverage': dict(batch.coverage), 'fetched_at': iso(batch.fetched_at),
            'attempted_at': iso(batch.attempted_at), 'data_origin': batch.data_origin,
            'hazard_origins': dict(batch.hazard_origins), 'freshness_policy': dict(batch.freshness_policy),
            'removed_hazard_ids': list(batch.removed_hazard_ids)}


def snapshot_to_dict(snapshot):
    return {'schema_version': 2, 'snapshot_id': snapshot.id, 'created_at': iso(snapshot.created_at),
            'coverage': snapshot.coverage.to_dict(), 'sources': {
                name: batch_to_dict(batch) for name, batch in snapshot.sources.items()},
            'hazards': [{'adapter': name, **h.to_dict()} for name, batch in snapshot.sources.items()
                        for h in batch.hazards], 'warnings': list(snapshot.warnings),
            'hazard_coverage_state': snapshot.hazard_coverage_state}


def snapshot_from_dict(data):
    """Bad envelope fails; bad individual hazards/sources preserve valid records."""
    try:
        if not isinstance(data.get('sources'), dict) or not isinstance(data.get('hazards'), list):
            raise ValueError('Snapshot requires sources object and hazards list.')
        created = parse_time(data['created_at'])
        if created is None:
            raise ValueError('Snapshot timestamp is required.')
        coverage = Coverage(**data['coverage'])
        snapshot_id = data['snapshot_id']
        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            raise ValueError('Invalid snapshot ID.')
        warnings = data.get('warnings', [])
        if not isinstance(warnings, list) or any(not isinstance(w, dict) for w in warnings):
            raise ValueError('Warnings must be objects.')
        sources = {}
        for name, raw in data['sources'].items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError('Invalid adapter name.')
            try:
                values = dict(raw)
                values['fetched_at'] = parse_time(values.get('fetched_at'))
                values['attempted_at'] = parse_time(values.get('attempted_at'))
                sources[name] = normalize_batch(HazardBatch(**values), now=created)
            except (TypeError, ValueError, KeyError, AttributeError, OverflowError) as exc:
                sources[name] = HazardBatch(status='unavailable', data_origin='none',
                                             issues=(f'INVALID_SOURCE: {exc}',))
                warnings = warnings + [{'code': 'INVALID_SOURCE', 'adapter': name}]
        rows = {name: [] for name in sources}
        for index, raw in enumerate(data['hazards']):
            if not isinstance(raw, dict) or raw.get('adapter') not in sources:
                warnings = warnings + [{'code': 'INVALID_HAZARD_OWNER', 'record_index': index}]
                continue
            rows[raw['adapter']].append({k: v for k, v in raw.items() if k != 'adapter'})
        sources = {name: replace(batch, hazards=tuple(rows[name])) for name, batch in sources.items()}
        # Derive coverage/freshness anew; a saved 'available' label is not trusted.
        snapshot = make_snapshot(coverage, sources, now=created, snapshot_id=snapshot_id, warnings=warnings)
        if warnings and snapshot.hazard_coverage_state == 'available':
            snapshot = replace(snapshot, hazard_coverage_state='incomplete')
        return snapshot
    except Exception as exc:
        raise CacheError('MALFORMED', f'Invalid normalized snapshot: {exc}') from exc
