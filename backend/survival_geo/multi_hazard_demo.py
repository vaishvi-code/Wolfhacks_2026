"""Deterministic offline demo: python -m survival_geo.multi_hazard_demo."""
from dataclasses import replace
from datetime import datetime, timezone
import json

from .demo import demo_scenario
from .pipeline import HazardBatch, compare_hazard_routes


def demo_result():
    graph, origin, destination, hazards = demo_scenario()
    warning = replace(hazards[0], id='synthetic-flood', hazard_type='flood', severity='high',
                      metadata={'freshness': 'current', 'reason': 'Synthetic alert polygon intersects road.'})
    stale = replace(warning, id='stale-report', hazard_type='user_reported', severity='critical',
                    metadata={'freshness': 'stale', 'reason': 'Old report, retained as evidence only.'})
    return compare_hazard_routes(graph, origin, destination, [warning, stale],
        adapters={'missing-feed': lambda: HazardBatch(status='unavailable', issues=('Synthetic outage',))},
        evaluated_at=datetime(2026, 10, 3, 14, 10, tzinfo=timezone.utc))


def main():
    result = demo_result()
    summary = {'baseline_distance_m': result['baseline']['route']['total_distance_m'],
               'safer_distance_m': result['safer']['route']['total_distance_m'],
               'baseline_hazard_ids': result['baseline']['route']['hazard_ids'],
               'safer_hazard_ids': result['safer']['route']['hazard_ids'],
               'affected_edge_count': result['baseline']['route']['affected_edge_count'],
               'avoided_edge_count': result['avoided_edge_count'], 'comparison': result}
    print(json.dumps(summary, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
