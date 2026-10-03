"""Source-independent hazard records in EPSG:4326 (longitude, latitude)."""
from dataclasses import dataclass, field
from datetime import datetime
import json
import math
from typing import Any, Mapping, Optional

from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry


def validate_geometry(geometry, geographic=True):
    """Reject missing/invalid footprints instead of silently repairing them."""
    if (not isinstance(geometry, BaseGeometry)
            or geometry.geom_type not in {'Point', 'LineString', 'Polygon', 'MultiPolygon'}
            or geometry.is_empty or not geometry.is_valid or geometry.has_z
            or not all(math.isfinite(v) for v in geometry.bounds)):
        raise ValueError('Expected a valid, nonempty 2D Point, LineString, Polygon or MultiPolygon.')
    if geographic:
        west, south, east, north = geometry.bounds
        if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
            raise ValueError('Hazard/destination geometry must use EPSG:4326 coordinates.')


def validate_timestamp(value):
    if value is not None and (not isinstance(value, datetime) or value.utcoffset() is None):
        raise ValueError('Timestamps must be timezone-aware datetime values or None.')


@dataclass(frozen=True)
class Hazard:
    """Severity is a policy label, not a physical measurement or probability.

    Types are extensible strings (flood, hurricane, heat, wildfire, road_closure,
    user_reported, etc.). Metadata can carry source-specific measurements.
    """
    id: str
    hazard_type: str
    geometry: BaseGeometry
    severity: str
    source: str
    confidence: float
    timestamp: Optional[datetime] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        for name in ('id', 'hazard_type', 'severity', 'source'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{name} must be a nonempty string.')
        validate_geometry(self.geometry)
        if (isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float))
                or not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1):
            raise ValueError('confidence must be a finite number in [0, 1].')
        validate_timestamp(self.timestamp)
        if not isinstance(self.metadata, Mapping):
            raise ValueError('metadata must be a JSON-compatible mapping.')
        try:
            # Snapshot caller-owned metadata, also checking strict JSON compatibility.
            metadata = json.loads(json.dumps(dict(self.metadata), allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise ValueError('metadata must be JSON-compatible.') from exc
        object.__setattr__(self, 'metadata', metadata)

    def to_dict(self):
        return {'id': self.id, 'hazard_type': self.hazard_type,
                'geometry': mapping(self.geometry), 'severity': self.severity,
                'source': self.source, 'confidence': self.confidence,
                'timestamp': self.timestamp.isoformat() if self.timestamp else None,
                'metadata': json.loads(json.dumps(self.metadata))}


def validate_hazards(hazards):
    """Materialize once, reject ambiguous inputs/duplicate IDs; [] is valid."""
    if hazards is None or isinstance(hazards, (str, bytes, Mapping)):
        raise ValueError('Supply an iterable of Hazard records; use [] for no hazards.')
    try:
        hazards = tuple(hazards)
    except TypeError as exc:
        raise ValueError('Supply an iterable of Hazard records.') from exc
    if any(not isinstance(hazard, Hazard) for hazard in hazards):
        raise ValueError('Every hazard must be a Hazard record.')
    if len({hazard.id for hazard in hazards}) != len(hazards):
        raise ValueError('Hazard IDs must be unique within an evaluation.')
    return hazards
