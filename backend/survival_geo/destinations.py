"""Configurable OSM destination candidates, not verified safe facilities."""
import osmnx as ox
from osmnx._errors import InsufficientResponseError
from shapely.geometry import mapping

from .errors import DataAccessError, EmptyOSMResults
from .validation import location, positive

DEFAULT_CATEGORIES = {
    'hospital': {'amenity': ['hospital']},
    'shelter': {'amenity': ['shelter'], 'social_facility': ['shelter']},
    'emergency_facility': {'amenity': ['fire_station', 'police']},
}


def discover_destinations(latitude, longitude, radius_m=5000, categories=None):
    """Return JSON-compatible OSM candidates. Tag predicates use OR semantics.

    Supply {category: {OSM tag: value or list or True}} to customize discovery.
    Polygon destinations use an interior representative point for routing.
    """
    center = location(latitude, longitude)
    radius_m = positive(radius_m, 'radius_m')
    categories = DEFAULT_CATEGORIES if categories is None else categories
    if not categories or any(not tags for tags in categories.values()):
        raise ValueError('Supply at least one category with OSM tags.')
    tags = {}
    for predicates in categories.values():
        for key, values in predicates.items():
            if values is True:
                tags[key] = True
            elif tags.get(key) is not True:
                values = [values] if isinstance(values, str) else list(values)
                tags[key] = list(dict.fromkeys(tags.get(key, []) + values))
    try:
        features = ox.features_from_point(center, tags=tags, dist=radius_m)
    except InsufficientResponseError as exc:
        raise EmptyOSMResults('OSM returned no destination candidates.') from exc
    except Exception as exc:
        raise DataAccessError('Unable to download OSM destinations.') from exc
    if features.empty:
        raise EmptyOSMResults('OSM returned no destination candidates.')
    features = features.to_crs('EPSG:4326')
    results = []
    for (element_type, osm_id), row in features.iterrows():
        geometry = row.geometry
        if geometry is None or geometry.is_empty:
            continue
        matched = []
        for category, predicates in categories.items():
            for key, values in predicates.items():
                value = row.get(key)
                if (values is True and isinstance(value, str)) or (values is not True and value in ([values] if isinstance(values, str) else values)):
                    matched.append(category)
                    break
        if not matched:
            continue
        point = geometry.representative_point()
        name = row.get('name')
        results.append({'osm_id': int(osm_id), 'element_type': element_type,
                        'name': name if isinstance(name, str) else None,
                        'categories': matched, 'latitude': point.y, 'longitude': point.x,
                        'geometry': mapping(geometry)})
    if not results:
        raise EmptyOSMResults('No usable destination geometries found.')
    return results
