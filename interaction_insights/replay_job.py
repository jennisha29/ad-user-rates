"""Load GA4 parquet events into BigQuery with batched load jobs."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from interaction_insights.model import Ga4Event, make_run_id
from interaction_insights.sources import resolve_events


@dataclass
class ReplayResult:
    run_id: str
    source: str
    rows: int
    events: int
    page_views: int
    clicks: int
    loaded: int
    dry_run: bool = False


def run_replay(
    *,
    max_rows: int,
    speed: float,
    flush_seconds: float = 60,
    dry_run: bool = False,
    allow_sample: bool = False,
    project: str = "",
    dataset: str = "interaction_insights",
    location: str = "US",
    data_dir=None,
    loader=None,
    ensurer=None,
    sleep=time.sleep,
    clock=time.monotonic,
) -> ReplayResult:
    events, source = resolve_events(
        max_rows,
        allow_sample=allow_sample or dry_run,
        data_dir=data_dir,
    )
    if source == "sample" and not allow_sample and not dry_run:
        raise FileNotFoundError("Refusing to load the stand-in sample into BigQuery.")

    run_id = make_run_id()
    stamped = [
        Ga4Event(
            event_id=event.event_id,
            event_time=event.event_time,
            event_timestamp_micros=event.event_timestamp_micros,
            event_name=event.event_name,
            user_pseudo_id=event.user_pseudo_id,
            user_id=event.user_id,
            session_id=event.session_id,
            page_path=event.page_path,
            page_title=event.page_title,
            engagement_time_msec=event.engagement_time_msec,
            source=event.source,
            medium=event.medium,
            device_category=event.device_category,
            replay_run_id=run_id,
        )
        for event in events
    ]
    if not dry_run:
        if ensurer is not None:
            ensurer()
        elif project:
            from interaction_insights.warehouse import ensure_warehouse

            ensure_warehouse(project, dataset, location)

    buffer: list[Ga4Event] = []
    loaded = 0
    page_views = 0
    clicks = 0
    last_flush = clock()
    start = datetime.now(timezone.utc)

    def flush() -> None:
        nonlocal loaded, buffer, last_flush
        if not buffer:
            last_flush = clock()
            return
        if not dry_run:
            if loader is not None:
                loaded += loader(buffer)
            else:
                from interaction_insights.warehouse import load_events

                loaded += load_events(project, dataset, buffer, location)
        buffer = []
        last_flush = clock()

    for index, event in enumerate(stamped):
        _wait_until_due(start, index, speed, sleep)
        if event.event_name == "page_view":
            page_views += 1
        elif event.event_name in {"click", "ad_click"}:
            clicks += 1
        buffer.append(event)
        if len(buffer) >= 50_000 or clock() - last_flush >= flush_seconds:
            flush()
    flush()
    return ReplayResult(
        run_id=run_id,
        source=source,
        rows=len(stamped),
        events=len(stamped),
        page_views=page_views,
        clicks=clicks,
        loaded=loaded,
        dry_run=dry_run,
    )


def _wait_until_due(start: datetime, index: int, speed: float, sleep) -> None:
    if speed <= 0:
        return
    due = start + timedelta(seconds=index / speed)
    delay = (due - datetime.now(timezone.utc)).total_seconds()
    if delay > 0:
        sleep(delay)
