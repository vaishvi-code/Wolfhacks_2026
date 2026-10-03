"""Exact official heat event selection using the shared CAP parser/transport."""
from ..flood.nws import NWSClient, parse_nws_alerts


def canonical_event(event):
    return ' '.join(event.split()).casefold() if isinstance(event, str) else ''


HEAT_EVENTS = frozenset({
    'excessive heat warning', 'excessive heat watch', 'heat advisory',
    'extreme heat warning', 'extreme heat watch',
})


def heat_event(event):
    return canonical_event(event) in HEAT_EVENTS


class HeatNWSClient(NWSClient):
    def __init__(self, **kwargs):
        super().__init__(event_filter=heat_event, **kwargs)


def parse_heat_alerts(payload, **kwargs):
    return parse_nws_alerts(payload, event_filter=heat_event, **kwargs)
