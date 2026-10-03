"""Explicit age/expiry rules for dynamic source records, independent of routing."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from ..hazards import validate_timestamp


def utc_now():
    return datetime.now(timezone.utc)


def parse_time(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('Expected an ISO timestamp string or null.')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    validate_timestamp(result)
    return result.astimezone(timezone.utc)


def iso(value):
    return value.isoformat() if value is not None else None


class Freshness(str, Enum):
    CURRENT = 'current'
    STALE = 'stale'
    EXPIRED = 'expired'
    UNKNOWN = 'unknown'
    NOT_YET_ACTIVE = 'not_yet_active'


@dataclass(frozen=True)
class FreshnessPolicy:
    max_fetch_age: timedelta = timedelta(minutes=15)
    max_observation_age: Optional[timedelta] = None
    future_clock_tolerance: timedelta = timedelta(minutes=2)

    def __post_init__(self):
        for value in (self.max_fetch_age, self.max_observation_age, self.future_clock_tolerance):
            if value is not None and (not isinstance(value, timedelta) or value < timedelta(0)):
                raise ValueError('Freshness thresholds must be nonnegative timedeltas.')
        if self.max_fetch_age is None or self.future_clock_tolerance is None:
            raise ValueError('Fetch age and clock tolerance are required.')

    def to_dict(self):
        return {'max_fetch_age_seconds': self.max_fetch_age.total_seconds(),
                'max_observation_age_seconds': (self.max_observation_age.total_seconds()
                                                if self.max_observation_age is not None else None),
                'future_clock_tolerance_seconds': self.future_clock_tolerance.total_seconds()}


ALERT_FRESHNESS = FreshnessPolicy()
OBSERVATION_FRESHNESS = FreshnessPolicy(max_observation_age=timedelta(hours=2))


def freshness(*, observed_at, fetched_at, expires_at=None, effective_at=None,
              now=None, policy=ALERT_FRESHNESS):
    now = utc_now() if now is None else now
    for value in (observed_at, fetched_at, expires_at, effective_at, now):
        validate_timestamp(value)
    if expires_at is not None and now >= expires_at:
        return Freshness.EXPIRED
    if effective_at is not None and now < effective_at:
        return Freshness.NOT_YET_ACTIVE
    if observed_at is None or fetched_at is None:
        return Freshness.UNKNOWN
    if any(value > now + policy.future_clock_tolerance for value in (observed_at, fetched_at)):
        return Freshness.UNKNOWN
    if now - fetched_at > policy.max_fetch_age:
        return Freshness.STALE
    if policy.max_observation_age is not None and now - observed_at > policy.max_observation_age:
        return Freshness.STALE
    return Freshness.CURRENT
