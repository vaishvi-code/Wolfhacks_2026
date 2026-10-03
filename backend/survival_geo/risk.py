"""Spatial exposure and configurable demo policy; no disaster physics."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from types import MappingProxyType
from typing import Mapping, Optional, Tuple

import geopandas as gpd
import pandas as pd

from .hazards import validate_geometry, validate_hazards, validate_timestamp


class RiskLevel(str, Enum):
    SAFE = 'SAFE'
    CAUTION = 'CAUTION'
    HIGH_RISK = 'HIGH_RISK'
    IMPASSABLE = 'IMPASSABLE'


LEVEL_ORDER = {level: rank for rank, level in enumerate(RiskLevel)}
EdgeId = Tuple[int, int, int]


@dataclass(frozen=True)
class RiskPolicy:
    """Illustrative severity mapping and additive per-hazard costs.

    Costs use meter-equivalent units when base cost is distance. No confidence
    weighting can reopen an IMPASSABLE edge. Subclass level_for() for future
    hazard-specific classification without changing the routing engine.
    """
    severity_levels: Mapping[str, RiskLevel] = field(default_factory=lambda: {
        'none': RiskLevel.SAFE, 'low': RiskLevel.CAUTION,
        'moderate': RiskLevel.CAUTION, 'high': RiskLevel.HIGH_RISK,
        'critical': RiskLevel.IMPASSABLE,
    })
    penalties: Mapping[RiskLevel, float] = field(default_factory=lambda: {
        RiskLevel.SAFE: 0.0, RiskLevel.CAUTION: 250.0,
        RiskLevel.HIGH_RISK: 2500.0, RiskLevel.IMPASSABLE: 0.0,
    })
    uncertainty_penalty_scale: float = 0.0

    def __post_init__(self):
        try:
            levels = {key: RiskLevel(value) for key, value in self.severity_levels.items()}
            penalties = {RiskLevel(key): float(value) for key, value in self.penalties.items()}
            scale = float(self.uncertainty_penalty_scale)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError('Invalid risk policy mapping or costs.') from exc
        if not levels or any(not isinstance(key, str) or not key.strip() for key in levels):
            raise ValueError('Severity mapping must contain nonempty labels.')
        if set(penalties) != set(RiskLevel):
            raise ValueError('Provide a penalty for every RiskLevel.')
        if any(not math.isfinite(v) or v < 0 for v in [*penalties.values(), scale]):
            raise ValueError('Penalties must be finite and nonnegative.')
        if penalties[RiskLevel.SAFE] != 0 or penalties[RiskLevel.HIGH_RISK] < penalties[RiskLevel.CAUTION]:
            raise ValueError('SAFE penalty must be zero; HIGH_RISK must be at least CAUTION.')
        object.__setattr__(self, 'severity_levels', MappingProxyType(levels))
        object.__setattr__(self, 'penalties', MappingProxyType(penalties))
        object.__setattr__(self, 'uncertainty_penalty_scale', scale)

    def level_for(self, hazard):
        try:
            return self.severity_levels[hazard.severity]
        except KeyError as exc:
            raise ValueError(f'Unmapped severity: {hazard.severity!r}. Supply a policy mapping.') from exc


@dataclass(frozen=True)
class RoadRisk:
    edge_id: EdgeId
    risk_level: RiskLevel
    hazard_penalty: float
    uncertainty_penalty: float
    passable: bool
    reasons: Tuple[str, ...]
    hazard_ids: Tuple[str, ...]
    confidence: Optional[float]
    evaluated_at: datetime
    contributions: Tuple[dict, ...] = ()

    def __post_init__(self):
        level = RiskLevel(self.risk_level)
        object.__setattr__(self, 'risk_level', level)
        if len(self.edge_id) != 3:
            raise ValueError('RoadRisk edge_id must be (u, v, key).')
        if self.passable != (level != RiskLevel.IMPASSABLE):
            raise ValueError('Passability must agree with the risk category.')
        if any(not math.isfinite(v) or v < 0 for v in
               (self.hazard_penalty, self.uncertainty_penalty, self.penalty)):
            raise ValueError('Road-risk penalties must be finite and nonnegative.')
        if level == RiskLevel.SAFE and self.penalty != 0:
            raise ValueError('SAFE roads must have zero penalty.')
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError('Road-risk confidence must be in [0, 1] or None.')
        validate_timestamp(self.evaluated_at)
        if self.evaluated_at is None:
            raise ValueError('Road-risk evaluation timestamp is required.')

    @property
    def penalty(self):
        return self.hazard_penalty + self.uncertainty_penalty

    def to_dict(self):
        return {'edge_id': dict(zip(('u', 'v', 'key'), map(int, self.edge_id))),
                'risk_level': self.risk_level.value, 'penalty': self.penalty,
                'hazard_penalty': self.hazard_penalty,
                'uncertainty_penalty': self.uncertainty_penalty,
                'passable': self.passable, 'reasons': list(self.reasons),
                'hazard_ids': list(self.hazard_ids), 'confidence': self.confidence,
                'evaluated_at': self.evaluated_at.isoformat(),
                'contributions': [dict(item) for item in self.contributions]}


def hazard_reason(hazard, level):
    return f'{hazard.id}: {hazard.hazard_type} severity={hazard.severity} mapped to {level.value}'


def spatial_matches(geometries, hazards):
    """Return hazard positions per geometry, including boundary touches.

    Work on fresh frames to avoid mutating caller geometries or relying on their
    index/column names. Reproject footprints to the input frame's declared CRS.
    """
    matches = [[] for _ in geometries]
    if not hazards or geometries.empty:
        return matches
    left = gpd.GeoDataFrame(geometry=list(geometries), crs=geometries.crs)
    right = gpd.GeoDataFrame({'hazard_position': range(len(hazards))},
                             geometry=[h.geometry for h in hazards], crs='EPSG:4326')
    right = right.to_crs(left.crs)
    joined = left.sjoin(right, how='inner', predicate='intersects')
    for position, hazard_position in zip(joined.index, joined['hazard_position']):
        matches[position].append(int(hazard_position))
    return [sorted(set(items)) for items in matches]


def evaluate_road_risks(edges, hazards, policy=None, evaluated_at=None):
    """Return {(u, v, key): RoadRisk} for every GeoPandas road edge.

    Overlap aggregation: maximum risk category, sum of individual penalties,
    minimum supplied confidence. SAFE means no mapped risk, not proven safety.
    """
    hazards = validate_hazards(hazards)
    policy = RiskPolicy() if policy is None else policy
    levels = [RiskLevel(policy.level_for(h)) for h in hazards]
    evaluated_at = datetime.now(timezone.utc) if evaluated_at is None else evaluated_at
    validate_timestamp(evaluated_at)
    if not isinstance(edges, gpd.GeoDataFrame) or edges.crs is None:
        raise ValueError('Road edges must be a GeoDataFrame with a declared CRS.')
    if (not isinstance(edges.index, pd.MultiIndex) or edges.index.names != ['u', 'v', 'key']
            or not edges.index.is_unique):
        raise ValueError('Road edges must have a unique (u, v, key) MultiIndex.')
    for geometry in edges.geometry:
        validate_geometry(geometry, geographic=False)
        if geometry.geom_type != 'LineString':
            raise ValueError('Road edge geometries must be LineStrings.')
    matches = spatial_matches(edges.geometry, hazards)
    results = {}
    for edge_id, positions in zip(edges.index, matches):
        contributors = [hazards[i] for i in positions]
        risk_level = max((levels[i] for i in positions), key=LEVEL_ORDER.get, default=RiskLevel.SAFE)
        hazard_penalty = sum(policy.penalties[levels[i]] for i in positions)
        uncertainty = sum(policy.uncertainty_penalty_scale * (1 - hazards[i].confidence)
                          for i in positions if levels[i] != RiskLevel.SAFE)
        reasons = tuple(hazard_reason(hazards[i], levels[i]) for i in positions)
        results[edge_id] = RoadRisk(
            edge_id=edge_id, risk_level=risk_level,
            hazard_penalty=float(hazard_penalty), uncertainty_penalty=float(uncertainty),
            passable=risk_level != RiskLevel.IMPASSABLE,
            reasons=reasons or ('No supplied hazard intersects this road.',),
            hazard_ids=tuple(h.id for h in contributors),
            confidence=min((h.confidence for h in contributors), default=None),
            evaluated_at=evaluated_at,
            contributions=tuple({
                'hazard_id': hazards[i].id, 'hazard_type': hazards[i].hazard_type,
                'severity': hazards[i].severity, 'risk_level': levels[i].value,
                'hazard_penalty': float(policy.penalties[levels[i]]),
                'uncertainty_penalty': float(policy.uncertainty_penalty_scale *
                    (1 - hazards[i].confidence) if levels[i] != RiskLevel.SAFE else 0),
                'passable': levels[i] != RiskLevel.IMPASSABLE,
                'applies_because': 'edge_geometry_intersects_hazard_geometry',
                'reason': hazards[i].metadata.get('reason', hazard_reason(hazards[i], levels[i])),
                'source': hazards[i].source, 'confidence': hazards[i].confidence,
                'timestamp': hazards[i].timestamp.isoformat() if hazards[i].timestamp else None,
                'freshness': hazards[i].metadata.get('freshness', 'unknown'),
                'evidence': hazards[i].to_dict()['metadata'],
            } for i in positions),
        )
    return results
