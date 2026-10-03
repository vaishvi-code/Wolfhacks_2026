"""Bounded read-only HTTP retrieval shared by the two official adapters."""
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import requests


@dataclass(frozen=True)
class Download:
    payload: dict
    status: str
    issues: tuple


def required_text(properties, key):
    value = properties.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'Missing/invalid {key}.')
    return value


def collection_features(payload):
    if (not isinstance(payload, dict) or payload.get('type') != 'FeatureCollection'
            or not isinstance(payload.get('features'), list)):
        raise ValueError('Expected a GeoJSON FeatureCollection with a features list.')
    return payload['features']


def download_collection(endpoint, params, *, headers, session=None,
                        timeout=(5, 20), max_pages=5):
    """Failure returns status, never an indistinguishable successful empty list.

    Pagination is same-origin only, bounded, and explicitly partial on truncation.
    No automatic retries: callers control refresh frequency and backoff.
    """
    if not isinstance(max_pages, int) or max_pages < 1:
        raise ValueError('max_pages must be a positive integer.')
    client = requests if session is None else session
    features, seen, issues = [], set(), []
    url, query, pages = endpoint, dict(params), 0
    while url:
        if url in seen:
            issues.append('Pagination repeated a page; results are incomplete.')
            break
        seen.add(url)
        try:
            response = client.get(url, params=query, headers=headers, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            page_features = collection_features(payload)
        except (requests.RequestException, ValueError) as exc:
            # Avoid exception URLs/headers that could contain API keys.
            status_code = getattr(getattr(exc, 'response', None), 'status_code', None)
            issues.append(f'Source request failed: {type(exc).__name__}' +
                          (f' (HTTP {status_code}).' if status_code else '.'))
            return Download({'type': 'FeatureCollection', 'features': features},
                            'partial' if pages else 'unavailable', tuple(issues))
        features.extend(page_features)
        pages += 1
        try:
            links = payload.get('links', [])
            next_url = next((link['href'] for link in links if link.get('rel') == 'next'), None)
            if next_url is None:
                next_url = payload.get('pagination', {}).get('next')
            if next_url is None:
                break
            if not isinstance(next_url, str):
                raise ValueError('Malformed next link.')
            next_url = urljoin(endpoint, next_url)
            target, origin = urlsplit(next_url), urlsplit(endpoint)
            if (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
                raise ValueError('Pagination changed source origin.')
            if pages >= max_pages:
                issues.append('Pagination limit reached; results are incomplete.')
                break
            url, query = next_url, None
        except (AttributeError, KeyError, TypeError, ValueError):
            issues.append('Invalid pagination metadata; results are incomplete.')
            break
    return Download({'type': 'FeatureCollection', 'features': features},
                    'partial' if issues else 'available', tuple(issues))
