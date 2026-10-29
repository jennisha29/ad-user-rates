"""Flattened GA4 event used by the dashboard and BigQuery load jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Ga4Event:
    event_id: str
    event_time: datetime
    event_timestamp_micros: int
    event_name: str
    user_pseudo_id: str
    user_id: str
    session_id: str
    page_path: str
    page_title: str
    engagement_time_msec: int
    source: str
    medium: str
    device_category: str
    replay_run_id: str = ""

    def to_bq(self) -> dict:
        event_time = self.event_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return {
            "event_id": self.event_id,
            "event_time": event_time,
            "event_timestamp_micros": self.event_timestamp_micros,
            "event_name": self.event_name,
            "user_pseudo_id": self.user_pseudo_id,
            "user_id": self.user_id or None,
            "session_id": self.session_id,
            "page_path": self.page_path or None,
            "page_title": self.page_title or None,
            "engagement_time_msec": self.engagement_time_msec,
            "source": self.source or None,
            "medium": self.medium or None,
            "device_category": self.device_category or None,
            "replay_run_id": self.replay_run_id,
        }


def make_run_id(when: datetime | None = None) -> str:
    moment = when or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
