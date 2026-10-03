"""Heat evidence and relief selection on the existing unified backend."""
from .nws import HeatNWSClient, heat_event, parse_heat_alerts
from .adapter import HeatPolicy, heat_hazard_batch, registered_heat_adapters
from .observations import WeatherClient, parse_weather_observation
from .relief import (ReliefPolicy, discover_relief_candidates, ingest_official_centers,
                    evaluate_relief_candidates, relief_offline, route_exposure)
