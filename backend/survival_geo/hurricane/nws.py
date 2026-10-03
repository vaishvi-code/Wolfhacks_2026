"""Tropical event selection using the existing CAP parser and HTTP client."""
import re

from ..flood.nws import NWSClient, parse_nws_alerts

# Anchored family + product names avoid generic wind, marine hurricane-force
# wind, tornado, flood, heat and other non-cyclone events. Preserve official text.
_EVENT = re.compile(r'^(Hurricane|Tropical Storm|Storm Surge|Typhoon|Tropical Cyclone) '
                    r'(Warning|Watch|Advisory|Statement|Local Statement)$', re.IGNORECASE)


def tropical_event(event):
    return isinstance(event, str) and bool(_EVENT.fullmatch(' '.join(event.split())))


def canonical_event(event):
    return ' '.join(event.split()).casefold()


def parse_tropical_alerts(payload, *, fetched_at, query=None, data_origin='local'):
    return parse_nws_alerts(payload, fetched_at=fetched_at, query=query,
                           data_origin=data_origin, event_filter=tropical_event)


class TropicalNWSClient(NWSClient):
    """Only event selection differs; requests, pagination and failures are shared."""
    def __init__(self, **kwargs):
        super().__init__(event_filter=tropical_event, **kwargs)
