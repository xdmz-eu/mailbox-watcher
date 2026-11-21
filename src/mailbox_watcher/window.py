"""Stateless report windows aligned to UTC interval boundaries.

Each run reports the complete interval that ended at the most recent
boundary. With ``interval_hours=6`` the boundaries are 00:00, 06:00, 12:00 and
18:00 UTC, so a run at 12:05 reports [06:00, 12:00). Consecutive successful
runs therefore cover time without gaps or overlaps, without storing state.
Cold starts or scheduling delays inside the next interval do not matter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class ReportWindow:
    start: datetime
    end: datetime

    def contains(self, moment: datetime) -> bool:
        """Return True if ``moment`` lies in [start, end)."""
        if moment.tzinfo is None:
            raise ValueError("moment must be timezone-aware")
        return self.start <= moment < self.end


def report_window(now: datetime, interval_hours: int) -> ReportWindow:
    """Return the last complete interval before ``now``."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if interval_hours < 1 or 24 % interval_hours != 0:
        raise ValueError("interval_hours must divide 24")
    now_utc = now.astimezone(timezone.utc)
    midnight = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    slots = now_utc.hour // interval_hours
    end = midnight + timedelta(hours=slots * interval_hours)
    start = end - timedelta(hours=interval_hours)
    return ReportWindow(start=start, end=end)
