"""Versioned Snapshot model: legacy source records and generic hazard batches."""
from dataclasses import dataclass
from datetime import datetime
import json
import math
from typing import Mapping, Tuple
from uuid import uuid4

from shapely.geometry import shape

from ..hazards import Hazard, validate_geometry, validate_timestamp
from ..flood.adapter import prepare_flood_hazards
from ..flood.freshness import iso, parse_time
from ..flood.models import FloodAlert, SourceResult, WaterObservation
from ..flood.nws import NWS_ENDPOINT, NWS_SOURCE
from ..flood.usgs import USGS_ENDPOINT, USGS_SOURCE

SCHEMA_VERSION = 1
SOURCE_DEFINITIONS = {'nws': (NWS_SOURCE, NWS_ENDPOINT), 'usgs': (USGS_SOURCE, USGS_ENDPOINT)}


class CacheError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

    def to_dict(self):
        return {'code': self.code, 'message': str(self)}


@dataclass(frozen=True)
class Coverage:
    region_id: str
    bbox: Tuple[float, float, float, float]  # west, south, east, north
    crs: str = 'EPSG:4326'

    def __post_init__(self):
        if not isinstance(self.region_id, str) or not self.region_id.strip():
            raise ValueError('Coverage requires a region identifier.')
        bounds = tuple(float(value) for value in self.bbox)
        if len(bounds) != 4 or not all(math.isfinite(v) for v in bounds):
            raise ValueError('Coverage requires four finite bounds.')
        west, south, east, north = bounds
        if self.crs != 'EPSG:4326' or not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError('Expected a non-wrapping EPSG:4326 coverage box.')
        object.__setattr__(self, 'bbox', bounds)

    @property
    def center(self):
        west, south, east, north = self.bbox
        return ((south + north) / 2, (west + east) / 2)

    def to_dict(self):
        return {'region_id': self.region_id, 'bbox': list(self.bbox), 'crs': self.crs}


@dataclass(frozen=True)
class Snapshot:
    id: str
    created_at: datetime
    coverage: Coverage
    sources: Mapping[str, SourceResult]
    hazards: tuple
    schema_version: int = SCHEMA_VERSION
    warnings: tuple = ()
    hazard_coverage_state: str = 'unavailable'

    def to_dict(self):
        if self.schema_version == 2:
            from .normalized import snapshot_to_dict
            return snapshot_to_dict(self)
        return {'schema_version': SCHEMA_VERSION, 'snapshot_id': self.id,
                'created_at': iso(self.created_at), 'coverage': self.coverage.to_dict(),
                'sources': {name: source.to_dict() for name, source in self.sources.items()},
                'hazards': [hazard.to_dict() for hazard in self.hazards]}


def make_snapshot(coverage, sources, *, now):
    validate_timestamp(now)
    if now is None:
        raise ValueError('Snapshot creation timestamp is required.')
    # Serialized hazards are an auditable derivation, not timeless routing truth.
    hazards = prepare_flood_hazards(sources['nws'], now=now, include_stale=True).hazards
    return Snapshot(str(uuid4()), now, coverage, dict(sources), hazards)


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Expected nonempty text.')
    return value


def _time(value, required=False):
    result = parse_time(value)
    if required and result is None:
        raise ValueError('Missing timestamp.')
    return result


def _geometry(value, allowed):
    if value is None:
        return None
    geometry = shape(value)
    validate_geometry(geometry)
    if geometry.geom_type not in allowed:
        raise ValueError('Unexpected geometry type.')
    return geometry


def _mapping(value):
    if not isinstance(value, dict):
        raise ValueError('Expected an object.')
    return value


def source_from_dict(name, data):
    data = dict(_mapping(data))
    if (data['source'], data['endpoint']) != SOURCE_DEFINITIONS[name]:
        raise ValueError('Source provenance does not match the source key.')
    if data['status'] not in ('available', 'partial', 'unavailable'):
        raise ValueError('Invalid source status.')
    if data['data_origin'] not in ('live', 'cached', 'local', 'mixed', 'none'):
        raise ValueError('Invalid data origin.')
    data['query'] = _mapping(data['query'])
    if not isinstance(data['issues'], list) or any(not isinstance(v, str) for v in data['issues']):
        raise ValueError('Invalid source issues.')
    data['issues'] = tuple(data['issues'])
    data['fetched_at'] = _time(data['fetched_at'])
    data['attempted_at'] = _time(data['attempted_at'], required=True)
    if not isinstance(data['records'], list):
        raise ValueError('Expected a list of source records.')
    records, ids = [], set()
    for raw in data['records']:
        item = dict(_mapping(raw))
        record_id = _text(item['id'])
        if record_id in ids or item['source'] != data['source']:
            raise ValueError('Duplicate record ID or mismatched source.')
        ids.add(record_id)
        item['metadata'] = _mapping(item['metadata'])
        if item['metadata'].get('source_endpoint') != data['endpoint']:
            raise ValueError('Missing or mismatched record source endpoint.')
        item['fetched_at'] = _time(item['fetched_at'], required=True)
        if name == 'nws':
            for key in ('event', 'severity', 'status', 'message_type'):
                _text(item[key])
            references = item['metadata'].get('references')
            if references is not None and not isinstance(references, list):
                raise ValueError('Invalid alert references.')
            for key in ('sent', 'effective', 'onset', 'expires', 'ends'):
                item[key] = _time(item[key])
            item['geometry'] = _geometry(item['geometry'], ('Polygon', 'MultiPolygon'))
            records.append(FloodAlert(**item))
        else:
            series = item['metadata'].get('time_series_id')
            if series is not None and not isinstance(series, str):
                raise ValueError('Invalid time-series identifier.')
            if item.pop('role') != 'context_only':
                raise ValueError('Gauge observations must remain contextual.')
            for key in ('site_id', 'parameter_code', 'unit', 'raw_value'):
                _text(item[key])
            if (isinstance(item['value'], bool) or not isinstance(item['value'], (int, float))
                    or not math.isfinite(item['value'])):
                raise ValueError('Invalid observation value.')
            item['observed_at'] = _time(item['observed_at'], required=True)
            item['expires_at'] = _time(item['expires_at'])
            item['geometry'] = _geometry(item['geometry'], ('Point',))
            records.append(WaterObservation(**item))
    data['records'] = tuple(records)
    if data['fetched_at'] is None and (records or data['status'] != 'unavailable'):
        raise ValueError('Usable sources must retain a fetch timestamp.')
    return SourceResult(**data)


def snapshot_from_dict(data):
    """Reject incompatible or corrupt snapshots with a stable CacheError code."""
    if not isinstance(data, dict):
        raise CacheError('MALFORMED', 'Snapshot must be a JSON object.')
    if type(data.get('schema_version')) is int and data['schema_version'] == 2:
        from .normalized import snapshot_from_dict as normalized_from_dict
        return normalized_from_dict(data)
    if type(data.get('schema_version')) is not int or data['schema_version'] != SCHEMA_VERSION:
        raise CacheError('INCOMPATIBLE_VERSION', 'Unsupported or missing snapshot schema version.')
    try:
        json.dumps(data, allow_nan=False)
        if set(data) != {'schema_version', 'snapshot_id', 'created_at', 'coverage', 'sources', 'hazards'}:
            raise ValueError('Unexpected/missing snapshot fields.')
        created_at = _time(data['created_at'], required=True)
        coverage = Coverage(**_mapping(data['coverage']))
        if set(_mapping(data['sources'])) != set(SOURCE_DEFINITIONS):
            raise ValueError('Snapshot requires both NWS and USGS source statuses.')
        sources = {name: source_from_dict(name, value) for name, value in data['sources'].items()}
        hazards = []
        for raw in data['hazards']:
            item = dict(_mapping(raw))
            item['timestamp'] = _time(item['timestamp'])
            item['geometry'] = _geometry(item['geometry'], ('Point', 'LineString', 'Polygon', 'MultiPolygon'))
            hazards.append(Hazard(**item))
        expected = prepare_flood_hazards(sources['nws'], now=created_at, include_stale=True).hazards
        # Prevent stale/tampered cached derivatives from bypassing source expiry.
        encode = lambda values: json.dumps([h.to_dict() for h in values], sort_keys=True, allow_nan=False)
        if encode(hazards) != encode(expected):
            raise ValueError('Cached hazards do not match the source snapshot.')
        return Snapshot(_text(data['snapshot_id']), created_at, coverage, sources, tuple(hazards))
    except Exception as exc:
        raise CacheError('MALFORMED', f'Invalid snapshot: {type(exc).__name__}: {exc}') from exc


def make_hazard_snapshot(coverage, sources, *, now, snapshot_id=None, warnings=()):
    """Build schema 2 in the same Snapshot model; sources are HazardBatch values."""
    from .normalized import make_snapshot as normalized_snapshot
    return normalized_snapshot(coverage, sources, now=now, snapshot_id=snapshot_id, warnings=warnings)
