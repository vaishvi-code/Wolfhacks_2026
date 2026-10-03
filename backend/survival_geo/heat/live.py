"""Optional read-only official NC heat alerts and KRDU observation check."""
import json

from ..flood.freshness import utc_now
from .adapter import heat_hazard_batch
from .nws import HeatNWSClient
from .observations import WeatherClient


def main():
    source = HeatNWSClient().fetch(area='NC')
    now = utc_now()
    batch = heat_hazard_batch(source, now=now)
    weather = WeatherClient('KRDU').fetch()
    print(json.dumps({'evaluated_at': now.isoformat(), 'area': 'NC',
        'nws_status': source.status, 'nws_issues': list(source.issues),
        'returned_heat_alerts': len(source.records) if source.status != 'unavailable' else None,
        'alerts_with_geometry': sum(row.geometry is not None for row in source.records)
                                if source.status != 'unavailable' else None,
        'mapped_hazards': len(batch.hazards), 'assessment_issues': list(batch.issues),
        'weather_status': weather.status, 'weather_issues': list(weather.issues),
        'weather_context': list(weather.context),
        'notice': 'Partial counts are lower bounds; unavailable counts are unknown, not zero. No safety claim.'
    }, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
