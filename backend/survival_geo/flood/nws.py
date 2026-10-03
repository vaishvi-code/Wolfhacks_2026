"""NWS active flood alerts: official footprints only, never gauge buffers."""
from dataclasses import replace
import re

from shapely.errors import ShapelyError
from shapely.geometry import shape

from ..hazards import validate_geometry, validate_timestamp
from ..validation import location
from .freshness import parse_time, utc_now
from .models import FloodAlert, SourceResult
from .transport import collection_features, download_collection, required_text

NWS_SOURCE = 'NOAA/NWS'
NWS_ENDPOINT = 'https://api.weather.gov/alerts/active'


def parse_nws_alerts(payload, *, fetched_at, query=None, data_origin='local'):
    """Pure parser also usable with caller-managed saved response JSON.

    Keep expired, future, geometry-less and cancellation records for inspection;
    the flood adapter decides which may be routed against at evaluation time.
    """
    validate_timestamp(fetched_at)
    if fetched_at is None:
        raise ValueError('The original fetch timestamp is required.')
    records, issues, seen = [], [], set()
    try:
        features = collection_features(payload)
    except ValueError as exc:
        return SourceResult(NWS_SOURCE, NWS_ENDPOINT, 'unavailable', (), None,
                            fetched_at, query or {}, (str(exc),), data_origin)
    for index, feature in enumerate(features):
        try:
            if not isinstance(feature, dict) or not isinstance(feature.get('properties'), dict):
                raise ValueError('Missing feature properties.')
            properties = feature['properties']
            event = required_text(properties, 'event')
            if 'flood' not in event.casefold():
                continue
            alert_id = required_text(properties, 'id')
            if alert_id in seen:
                raise ValueError(f'Duplicate alert ID {alert_id}.')
            times = {key: parse_time(properties.get(key))
                     for key in ('sent', 'effective', 'onset', 'expires', 'ends')}
            for key in ('severity', 'status', 'messageType'):
                if properties.get(key) is not None and not isinstance(properties[key], str):
                    raise ValueError(f'Invalid {key}.')
            if properties.get('references') is not None and not isinstance(properties['references'], list):
                raise ValueError('Invalid alert references.')
            geometry = None
            if feature.get('geometry') is not None:
                try:
                    geometry = shape(feature['geometry'])
                    validate_geometry(geometry)
                    if geometry.geom_type not in ('Polygon', 'MultiPolygon'):
                        raise ValueError('Alert footprint must be a polygon.')
                except (ShapelyError, AttributeError, IndexError, TypeError, ValueError):
                    geometry = None
                    issues.append(f'{alert_id}: unusable official geometry; retained as context only.')
            metadata = {key: properties.get(key) for key in (
                'headline', 'description', 'instruction', 'areaDesc', 'affectedZones',
                'geocode', 'certainty', 'urgency', 'sender', 'senderName', 'web',
                'references', 'parameters', 'response')}
            metadata['feature_url'] = feature.get('id')
            metadata['source_endpoint'] = NWS_ENDPOINT
            records.append(FloodAlert(
                id=alert_id, event=event, severity=properties.get('severity') or 'Unknown',
                geometry=geometry, fetched_at=fetched_at, source=NWS_SOURCE,
                status=properties.get('status') or 'Unknown',
                message_type=properties.get('messageType') or 'Unknown', metadata=metadata, **times))
            seen.add(alert_id)
            if times['sent'] is None or times['expires'] is None:
                issues.append(f'{alert_id}: missing sent/expiration time; freshness may be unknown.')
        except (TypeError, ValueError) as exc:
            issues.append(f'Alert feature {index} skipped: {exc}')
    return SourceResult(NWS_SOURCE, NWS_ENDPOINT, 'partial' if issues else 'available',
                        tuple(records), fetched_at, fetched_at, query or {}, tuple(issues), data_origin)


class NWSClient:
    def __init__(self, *, user_agent='Wolfhacks-NC-Survival/0.1', session=None,
                 timeout=(5, 20), max_pages=5):
        if not isinstance(user_agent, str) or not user_agent.strip():
            raise ValueError('NWS requires an identifying User-Agent.')
        self.session, self.timeout, self.max_pages = session, timeout, max_pages
        self.headers = {'User-Agent': user_agent, 'Accept': 'application/geo+json'}

    def fetch(self, *, point=None, area=None):
        """Query a (lat, lon) point OR state area codes; defaults to area=NC.

        For a route corridor use an area covering the graph, not just its origin.
        """
        if point is not None and area is not None:
            raise ValueError('Choose either a point or an area query.')
        if point is not None:
            lat, lon = location(*point)
            query = {'point': f'{lat},{lon}'}
        else:
            area = 'NC' if area is None else area
            if not isinstance(area, str) or not re.fullmatch(r'[A-Z]{2}(,[A-Z]{2})*', area):
                raise ValueError('area must contain uppercase two-letter state codes.')
            query = {'area': area}
        downloaded = download_collection(NWS_ENDPOINT, query, headers=self.headers,
                                          session=self.session, timeout=self.timeout,
                                          max_pages=self.max_pages)
        fetched_at = utc_now()
        result = parse_nws_alerts(downloaded.payload, fetched_at=fetched_at,
                                  query=query, data_origin='live')
        status = downloaded.status if downloaded.status != 'available' else result.status
        return replace(result, status=status, issues=downloaded.issues + result.issues,
                       fetched_at=None if status == 'unavailable' else fetched_at,
                       data_origin='none' if status == 'unavailable' else 'live')
