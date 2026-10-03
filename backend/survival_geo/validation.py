import math


def location(latitude, longitude):
    """Validate and normalize a (latitude, longitude) pair."""
    lat, lon = float(latitude), float(longitude)
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError('Expected finite latitude [-90, 90] and longitude [-180, 180].')
    return lat, lon


def positive(value, name):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f'{name} must be finite and positive.')
    return value
