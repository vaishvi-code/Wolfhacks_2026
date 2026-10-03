"""Optional read-only source verification; never substitutes synthetic records."""
import json

from ..flood.freshness import utc_now
from .adapter import hurricane_hazard_batch
from .nws import TropicalNWSClient
from .nhc import NHCClient


def main():
    source = TropicalNWSClient().fetch(area='NC')
    now = utc_now()
    batch = hurricane_hazard_batch(source, now=now)
    nhc = NHCClient().fetch()
    print(json.dumps({
        'evaluated_at': now.isoformat(), 'nws_query': {'area': 'NC'},
        'nws_status': source.status, 'nws_issues': list(source.issues),
        'returned_tropical_alerts': len(source.records) if source.status != 'unavailable' else None,
        'alerts_with_official_geometry': sum(r.geometry is not None for r in source.records)
                                         if source.status != 'unavailable' else None,
        'mapped_hazards': len(batch.hazards), 'assessment_issues': list(batch.issues),
        'nhc_status': nhc.status, 'nhc_issues': list(nhc.issues),
        'nhc_storms': [{'id': s['id'], 'name': s['name'], 'observed_at': s['observed_at'],
                        'freshness': s['freshness']} for s in nhc.context],
        'notice': 'Partial counts are lower bounds. No returned hazards does not establish safe roads.'
    }, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
