"""Explicit refresh and offline load; no probing, timers, or background network."""
from dataclasses import replace

from ..flood.freshness import ALERT_FRESHNESS, OBSERVATION_FRESHNESS, utc_now
from ..flood.models import SourceResult, use_local_fallback
from ..flood.nws import NWSClient
from ..flood.usgs import USGSClient
from .snapshot import CacheError, SOURCE_DEFINITIONS, make_snapshot
from .state import DataState


def _empty_sources(now):
    return {key: SourceResult(source, endpoint, 'unavailable', (), None, now,
                              issues=('No source snapshot is available.',), data_origin='none')
            for key, (source, endpoint) in SOURCE_DEFINITIONS.items()}


def _scope_matches(left, right):
    return (left.source, left.endpoint, dict(left.query)) == (right.source, right.endpoint, dict(right.query))


def merge_source(live, cached):
    """Complete results replace. Partial results upsert without inferred deletion.

    Cached rows retain their individual timestamps. Observation upserts use
    time-series identity so a new observation replaces an older one in that series.
    """
    if cached is None or cached.fetched_at is None or not _scope_matches(live, cached):
        return live, {r.id: 'live' for r in live.records}
    if live.status == 'available':
        return live, {r.id: 'live' for r in live.records}
    if live.status == 'unavailable':
        merged = use_local_fallback(live, cached)
        return merged, {r.id: 'cached' for r in cached.records}
    def key(record):
        series = record.metadata.get('time_series_id')
        return ('series', series) if hasattr(record, 'observed_at') and series else ('id', record.id)
    rows = {key(r): r for r in cached.records}
    origins = {r.id: 'cached' for r in cached.records}
    for row in live.records:
        previous = rows.get(key(row))
        # A delayed/older gauge reading cannot erase a newer cached reading.
        if (previous is not None and hasattr(row, 'observed_at')
                and row.observed_at < previous.observed_at):
            continue
        if previous is not None:
            origins.pop(previous.id, None)
        rows[key(row)] = row
        origins[row.id] = 'live'
    has_cached = 'cached' in origins.values() or (not rows and cached.fetched_at is not None)
    merged = replace(live, records=tuple(rows.values()),
                     fetched_at=min(live.fetched_at, cached.fetched_at) if has_cached else live.fetched_at,
                     data_origin=('mixed' if 'live' in origins.values() else 'cached') if has_cached else 'live',
                     issues=live.issues + ('Partial refresh: retained cached records; absence is not deletion.',))
    return merged, origins


class RefreshService:
    def __init__(self, store, coverage, *, nws_client=None, usgs_client=None,
                 nws_area='NC', gauge_radius_m=25000, alert_freshness=ALERT_FRESHNESS,
                 observation_freshness=OBSERVATION_FRESHNESS, include_stale=False, adapters=None):
        self.store, self.coverage = store, coverage
        # None preserves the legacy flood API; {} is an explicit generic registry.
        self.adapters = None if adapters is None else dict(adapters)
        self.nws_client = nws_client if nws_client is not None else NWSClient()
        self.usgs_client = usgs_client if usgs_client is not None else USGSClient()
        self.nws_area, self.gauge_radius_m = nws_area, gauge_radius_m
        self.options = dict(alert_freshness=alert_freshness, observation_freshness=observation_freshness,
                            include_stale=include_stale)

    def _read(self):
        try:
            snapshot = self.store.load(self.coverage)
            expected = 1 if self.adapters is None else 2
            if snapshot.schema_version != expected:
                raise CacheError('INCOMPATIBLE_VERSION', 'Configured service expects a different snapshot schema.')
            return snapshot, None
        except CacheError as exc:
            return None, exc.to_dict()

    def load_offline(self, *, now=None):
        if self.adapters is not None:
            return self._load_normalized(now=now)
        now = utc_now() if now is None else now
        snapshot, error = self._read()
        sources = ({name: replace(source, data_origin='cached')
                    if source.fetched_at is not None else source
                    for name, source in snapshot.sources.items()} if snapshot else _empty_sources(now))
        return DataState(self.coverage, sources, now, snapshot=snapshot,
                         record_origins={name: {r.id: 'cached' for r in s.records} for name, s in sources.items()},
                         cache_status='LOADED' if snapshot else error['code'], cache_error=error, **self.options)

    def refresh(self, *, now=None):
        if self.adapters is not None:
            return self._refresh_normalized(now=now)
        cached, read_error = self._read()
        # Actual source attempts provide connectivity evidence; no fake detector.
        live = {'nws': self.nws_client.fetch(area=self.nws_area),
                'usgs': self.usgs_client.fetch(point=self.coverage.center, radius_m=self.gauge_radius_m)}
        now = utc_now() if now is None else now
        sources, origins, attempts = {}, {}, {}
        for name, result in live.items():
            previous = cached.sources[name] if cached else None
            sources[name], origins[name] = merge_source(result, previous)
            attempts[name] = {'status': result.status, 'attempted_at': result.attempted_at.isoformat(),
                              'issues': list(result.issues)}
            if previous and not _scope_matches(result, previous):
                attempts[name]['issues'].append('Cached query scope differs; fallback was not used.')
        # Empty AVAILABLE responses are useful: they can retire old active alerts.
        # Empty PARTIAL responses and total failures cannot overwrite good state.
        usable_update = any(s.status == 'available' or (s.status == 'partial' and s.records)
                            for s in live.values())
        snapshot, cache_status, error = cached, 'PRESERVED' if cached else 'NOT_WRITTEN', read_error
        if usable_update:
            snapshot = make_snapshot(self.coverage, sources, now=now)
            try:
                self.store.save(snapshot)
                cache_status = 'SAVED'
                error = None
            except CacheError as exc:
                cache_status, error = 'WRITE_FAILED', exc.to_dict()
        return DataState(self.coverage, sources, now, snapshot=snapshot, attempts=attempts,
                         record_origins=origins, cache_status=cache_status, cache_error=error, **self.options)

    def refresh_and_reevaluate(self, graph, route, previous_state, *, now=None, **kwargs):
        from .routing import reevaluate_route
        current = self.refresh(now=now)
        return current, reevaluate_route(graph, route, previous_state, current, **kwargs)


    def _load_normalized(self, *, now=None):
        from ..pipeline import HazardBatch
        from .normalized import evaluated_batch
        now = utc_now() if now is None else now
        cached, error = self._read()
        sources = ({name: evaluated_batch(batch, now, cached=True) for name, batch in cached.sources.items()}
                   if cached else {name: HazardBatch(status='unavailable', data_origin='none',
                       issues=('No usable local snapshot.',)) for name in sorted(self.adapters)})
        return DataState(self.coverage, sources, now, snapshot=cached, generic=True,
                         cache_status='LOADED' if cached else error['code'], cache_error=error)

    def _refresh_normalized(self, *, now=None):
        from ..pipeline import HazardBatch
        from .normalized import evaluated_batch, normalize_batch
        from .snapshot import make_hazard_snapshot
        now = utc_now() if now is None else now
        cached, read_error = self._read()
        sources, attempts = {}, {}
        usable_update = False
        for name, adapter in sorted(self.adapters.items()):
            try:
                live = normalize_batch(adapter(now), now=now)
                # Returning data without its fetch time cannot make old data live.
                live = replace(live, attempted_at=now)
            except Exception as exc:
                live = HazardBatch(status='unavailable', attempted_at=now, data_origin='none',
                                   issues=(f'ADAPTER_FAILED: {type(exc).__name__}: {exc}',))
            attempts[name] = {'status': live.status, 'attempted_at': now.isoformat(), 'issues': list(live.issues)}
            previous = cached.sources.get(name) if cached else None
            if previous:
                previous = evaluated_batch(previous, now, cached=True)
            sources[name] = merge_hazard_batch(live, previous)
            usable_update |= (live.status == 'available' and live.fetched_at is not None
                              or live.status == 'partial' and bool(live.hazards or live.context)
                              and live.fetched_at is not None)
        # Unregistered sources are retained as cached, with explicit limited coverage.
        if cached:
            for name, batch in cached.sources.items():
                if name not in sources:
                    sources[name] = replace(evaluated_batch(batch, now, cached=True), status='partial',
                                             issues=batch.issues + ('Adapter not registered; cached evidence only.',))
        snapshot, cache_status, error = cached, 'PRESERVED' if cached else 'NOT_WRITTEN', read_error
        if usable_update:
            warnings = tuple({'code': 'ADAPTER_REFRESH_DEGRADED', 'adapter': name, **attempt}
                             for name, attempt in attempts.items() if attempt['status'] != 'available' or attempt['issues'])
            snapshot = make_hazard_snapshot(self.coverage, sources, now=now, warnings=warnings)
            sources = snapshot.sources
            try:
                self.store.save(snapshot)
                cache_status, error = 'SAVED', None
            except CacheError as exc:
                cache_status, error = 'WRITE_FAILED', exc.to_dict()
        return DataState(self.coverage, sources, now, snapshot=snapshot, generic=True,
                         attempts=attempts, cache_status=cache_status, cache_error=error)


def merge_hazard_batch(live, cached):
    """Complete scope replaces; partial scope upserts; outages retain old times.

    Registry names identify stable sources. Coverage/query metadata must match
    before fallback, preventing cached observations from another scope leaking in.
    """
    if cached is None:
        return live
    # Exceptions have no query metadata; reuse configured registry identity.
    if live.coverage and dict(live.coverage) != dict(cached.coverage):
        return replace(live, issues=live.issues + ('Cached scope differs; no fallback used.',))
    if live.status == 'available':
        return live
    if live.status == 'unavailable':
        return replace(cached, status='unavailable', attempted_at=live.attempted_at,
                       issues=live.issues + cached.issues + ('Refresh failed; cached evidence retained.',))
    hazards = {h.id: h for h in cached.hazards}
    origins = {h.id: 'cached' for h in cached.hazards}
    for hazard in live.hazards:
        previous = hazards.get(hazard.id)
        if previous and previous.timestamp and hazard.timestamp and hazard.timestamp < previous.timestamp:
            continue
        hazards[hazard.id] = hazard
        origins[hazard.id] = live.hazard_origins.get(hazard.id, live.data_origin)
    for id in live.removed_hazard_ids:
        hazards.pop(id, None)
        origins.pop(id, None)
    def context_key(item):
        metadata = item.get('metadata') if isinstance(item.get('metadata'), dict) else {}
        import json
        identity = metadata.get('time_series_id') or item.get('id') or item.get('endpoint')
        if identity is None:
            identity = {k: v for k, v in item.items() if k not in ('data_origin', 'data_state')}
        return json.dumps([item.get('kind'), item.get('source'), identity], sort_keys=True)
    context = {context_key(item): dict(item, data_origin='cached') for item in cached.context}
    for item in live.context:
        previous = context.get(context_key(item))
        if previous and item.get('observed_at') and previous.get('observed_at'):
            from ..flood.freshness import parse_time
            try:
                if parse_time(item['observed_at']) < parse_time(previous['observed_at']):
                    continue
            except (TypeError, ValueError):
                pass  # evaluated_batch keeps malformed timing visibly unknown.
        context[context_key(item)] = dict(item, data_origin=item.get('data_origin', live.data_origin))
    retained = 'cached' in origins.values() or any(item.get('data_origin') == 'cached' for item in context.values())
    times = [t for t in (live.fetched_at, cached.fetched_at if retained else None) if t is not None]
    return replace(live, hazards=tuple(hazards[key] for key in sorted(hazards)), hazard_origins=origins,
                   context=tuple(context.values()), fetched_at=min(times) if times else None,
                   data_origin='mixed' if retained and live.fetched_at else ('cached' if retained else live.data_origin),
                   issues=live.issues + ('Partial refresh; cached records retained, absence is not deletion.',))
