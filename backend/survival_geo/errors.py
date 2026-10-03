"""Errors callers can translate into API responses."""

class GeospatialError(Exception):
    """Base backend error."""

class EmptyOSMResults(GeospatialError):
    """No usable roads or destinations were found."""

class DataAccessError(GeospatialError):
    """An OSM request or local graph operation failed."""

class LocationOutsideGraph(GeospatialError):
    """A location is outside coverage or too far from a road node."""

class NoRouteAvailable(GeospatialError):
    """No directed road path connects the endpoints."""
