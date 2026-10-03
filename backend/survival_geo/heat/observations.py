"""Official station observations, always context-only; no derived heat index."""
import math
import re

import requests

from ..flood.freshness import OBSERVATION_FRESHNESS, freshness, iso, parse_time, utc_now
from ..hazards import validate_timestamp
from ..pipeline import HazardBatch


def station_endpoint(station):
    if not isinstance(station, str) or not re.fullmatch(r'[A-Za-z0-9_-]{3,20}', station):
        raise ValueError('Supply an official NWS station identifier.')
    return f'https://api.weather.gov/stations/{station}/observations/latest'


def parse_weather_observation(payload, *, station, fetched_at, now=None,
                              data_origin='local', freshness_policy=OBSERVATION_FRESHNESS):
    endpoint = station_endpoint(station)
    validate_timestamp(fetched_at)
    if fetched_at is None:
        raise ValueError('Original fetch time required.')
    now = fetched_at if now is None else now
    issues, measurements = [], {}
    coverage = {'endpoint': endpoint, 'station': station}
    try:
        properties = payload['properties']
        if not isinstance(properties, dict):
            raise ValueError('Invalid observation properties.')
        observed = parse_time(properties.get('timestamp'))
        for key in ('temperature', 'relativeHumidity', 'heatIndex'):
            raw = properties.get(key)
            if raw is None:
                measurements[key] = None
                continue
            try:
                value, unit = raw.get('value'), raw['unitCode']
                if not isinstance(unit, str) or not unit.strip():
                    raise ValueError('Missing units.')
                if value is not None and (isinstance(value, bool) or
                        not isinstance(value, (int, float)) or not math.isfinite(value)):
                    raise ValueError('Invalid measurement.')
                quality = raw.get('qualityControl')
                if quality is not None and not isinstance(quality, str):
                    raise ValueError('Invalid quality-control code.')
                measurements[key] = {'value': value, 'unitCode': unit,
                                     'qualityControl': quality}
            except (KeyError, TypeError, ValueError, AttributeError):
                measurements[key] = None
                issues.append(f'Invalid {key}; omitted.')
        state = freshness(observed_at=observed, fetched_at=fetched_at, now=now,
                          policy=freshness_policy).value
        if state != 'current':
            issues.append(f'Weather observation is {state}; context only.')
        if not any(item and item['value'] is not None for item in measurements.values()):
            issues.append('No usable weather measurements returned.')
        context = ({'kind': 'heat_weather_observation', 'id': f'nws-station:{station}',
            'station': station, 'source': 'NOAA/NWS', 'source_endpoint': endpoint,
            'observed_at': iso(observed), 'fetched_at': iso(fetched_at),
            'freshness': state, 'freshness_policy': freshness_policy.to_dict(),
            'measurements': measurements, 'used_for_routing': False,
            'data_origin': data_origin, 'spatial_scope': 'configured_station_only'},)
    except (KeyError, TypeError, ValueError) as exc:
        return HazardBatch(status='unavailable', issues=(f'Invalid weather observation: {exc}',),
                           attempted_at=fetched_at, coverage=coverage, data_origin='none')
    return HazardBatch(status='partial' if issues else 'available', issues=tuple(issues),
        context=context, coverage=coverage, fetched_at=fetched_at, attempted_at=fetched_at,
        data_origin=data_origin, freshness_policy=freshness_policy.to_dict())


class WeatherClient:
    def __init__(self, station, *, session=None, timeout=(5, 20),
                 user_agent='Wolfhacks-NC-Survival/0.1', freshness_policy=OBSERVATION_FRESHNESS):
        self.endpoint = station_endpoint(station)
        if not isinstance(user_agent, str) or not user_agent.strip():
            raise ValueError('Provide an identifying User-Agent.')
        self.station, self.session, self.timeout = station, session, timeout
        self.freshness_policy = freshness_policy
        self.headers = {'User-Agent': user_agent, 'Accept': 'application/geo+json'}

    def fetch(self, *, now=None):
        client = requests if self.session is None else self.session
        try:
            response = client.get(self.endpoint, headers=self.headers, timeout=self.timeout,
                                  allow_redirects=False)
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError('Unexpected HTTP response.')
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            return HazardBatch(status='unavailable', issues=(f'Weather request failed: {type(exc).__name__}.',),
                attempted_at=utc_now(), data_origin='none',
                coverage={'endpoint': self.endpoint, 'station': self.station})
        return parse_weather_observation(payload, station=self.station, fetched_at=utc_now(),
            now=now, data_origin='live', freshness_policy=self.freshness_policy)
