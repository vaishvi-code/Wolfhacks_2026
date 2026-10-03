"""NHC CurrentStorms.json summaries: context only, never invented risk areas."""
from datetime import timedelta
import json
import math

import requests
from shapely.geometry import Point, mapping

from ..hazards import validate_geometry, validate_timestamp
from ..pipeline import HazardBatch
from ..flood.freshness import FreshnessPolicy, freshness, iso, parse_time, utc_now
from ..flood.transport import required_text

NHC_SOURCE = 'NOAA/NHC'
NHC_ENDPOINT = 'https://www.nhc.noaa.gov/CurrentStorms.json'
# Same freshness mechanism; the summary has no expiry. This is a configurable
# maximum observation age, not an official product expiration or danger threshold.
NHC_FRESHNESS = FreshnessPolicy(max_observation_age=timedelta(hours=6))


def _number(value):
    if isinstance(value, bool):
        raise ValueError('Boolean is not a measurement.')
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Non-finite measurement.')
    return number


def parse_nhc_storms(payload, *, fetched_at, now=None, data_origin='local',
                     freshness_policy=NHC_FRESHNESS):
    """Retain official IDs, center points, wind intensity and product metadata.

    CurrentStorms lists links to forecast GIS products, not embedded risk-area
    geometry. A storm center is context, not an affected-road footprint. Numeric
    camelCase fields match the official sample; snake_case aliases match the
    older official reference. No wind/category/track/surge model is inferred.
    """
    validate_timestamp(fetched_at)
    if fetched_at is None:
        raise ValueError('Original fetch time is required.')
    now = fetched_at if now is None else now
    validate_timestamp(now)
    if not isinstance(payload, dict) or not isinstance(payload.get('activeStorms'), list):
        return HazardBatch(status='unavailable', issues=('Invalid NHC activeStorms envelope.',),
                            attempted_at=fetched_at, data_origin='none',
                            coverage={'endpoint': NHC_ENDPOINT, 'scope': 'NHC active storm summaries'})
    context, issues, seen = [], [], set()
    for index, raw in enumerate(payload['activeStorms']):
        try:
            if not isinstance(raw, dict):
                raise ValueError('Storm must be an object.')
            json.dumps(raw, allow_nan=False)
            id = required_text(raw, 'id')
            if id in seen:
                issues.append(f'Duplicate NHC storm {id}; counted once.')
                continue
            observed = parse_time(raw.get('lastUpdate'))
            state = freshness(observed_at=observed, fetched_at=fetched_at, now=now,
                              policy=freshness_policy).value
            if state != 'current':
                issues.append(f'{id}: storm summary is {state}.')
            geometry = None
            lat = raw.get('latitudeNumeric', raw.get('latitude_numeric'))
            lon = raw.get('longitudeNumeric', raw.get('longitude_numeric'))
            if lat is not None or lon is not None:
                try:
                    point = Point(_number(lon), _number(lat))
                    validate_geometry(point)
                    geometry = mapping(point)
                except (TypeError, ValueError):
                    issues.append(f'{id}: invalid official center; retained without geometry.')
            wind = None
            if raw.get('intensity') is not None:
                try:
                    wind = _number(raw['intensity'])
                    if wind < 0:
                        raise ValueError('Negative intensity.')
                except (TypeError, ValueError):
                    wind = None
                    issues.append(f'{id}: invalid intensity; raw value retained.')
            # Named official objects include advisory times, IDs and links.
            products = {key: value for key, value in raw.items() if isinstance(value, dict)}
            context.append({'kind': 'nhc_storm_summary', 'id': id, 'source': NHC_SOURCE,
                'source_endpoint': NHC_ENDPOINT, 'name': raw.get('name'),
                'classification': raw.get('classification'), 'category': raw.get('category'),
                'observed_at': iso(observed), 'fetched_at': iso(fetched_at),
                'freshness': state, 'freshness_policy': freshness_policy.to_dict(),
                'geometry': geometry, 'geometry_role': 'storm_center_only',
                'intensity': {'value': wind, 'unit': 'knots', 'raw_value': raw.get('intensity')},
                'products': products, 'metadata': {'official_fields': raw},
                'used_for_routing': False, 'data_origin': data_origin,
                'reason': 'Official storm summary is contextual; no road hazard footprint supplied.'})
            seen.add(id)
        except (TypeError, ValueError, AttributeError) as exc:
            issues.append(f'NHC storm {index} skipped: {exc}')
    return HazardBatch(status='partial' if issues else 'available', issues=tuple(issues),
        context=tuple(sorted(context, key=lambda item: item['id'])),
        coverage={'endpoint': NHC_ENDPOINT, 'scope': 'NHC active storm summaries'},
        fetched_at=fetched_at, attempted_at=fetched_at, data_origin=data_origin,
        freshness_policy=freshness_policy.to_dict())


class NHCClient:
    """One bounded request to an official structured endpoint; no linked scraping."""
    def __init__(self, *, session=None, timeout=(5, 20),
                 user_agent='Wolfhacks-NC-Survival/0.1', freshness_policy=NHC_FRESHNESS):
        if not isinstance(user_agent, str) or not user_agent.strip():
            raise ValueError('Provide an identifying User-Agent.')
        self.session, self.timeout, self.freshness_policy = session, timeout, freshness_policy
        self.headers = {'User-Agent': user_agent, 'Accept': 'application/json'}

    def fetch(self, *, now=None):
        client = requests if self.session is None else self.session
        try:
            response = client.get(NHC_ENDPOINT, headers=self.headers, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            return HazardBatch(status='unavailable', issues=(f'NHC request failed: {type(exc).__name__}.',),
                attempted_at=utc_now(), data_origin='none',
                coverage={'endpoint': NHC_ENDPOINT, 'scope': 'NHC active storm summaries'})
        return parse_nhc_storms(payload, fetched_at=utc_now(), now=now,
                                data_origin='live', freshness_policy=self.freshness_policy)
