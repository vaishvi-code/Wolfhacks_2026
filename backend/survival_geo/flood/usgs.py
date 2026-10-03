"""USGS latest continuous observations, retained strictly as flood context."""
from dataclasses import replace
import math
import re

import osmnx as ox
from shapely.errors import ShapelyError
from shapely.geometry import shape

from ..hazards import validate_geometry, validate_timestamp
from ..validation import location, positive
from .freshness import parse_time, utc_now
from .models import SourceResult, WaterObservation
from .transport import collection_features, download_collection, required_text

USGS_SOURCE = 'USGS'
USGS_ENDPOINT = 'https://api.waterdata.usgs.gov/ogcapi/v1/collections/latest-continuous/items'
WATER_PARAMETERS = ('00060', '00065')  # discharge and gage height; no thresholds


def parse_usgs_observations(payload, *, fetched_at, query=None, data_origin='local'):
    validate_timestamp(fetched_at)
    if fetched_at is None:
        raise ValueError('The original fetch timestamp is required.')
    records, issues, seen = [], [], set()
    try:
        features = collection_features(payload)
    except ValueError as exc:
        return SourceResult(USGS_SOURCE, USGS_ENDPOINT, 'unavailable', (), None,
                            fetched_at, query or {}, (str(exc),), data_origin)
    for index, feature in enumerate(features):
        try:
            if not isinstance(feature, dict) or not isinstance(feature.get('properties'), dict):
                raise ValueError('Missing feature properties.')
            properties = feature['properties']
            observation_id = required_text(feature, 'id')
            if observation_id in seen:
                raise ValueError(f'Duplicate observation ID {observation_id}.')
            site_id = required_text(properties, 'monitoring_location_id')
            parameter = required_text(properties, 'parameter_code')
            unit = required_text(properties, 'unit_of_measure')
            raw = properties.get('value')
            if raw is None or isinstance(raw, bool):
                raise ValueError('Missing/non-numeric value.')
            value = float(raw)
            if not math.isfinite(value) or value == -999999:
                raise ValueError('Non-finite or missing-value sentinel observation.')
            observed_at = parse_time(properties.get('time'))
            if observed_at is None:
                raise ValueError('Missing observation timestamp.')
            geometry = None
            if feature.get('geometry') is not None:
                try:
                    geometry = shape(feature['geometry'])
                    validate_geometry(geometry)
                    if geometry.geom_type != 'Point':
                        raise ValueError('Monitoring location must be a point.')
                except (ShapelyError, AttributeError, IndexError, TypeError, ValueError):
                    geometry = None
                    issues.append(f'{observation_id}: unusable monitoring-location geometry.')
            records.append(WaterObservation(
                id=observation_id, site_id=site_id, parameter_code=parameter, unit=unit,
                value=value, raw_value=str(raw), observed_at=observed_at, fetched_at=fetched_at,
                geometry=geometry, source=USGS_SOURCE, metadata={
                    'time_series_id': properties.get('time_series_id'),
                    'approval_status': properties.get('approval_status'),
                    'qualifier': properties.get('qualifier'),
                    'last_modified': properties.get('last_modified'),
                    'source_endpoint': USGS_ENDPOINT,
                    'site_url': f'https://waterdata.usgs.gov/monitoring-location/{site_id}/',
                }))
            seen.add(observation_id)
        except (TypeError, ValueError) as exc:
            issues.append(f'Observation feature {index} skipped: {exc}')
    return SourceResult(USGS_SOURCE, USGS_ENDPOINT, 'partial' if issues else 'available',
                        tuple(records), fetched_at, fetched_at, query or {}, tuple(issues), data_origin)


class USGSClient:
    def __init__(self, *, api_key=None, session=None, timeout=(5, 20), max_pages=5):
        self.session, self.timeout, self.max_pages = session, timeout, max_pages
        self.headers = {'Accept': 'application/geo+json', 'User-Agent': 'Wolfhacks-NC-Survival/0.1'}
        if api_key:
            self.headers['X-Api-Key'] = api_key

    def fetch(self, *, point=None, radius_m=25000, site_id=None, parameters=WATER_PARAMETERS):
        """Discover measurements/sites in a bounding box OR at an explicit site.

        One simple query per parameter, bounded pagination. 'Latest' does not
        guarantee recent: preserve measurement times for freshness assessment.
        """
        if (point is None) == (site_id is None):
            raise ValueError('Supply either point or site_id.')
        parameters = tuple(parameters)
        if not parameters or any(not isinstance(p, str) or not re.fullmatch(r'\d{5}', p) for p in parameters):
            raise ValueError('Supply five-digit parameter codes.')
        if point is not None:
            center = location(*point)
            radius_m = positive(radius_m, 'radius_m')
            bounds = ox.utils_geo.bbox_from_point(center, dist=radius_m)
            query = {'bbox': ','.join(str(v) for v in bounds)}
        else:
            if not isinstance(site_id, str) or not re.fullmatch(r'USGS-\d+', site_id):
                raise ValueError('Expected monitoring location ID such as USGS-02087324.')
            query = {'monitoring_location_id': site_id}
        features, issues, statuses = [], [], []
        for parameter in dict.fromkeys(parameters):
            params = {**query, 'parameter_code': parameter, 'f': 'json', 'limit': 1000}
            downloaded = download_collection(USGS_ENDPOINT, params, headers=self.headers,
                                              session=self.session, timeout=self.timeout,
                                              max_pages=self.max_pages)
            features.extend(downloaded.payload['features'])
            issues.extend(f'{parameter}: {issue}' for issue in downloaded.issues)
            statuses.append(downloaded.status)
        fetched_at = utc_now()
        result = parse_usgs_observations({'type': 'FeatureCollection', 'features': features},
                                         fetched_at=fetched_at,
                                         query={**query, 'parameters': list(parameters)}, data_origin='live')
        if all(status == 'unavailable' for status in statuses):
            status = 'unavailable'
        else:
            status = 'partial' if issues or result.issues else 'available'
        return replace(result, status=status, issues=tuple(issues) + result.issues,
                       fetched_at=None if status == 'unavailable' else fetched_at,
                       data_origin='none' if status == 'unavailable' else 'live')
