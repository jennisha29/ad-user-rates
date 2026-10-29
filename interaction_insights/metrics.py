"""Product metrics over flattened GA4 events. The BigQuery views match these rules."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta, timezone

from interaction_insights.constants import (
    CLICK_EVENTS,
    CLOSE_EVENTS,
    CONVERT_EVENTS,
    DEFINITIONS,
    EXPOSURE_BUCKETS,
    IGNORE_EVENTS,
    JOURNEY_STEPS,
    LIVE_WINDOW_MINUTES,
    MANY_EVENTS,
    MANY_PAGE_VIEWS,
    MANY_SESSIONS,
    PAGE_EVENTS,
    POWER_CONVERSION_WEIGHT,
    POWER_SESSION_WEIGHT,
    REOPEN_EVENTS,
    SESSION_GAP_SECONDS,
)
from interaction_insights.model import Ga4Event


def rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def exposure_bucket(exposure_n: int) -> tuple[str, int]:
    if exposure_n <= 1:
        return "1", 1
    if exposure_n == 2:
        return "2", 2
    if exposure_n == 3:
        return "3", 3
    if exposure_n == 4:
        return "4", 4
    if exposure_n <= 9:
        return "5-9", 5
    return "10+", 6


def _iso(moment) -> str | None:
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).isoformat()


def blank_metrics() -> dict:
    return {
        "live_15min": {
            "saw": 0,
            "clicked": 0,
            "ignored": 0,
            "converted_proxy": 0,
            "ctr": None,
            "page_views": 0,
            "clicks": 0,
            "ignores": 0,
            "conversions": 0,
        },
        "funnel_minute": [],
        "campaign_funnel": [],
        "campaign_count": 0,
        "fatigue": [
            {"exposure_bucket": name, "bucket_order": order, "impressions": 0, "clicks": 0, "ctr": None}
            for name, order in EXPOSURE_BUCKETS
        ],
        "reaction_rates": {
            "impressions": 0,
            "page_views": 0,
            "clicks": 0,
            "ignores": 0,
            "ad_closes": 0,
            "reopens": 0,
            "conversions_proxy": 0,
            "sessions": 0,
            "events": 0,
            "click_rate": None,
            "ignore_rate": None,
            "no_click_pages": 0,
            "no_click_rate": None,
            "close_rate": None,
            "reopen_rate": None,
            "close_reopen_users": 0,
            "users_with_close": 0,
            "close_reopen_rate": None,
            "conversion_rate_proxy": None,
        },
        "journey_patterns": [],
        "featured_journey": None,
        "session_end_proxy": [],
        "session_breaks": {"gaps": 0, "examples": []},
        "power_users": [],
        "power_user_count": 0,
        "recent_events": [],
        "page_times": [],
        "event_counts": [],
        "window": {
            "event_time_min": None,
            "event_time_max": None,
        },
        "definitions": DEFINITIONS,
        "event_names": [],
    }


def _cap(rows: list, limit: int | None) -> list:
    if not limit or limit <= 0:
        return rows
    return rows[:limit]


def _label(event: Ga4Event) -> str:
    if event.event_name in PAGE_EVENTS and event.page_path:
        return event.page_path
    return event.event_name


def _session_key(event: Ga4Event) -> tuple[str, str]:
    return (event.user_pseudo_id, event.session_id or "")


def compute_snapshot(
    events: list[Ga4Event],
    *,
    pattern_limit: int | None = 20,
    power_limit: int | None = 50,
    page_limit: int | None = 12,
    campaign_limit: int | None = 20,
    event_limit: int | None = 12,
    break_example_limit: int | None = 8,
    recent_limit: int | None = 12,
    include_journeys: bool = False,
    include_tail: bool = False,
) -> dict:
    snapshot = blank_metrics()
    if not events:
        return snapshot

    ordered = sorted(events, key=lambda event: (event.event_timestamp_micros, event.event_id))
    by_user: dict[str, list[Ga4Event]] = defaultdict(list)
    by_session: dict[tuple[str, str], list[Ga4Event]] = defaultdict(list)
    name_counts: Counter[str] = Counter()
    for event in ordered:
        by_user[event.user_pseudo_id].append(event)
        by_session[_session_key(event)].append(event)
        name_counts[event.event_name] += 1

    page_views = clicks = ignores = closes = reopens = conversions = no_click_pages = 0
    minute_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"impressions": 0, "clicks": 0, "ignores": 0, "conversions": 0}
    )
    source_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"events": 0, "clicks": 0, "page_views": 0})
    page_engagement: dict[str, dict[str, float]] = defaultdict(lambda: {"views": 0, "engagement_msec": 0})
    fatigue = {
        name: {"exposure_bucket": name, "bucket_order": order, "impressions": 0, "clicks": 0}
        for name, order in EXPOSURE_BUCKETS
    }
    patterns: Counter[str] = Counter()
    last_types: Counter[str] = Counter()
    breaks: list[dict] = []

    for event in ordered:
        minute = event.event_time.astimezone(timezone.utc).replace(second=0, microsecond=0).isoformat()
        source_counts[event.source or "(direct)"]["events"] += 1
        if event.event_name in PAGE_EVENTS:
            page_views += 1
            minute_counts[minute]["impressions"] += 1
            source_counts[event.source or "(direct)"]["page_views"] += 1
            if event.page_path:
                page_engagement[event.page_path]["views"] += 1
                page_engagement[event.page_path]["engagement_msec"] += event.engagement_time_msec
        elif event.event_name in CLICK_EVENTS:
            clicks += 1
            minute_counts[minute]["clicks"] += 1
            source_counts[event.source or "(direct)"]["clicks"] += 1
        elif event.event_name in IGNORE_EVENTS:
            ignores += 1
            minute_counts[minute]["ignores"] += 1
        elif event.event_name in CLOSE_EVENTS:
            closes += 1
        elif event.event_name in REOPEN_EVENTS:
            reopens += 1
        elif event.event_name in CONVERT_EVENTS:
            conversions += 1
            minute_counts[minute]["conversions"] += 1
        if event.event_name == "user_engagement" and event.page_path:
            page_engagement[event.page_path]["engagement_msec"] += event.engagement_time_msec

    users_with_close = 0
    close_reopen_users = 0
    for uid, user_events in by_user.items():
        previous = None
        seen_close = False
        reopened_after_close = False
        close_session = ""
        for event in user_events:
            if (
                seen_close
                and event.session_id != close_session
                and event.event_name in (REOPEN_EVENTS | {"session_start"})
            ):
                reopened_after_close = True
            if event.event_name in CLOSE_EVENTS:
                seen_close = True
                close_session = event.session_id
            if previous is not None:
                gap = int((event.event_timestamp_micros - previous.event_timestamp_micros) / 1_000_000)
                if gap >= SESSION_GAP_SECONDS:
                    breaks.append(
                        {
                            "uid": uid,
                            "prev_event_type": previous.event_name,
                            "event_type": event.event_name,
                            "gap_seconds": gap,
                            "prev_source_timestamp": int(previous.event_timestamp_micros / 1_000_000),
                            "source_timestamp": int(event.event_timestamp_micros / 1_000_000),
                        }
                    )
            previous = event
        if seen_close:
            users_with_close += 1
            if reopened_after_close:
                close_reopen_users += 1

    per_user: dict[str, dict] = {}
    journeys: dict[str, list[dict]] = {}
    for (uid, session_id), session_events in by_session.items():
        steps = []
        previous = None
        page_index = 0
        for index, event in enumerate(session_events):
            gap = None if previous is None else int(
                (event.event_timestamp_micros - previous.event_timestamp_micros) / 1_000_000
            )
            if event.event_name in PAGE_EVENTS:
                page_index += 1
                next_page = next(
                    (
                        later.event_timestamp_micros
                        for later in session_events[index + 1 :]
                        if later.event_name in PAGE_EVENTS
                    ),
                    None,
                )
                clicked = any(
                    later.event_name in CLICK_EVENTS
                    and later.event_timestamp_micros >= event.event_timestamp_micros
                    and (next_page is None or later.event_timestamp_micros < next_page)
                    for later in session_events[index:]
                )
                bucket_name, _order = exposure_bucket(page_index)
                fatigue[bucket_name]["impressions"] += 1
                if clicked:
                    fatigue[bucket_name]["clicks"] += 1
                else:
                    no_click_pages += 1
            steps.append(
                {
                    "step_index": len(steps) + 1,
                    "event_type": _label(event),
                    "event_name": event.event_name,
                    "page_path": event.page_path,
                    "seconds_since_prev": gap,
                    "event_time": _iso(event.event_time),
                }
            )
            previous = event
        path = " > ".join(step["event_type"] for step in steps[:JOURNEY_STEPS])
        if path:
            patterns[path] += 1
        last_types[session_events[-1].event_name] += 1
        record = per_user.setdefault(
            uid,
            {
                "uid": uid,
                "events": 0,
                "sessions": 0,
                "clicks": 0,
                "page_views": 0,
                "conversions": 0,
                "engagement_msec": 0,
                "pages": Counter(),
            },
        )
        record["events"] += len(session_events)
        record["sessions"] += 1
        record["clicks"] += sum(1 for event in session_events if event.event_name in CLICK_EVENTS)
        record["page_views"] += sum(1 for event in session_events if event.event_name in PAGE_EVENTS)
        record["conversions"] += sum(1 for event in session_events if event.event_name in CONVERT_EVENTS)
        record["engagement_msec"] += sum(event.engagement_time_msec for event in session_events)
        for event in session_events:
            if event.event_name in PAGE_EVENTS and event.page_path:
                record["pages"][event.page_path] += 1
        journeys.setdefault(uid, [])
        if len(steps) > len(journeys[uid]):
            journeys[uid] = steps

    sessions = len(by_session)
    snapshot["reaction_rates"] = {
        "impressions": page_views,
        "page_views": page_views,
        "clicks": clicks,
        "ignores": ignores,
        "ad_closes": closes,
        "reopens": reopens,
        "conversions_proxy": conversions,
        "sessions": sessions,
        "events": len(ordered),
        "click_rate": rate(clicks, page_views),
        "ignore_rate": rate(ignores, page_views),
        "no_click_pages": no_click_pages,
        "no_click_rate": rate(no_click_pages, page_views),
        "close_rate": rate(closes, sessions),
        "reopen_rate": rate(reopens, sessions),
        "close_reopen_users": close_reopen_users,
        "users_with_close": users_with_close,
        "close_reopen_rate": rate(close_reopen_users, users_with_close),
        "conversion_rate_proxy": rate(conversions, sessions),
    }

    end = max(event.event_time for event in ordered)
    start = end - timedelta(minutes=LIVE_WINDOW_MINUTES)
    recent = [event for event in ordered if event.event_time >= start]
    recent_pages = sum(1 for event in recent if event.event_name in PAGE_EVENTS)
    recent_clicks = sum(1 for event in recent if event.event_name in CLICK_EVENTS)
    recent_ignores = sum(1 for event in recent if event.event_name in IGNORE_EVENTS)
    recent_conversions = sum(1 for event in recent if event.event_name in CONVERT_EVENTS)
    snapshot["live_15min"] = {
        "saw": recent_pages,
        "clicked": recent_clicks,
        "ignored": recent_ignores,
        "converted_proxy": recent_conversions,
        "ctr": rate(recent_clicks, recent_pages),
        "page_views": recent_pages,
        "clicks": recent_clicks,
        "ignores": recent_ignores,
        "conversions": recent_conversions,
    }
    snapshot["funnel_minute"] = [
        {"minute": minute, **counts} for minute, counts in sorted(minute_counts.items())
    ]
    funnel = []
    for source, counts in source_counts.items():
        funnel.append(
            {
                "campaign": source,
                "impressions": counts["events"],
                "clicks": counts["clicks"],
                "ignores": 0,
                "conversions": 0,
                "page_views": counts["page_views"],
                "ctr": rate(counts["clicks"], counts["page_views"]),
            }
        )
    funnel.sort(key=lambda item: (-item["impressions"], item["campaign"]))
    snapshot["campaign_count"] = len(funnel)
    snapshot["campaign_funnel"] = _cap(funnel, campaign_limit)
    snapshot["fatigue"] = [
        {
            **fatigue[name],
            "ctr": rate(fatigue[name]["clicks"], fatigue[name]["impressions"]),
        }
        for name, _order in EXPOSURE_BUCKETS
    ]
    pattern_rows = [
        {"path": path, "users": users}
        for path, users in sorted(patterns.items(), key=lambda item: (-item[1], item[0]))
    ]
    if pattern_limit and len(pattern_rows) > pattern_limit:
        snapshot["patterns_truncated"] = True
    snapshot["journey_patterns"] = _cap(pattern_rows, pattern_limit)
    snapshot["session_end_proxy"] = [
        {"last_event_type": event_type, "users": users}
        for event_type, users in sorted(last_types.items(), key=lambda item: (-item[1], item[0]))
    ]
    breaks.sort(key=lambda item: -item["gap_seconds"])
    snapshot["session_breaks"] = {"gaps": len(breaks), "examples": _cap(breaks, break_example_limit)}

    power = []
    for record in per_user.values():
        if (
            record["events"] >= MANY_EVENTS
            or record["sessions"] >= MANY_SESSIONS
            or record["page_views"] >= MANY_PAGE_VIEWS
            or record["conversions"] >= 1
        ):
            score = (
                record["events"]
                + POWER_SESSION_WEIGHT * record["sessions"]
                + POWER_CONVERSION_WEIGHT * record["conversions"]
            )
            power.append(
                {
                    "uid": record["uid"],
                    "events": record["events"],
                    "sessions": record["sessions"],
                    "clicks": record["clicks"],
                    "page_views": record["page_views"],
                    "conversions": record["conversions"],
                    "engagement_seconds": round(record["engagement_msec"] / 1000, 1),
                    "power_score": score,
                    "impressions": record["events"],
                    "campaigns": record["sessions"],
                    "pages": [path for path, _count in record["pages"].most_common(6)],
                }
            )
            if include_journeys:
                power[-1]["journey_steps"] = journeys.get(record["uid"], [])[:16]
    power.sort(key=lambda item: (-item["power_score"], item["uid"]))
    snapshot["power_user_count"] = len(power)
    snapshot["power_users"] = _cap(power, power_limit)
    if power:
        featured = power[0]["uid"]
        snapshot["featured_journey"] = {
            "uid": featured,
            "power_score": power[0]["power_score"],
            "steps": journeys.get(featured, [])[:16],
        }
    tail_count = recent_limit or 12
    snapshot["recent_events"] = [
        {
            "event_time": _iso(event.event_time),
            "event_type": event.event_name,
            "uid": event.user_pseudo_id,
            "campaign": event.page_path or event.source,
        }
        for event in ordered[-tail_count:]
    ][::-1]
    if include_tail:
        tail_start = end - timedelta(minutes=LIVE_WINDOW_MINUTES + 5)
        snapshot["tail"] = [
            {
                "t": event.event_timestamp_micros,
                "page": event.event_name in PAGE_EVENTS,
                "click": event.event_name in CLICK_EVENTS,
                "ignore": event.event_name in IGNORE_EVENTS,
                "conversion": event.event_name in CONVERT_EVENTS,
            }
            for event in ordered
            if event.event_time >= tail_start
        ]
    page_times = []
    for path, counts in page_engagement.items():
        views = int(counts["views"])
        seconds = counts["engagement_msec"] / 1000
        page_times.append(
            {
                "page_path": path,
                "views": views,
                "engagement_msec": int(counts["engagement_msec"]),
                "engagement_seconds": round(seconds, 1),
                "avg_engagement_seconds": None if views == 0 else round(seconds / views, 1),
            }
        )
    page_times.sort(key=lambda item: (-item["engagement_seconds"], item["page_path"]))
    snapshot["page_times"] = _cap(page_times, page_limit)
    counted = name_counts.most_common(event_limit) if event_limit else name_counts.most_common()
    snapshot["event_counts"] = [
        {"event_name": name, "events": count, "event_count": count}
        for name, count in counted
    ]
    snapshot["event_names"] = sorted(name_counts)
    snapshot["window"] = {
        "event_time_min": _iso(min(event.event_time for event in ordered)),
        "event_time_max": _iso(end),
    }
    snapshot["definitions"] = DEFINITIONS
    return snapshot
