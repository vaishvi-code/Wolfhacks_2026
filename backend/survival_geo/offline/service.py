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
                 observation_freshness=OBSERVATION_FRESHNESS, include_stale=False):
        self.store, self.coverage = store, coverage
        self.nws_client = nws_client if nws_client is not None else NWSClient()
        self.usgs_client = usgs_client if usgs_client is not None else USGSClient()
        self.nws_area, self.gauge_radius_m = nws_area, gauge_radius_m
        self.options = dict(alert_freshness=alert_freshness, observation_freshness=observation_freshness,
                            include_stale=include_stale)

    def _read(self):
        try:
            return self.store.load(self.coverage), None
        except CacheError as exc:
            return None, exc.to_dict()

    def load_offline(self, *, now=None):
        now = utc_now() if now is None else now
        snapshot, error = self._read()
        sources = ({name: replace(source, data_origin='cached')
                    if source.fetched_at is not None else source
                    for name, source in snapshot.sources.items()} if snapshot else _empty_sources(now))
        return DataState(self.coverage, sources, now, snapshot=snapshot,
                         record_origins={name: {r.id: 'cached' for r in s.records} for name, s in sources.items()},
                         cache_status='LOADED' if snapshot else error['code'], cache_error=error, **self.options)

    def refresh(self, *, now=None):
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
