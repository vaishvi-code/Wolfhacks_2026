"""Filter discovered candidate footprints against supplied hazards."""
from collections.abc import Mapping
from datetime import datetime, timezone

import geopandas as gpd
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry

from .hazards import validate_geometry, validate_hazards, validate_timestamp
from .risk import RiskLevel, RiskPolicy, hazard_reason, spatial_matches
from .validation import location


def filter_destinations(candidates, hazards, policy=None, *, unsafe_levels=None,
                        evaluated_at=None):
    """Return accepted candidates and exclusions with their original records.

    Defaults exclude HIGH_RISK and IMPASSABLE intersections. Full GeoJSON
    footprints from discover_destinations are preferred over representative
    points; coordinate-only candidates are also supported. Boundary touch counts.
    """
    hazards = validate_hazards(hazards)
    policy = RiskPolicy() if policy is None else policy
    levels = [RiskLevel(policy.level_for(h)) for h in hazards]
    unsafe = ({RiskLevel.HIGH_RISK, RiskLevel.IMPASSABLE} if unsafe_levels is None
              else {RiskLevel(level) for level in unsafe_levels})
    evaluated_at = datetime.now(timezone.utc) if evaluated_at is None else evaluated_at
    validate_timestamp(evaluated_at)
    if candidates is None or isinstance(candidates, (str, bytes, Mapping)):
        raise ValueError('Supply an iterable of destination mappings.')
    candidates = list(candidates)
    geometries = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise ValueError('Each destination must be a mapping.')
        try:
            if 'geometry' in candidate:
                geometry = candidate['geometry']
                if not isinstance(geometry, BaseGeometry):
                    geometry = shape(geometry)
            else:
                lat, lon = location(candidate['latitude'], candidate['longitude'])
                geometry = Point(lon, lat)
            validate_geometry(geometry)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError('Destination requires valid EPSG:4326 geometry or coordinates.') from exc
        geometries.append(geometry)
    matches = spatial_matches(gpd.GeoSeries(geometries, crs='EPSG:4326'), hazards)
    accepted, excluded = [], []
    for candidate, positions in zip(candidates, matches):
        unsafe_positions = [i for i in positions if levels[i] in unsafe]
        if unsafe_positions:
            excluded.append({'destination': candidate,
                             'hazard_ids': [hazards[i].id for i in unsafe_positions],
                             'reasons': [hazard_reason(hazards[i], levels[i]) for i in unsafe_positions],
                             'evaluated_at': evaluated_at.isoformat()})
        else:
            accepted.append(candidate)
    return {'accepted': accepted, 'excluded': excluded}
