"""Full-count report over GA4 backup shards.

Events are spilled by user, each user is aggregated in one partition, and the
partition snapshots are summed. Rates, page time, session endings, and the
power-user count match `compute_snapshot` on the same events.
"""

from __future__ import annotations

import hashlib
import pickle
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from interaction_insights.constants import EXPOSURE_BUCKETS, LIVE_WINDOW_MINUTES
from interaction_insights.metrics import blank_metrics, compute_snapshot, rate
from interaction_insights.model import Ga4Event
from interaction_insights.parquet_source import assign_sessions, discover_parquet, iter_events

PATTERN_CAP = 100_000
_COUNT_KEYS = (
    "impressions",
    "page_views",
    "clicks",
    "ignores",
    "ad_closes",
    "reopens",
    "conversions_proxy",
    "sessions",
    "events",
    "no_click_pages",
    "close_reopen_users",
    "users_with_close",
)


def bucket_for(user: str, partitions: int) -> int:
    digest = hashlib.md5(user.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % partitions


def merge_snapshots(parts: list[dict]) -> dict:
    parts = [part for part in parts if int((part.get("reaction_rates") or {}).get("events") or 0) > 0]
    if not parts:
        return blank_metrics()

    snapshot = blank_metrics()
    totals = {key: 0 for key in _COUNT_KEYS}
    for part in parts:
        rates = part.get("reaction_rates") or {}
        for key in _COUNT_KEYS:
            totals[key] += int(rates.get(key) or 0)
    page_views = totals["page_views"]
    sessions = totals["sessions"]
    snapshot["reaction_rates"] = {
        **totals,
        "click_rate": rate(totals["clicks"], page_views),
        "ignore_rate": rate(totals["ignores"], page_views),
        "no_click_pages": totals["no_click_pages"],
        "no_click_rate": rate(totals["no_click_pages"], page_views),
        "close_rate": rate(totals["ad_closes"], sessions),
        "reopen_rate": rate(totals["reopens"], sessions),
        "close_reopen_users": totals["close_reopen_users"],
        "users_with_close": totals["users_with_close"],
        "close_reopen_rate": rate(totals["close_reopen_users"], totals["users_with_close"]),
        "conversion_rate_proxy": rate(totals["conversions_proxy"], sessions),
    }

    fatigue = {
        name: {"exposure_bucket": name, "bucket_order": order, "impressions": 0, "clicks": 0}
        for name, order in EXPOSURE_BUCKETS
    }
    for part in parts:
        for row in part.get("fatigue") or []:
            bucket = fatigue.get(row["exposure_bucket"])
            if bucket is None:
                continue
            bucket["impressions"] += int(row.get("impressions") or 0)
            bucket["clicks"] += int(row.get("clicks") or 0)
    snapshot["fatigue"] = [
        {**fatigue[name], "ctr": rate(fatigue[name]["clicks"], fatigue[name]["impressions"])}
        for name, _order in EXPOSURE_BUCKETS
    ]

    pages: dict[str, dict[str, int]] = {}
    for part in parts:
        for row in part.get("page_times") or []:
            slot = pages.setdefault(row["page_path"], {"views": 0, "engagement_msec": 0})
            slot["views"] += int(row.get("views") or 0)
            if row.get("engagement_msec") is not None:
                slot["engagement_msec"] += int(row["engagement_msec"])
            else:
                slot["engagement_msec"] += int(round(float(row.get("engagement_seconds") or 0) * 1000))
    page_rows = []
    for path, counts in pages.items():
        views = counts["views"]
        seconds = counts["engagement_msec"] / 1000
        page_rows.append(
            {
                "page_path": path,
                "views": views,
                "engagement_msec": counts["engagement_msec"],
                "engagement_seconds": round(seconds, 1),
                "avg_engagement_seconds": None if views == 0 else round(seconds / views, 1),
            }
        )
    page_rows.sort(key=lambda item: (-item["engagement_seconds"], item["page_path"]))
    snapshot["page_times"] = page_rows

    names: Counter[str] = Counter()
    present: set[str] = set()
    for part in parts:
        present.update(part.get("event_names") or [])
        for row in part.get("event_counts") or []:
            names[row["event_name"]] += int(row.get("events") or row.get("event_count") or 0)
            present.add(row["event_name"])
    snapshot["event_counts"] = [
        {"event_name": name, "events": count, "event_count": count}
        for name, count in names.most_common()
    ]
    snapshot["event_names"] = sorted(present)

    patterns: Counter[str] = Counter()
    for part in parts:
        for row in part.get("journey_patterns") or []:
            patterns[row["path"]] += int(row.get("users") or 0)
    if len(patterns) > PATTERN_CAP:
        snapshot["patterns_truncated"] = True
        patterns = Counter(dict(patterns.most_common(PATTERN_CAP // 2)))
    snapshot["journey_patterns"] = [
        {"path": path, "users": users}
        for path, users in sorted(patterns.items(), key=lambda item: (-item[1], item[0]))
    ]

    endings: Counter[str] = Counter()
    for part in parts:
        for row in part.get("session_end_proxy") or []:
            endings[row["last_event_type"]] += int(row.get("users") or 0)
    snapshot["session_end_proxy"] = [
        {"last_event_type": event_type, "users": users}
        for event_type, users in sorted(endings.items(), key=lambda item: (-item[1], item[0]))
    ]

    gaps = 0
    examples: list[dict] = []
    for part in parts:
        info = part.get("session_breaks") or {}
        gaps += int(info.get("gaps") or 0)
        examples.extend(info.get("examples") or [])
    examples.sort(key=lambda item: -int(item.get("gap_seconds") or 0))
    snapshot["session_breaks"] = {"gaps": gaps, "examples": examples[:8]}

    campaigns: dict[str, dict[str, int]] = {}
    for part in parts:
        for row in part.get("campaign_funnel") or []:
            slot = campaigns.setdefault(
                row["campaign"],
                {"impressions": 0, "clicks": 0, "page_views": 0, "ignores": 0, "conversions": 0},
            )
            for key in slot:
                slot[key] += int(row.get(key) or 0)
    funnel = []
    for campaign, counts in campaigns.items():
        funnel.append(
            {
                "campaign": campaign,
                "impressions": counts["impressions"],
                "clicks": counts["clicks"],
                "ignores": counts["ignores"],
                "conversions": counts["conversions"],
                "page_views": counts["page_views"],
                "ctr": rate(counts["clicks"], counts["page_views"]),
            }
        )
    funnel.sort(key=lambda item: (-item["impressions"], item["campaign"]))
    snapshot["campaign_count"] = len(funnel)
    snapshot["campaign_funnel"] = funnel

    power_count = 0
    power_users: list[dict] = []
    for part in parts:
        power_count += int(part.get("power_user_count") or 0)
        power_users.extend(part.get("power_users") or [])
    power_users.sort(key=lambda item: (-int(item.get("power_score") or 0), str(item.get("uid"))))
    snapshot["power_user_count"] = power_count
    top = []
    featured_steps = None
    featured_score = None
    featured_uid = None
    for user in power_users[:50]:
        item = dict(user)
        steps = item.pop("journey_steps", None)
        if featured_uid is None:
            featured_uid = item.get("uid")
            featured_score = item.get("power_score")
            featured_steps = steps
        top.append(item)
    snapshot["power_users"] = top
    if featured_uid is not None:
        if not featured_steps:
            for part in parts:
                featured = part.get("featured_journey") or {}
                if featured.get("uid") == featured_uid:
                    featured_steps = featured.get("steps") or []
                    break
        snapshot["featured_journey"] = {
            "uid": featured_uid,
            "power_score": featured_score,
            "steps": featured_steps or [],
        }

    min_time, max_time = _window(parts)
    snapshot["window"] = {
        "event_time_min": min_time.isoformat() if min_time else None,
        "event_time_max": max_time.isoformat() if max_time else None,
    }
    snapshot["live_15min"] = _live_from_tails(parts, max_time)
    snapshot["funnel_minute"] = _minutes_in_window(parts, max_time)
    snapshot["recent_events"] = _latest_events(parts, 12)
    snapshot["tail"] = _tail_in_window(parts, max_time)
    snapshot["definitions"] = (parts[0].get("definitions") if parts else None) or snapshot["definitions"]
    if any(part.get("patterns_truncated") for part in parts):
        snapshot["patterns_truncated"] = True
    return snapshot


def report_from_events(events: list[Ga4Event], *, partitions: int = 4) -> dict:
    """Partition already-built events the same way the parquet report does."""
    buckets: dict[int, list[Ga4Event]] = {}
    for event in events:
        buckets.setdefault(bucket_for(event.user_pseudo_id, partitions), []).append(event)
    parts = [
        compute_snapshot(
            assign_sessions(bucket),
            pattern_limit=None,
            page_limit=None,
            campaign_limit=None,
            event_limit=None,
            include_journeys=True,
            include_tail=True,
        )
        for bucket in buckets.values()
        if bucket
    ]
    return _present(merge_snapshots(parts))


def build_report(
    data_dir: Path,
    output: Path,
    *,
    partitions: int = 64,
    max_files: int | None = None,
) -> dict:
    files = discover_parquet(data_dir)
    if max_files is not None:
        files = files[:max_files]
    if not files:
        raise FileNotFoundError(
            f"No events-*.parquet files in {data_dir}. "
            "Point this at the folder that holds the GA4 backup shards."
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ga4-report-") as tmp:
        payload = _spill_and_merge(files, Path(tmp), partitions)
    payload["coverage"]["files"] = len(files)
    payload["coverage"]["directory"] = str(data_dir)
    output.write_text(_dump(payload), encoding="utf-8")
    return payload


def _spill_and_merge(files: list[Path], spill_dir: Path, partitions: int) -> dict:
    handles = [
        (spill_dir / f"part-{index:04d}.pkl").open("wb")
        for index in range(partitions)
    ]
    scanned = 0
    try:
        for file_index, path in enumerate(files, start=1):
            print(f"Reading {file_index}/{len(files)} {path.name}", flush=True)
            batch: list[tuple[int, tuple]] = []
            for event in iter_events([path]):
                scanned += 1
                batch.append((bucket_for(event.user_pseudo_id, partitions), _pack(event)))
                if len(batch) >= 20000:
                    _flush(handles, batch)
                    batch = []
            _flush(handles, batch)
    finally:
        for handle in handles:
            handle.close()

    merged: dict | None = None
    partition_paths = sorted(spill_dir.glob("part-*.pkl"))
    for index, handle_path in enumerate(partition_paths, start=1):
        events = _load_partition(handle_path)
        if not events:
            continue
        print(f"Counting partition {index}/{len(partition_paths)} ({len(events)} events)", flush=True)
        part = compute_snapshot(
            assign_sessions(events),
            pattern_limit=20000,
            page_limit=None,
            campaign_limit=None,
            event_limit=None,
            include_journeys=True,
            include_tail=True,
        )
        del events
        merged = part if merged is None else merge_snapshots([merged, part])
    merged = merged or blank_metrics()
    rates = merged["reaction_rates"]
    names = set(merged.get("event_names") or [])
    watched = ("ad_ignore", "ad_close", "app_reopen")
    merged["coverage"] = {
        "files": len(files),
        "events": rates["events"],
        "event_time_min": merged["window"]["event_time_min"],
        "event_time_max": merged["window"]["event_time_max"],
        "missing_event_names": [name for name in watched if name not in names],
        "events_scanned": scanned,
        "patterns_truncated": bool(merged.get("patterns_truncated")),
    }
    merged["generated_at"] = datetime.now(timezone.utc).isoformat()
    merged.pop("tail", None)
    merged["row_source"] = "ga4-parquet-report"
    return _present(merged)


def _present(snapshot: dict) -> dict:
    """Trim list fields to what the dashboard draws, after counts are final."""
    snapshot["page_times"] = (snapshot.get("page_times") or [])[:12]
    snapshot["campaign_funnel"] = (snapshot.get("campaign_funnel") or [])[:20]
    snapshot["event_counts"] = (snapshot.get("event_counts") or [])[:12]
    snapshot["journey_patterns"] = (snapshot.get("journey_patterns") or [])[:20]
    snapshot["funnel_minute"] = snapshot.get("funnel_minute") or []
    snapshot.pop("tail", None)
    return snapshot


def _flush(handles: list, batch: list[tuple[int, tuple]]) -> None:
    for index, packed in batch:
        pickle.dump(packed, handles[index], protocol=pickle.HIGHEST_PROTOCOL)


def _load_partition(path: Path) -> list[Ga4Event]:
    events = []
    with path.open("rb") as handle:
        while True:
            try:
                events.append(_unpack(pickle.load(handle)))
            except EOFError:
                return events
    return events


def _pack(event: Ga4Event) -> tuple:
    return (
        event.event_id,
        event.event_timestamp_micros,
        event.event_name,
        event.user_pseudo_id,
        event.user_id,
        event.session_id,
        event.page_path,
        event.page_title,
        event.engagement_time_msec,
        event.source,
        event.medium,
        event.device_category,
    )


def _unpack(row: tuple) -> Ga4Event:
    micros = int(row[1])
    return Ga4Event(
        event_id=row[0],
        event_time=datetime.fromtimestamp(micros / 1_000_000, tz=timezone.utc),
        event_timestamp_micros=micros,
        event_name=row[2],
        user_pseudo_id=row[3],
        user_id=row[4],
        session_id=row[5],
        page_path=row[6],
        page_title=row[7],
        engagement_time_msec=int(row[8] or 0),
        source=row[9],
        medium=row[10],
        device_category=row[11],
    )


def _window(parts: list[dict]) -> tuple[datetime | None, datetime | None]:
    minimum: datetime | None = None
    maximum: datetime | None = None
    for part in parts:
        window = part.get("window") or {}
        if window.get("event_time_min"):
            moment = datetime.fromisoformat(window["event_time_min"])
            minimum = moment if minimum is None else min(minimum, moment)
        if window.get("event_time_max"):
            moment = datetime.fromisoformat(window["event_time_max"])
            maximum = moment if maximum is None else max(maximum, moment)
    return minimum, maximum


def _live_from_tails(parts: list[dict], end: datetime | None) -> dict:
    blank = blank_metrics()["live_15min"]
    if end is None:
        return blank
    cutoff = int((end - timedelta(minutes=LIVE_WINDOW_MINUTES)).timestamp() * 1_000_000)
    pages = clicks = ignores = conversions = 0
    used_tail = False
    for part in parts:
        tail = part.get("tail")
        if not tail:
            continue
        used_tail = True
        for item in tail:
            if int(item["t"]) < cutoff:
                continue
            pages += int(bool(item.get("page")))
            clicks += int(bool(item.get("click")))
            ignores += int(bool(item.get("ignore")))
            conversions += int(bool(item.get("conversion")))
    if not used_tail:
        return _live_from_minutes(parts, end)
    return {
        "saw": pages,
        "clicked": clicks,
        "ignored": ignores,
        "converted_proxy": conversions,
        "ctr": rate(clicks, pages),
        "page_views": pages,
        "clicks": clicks,
        "ignores": ignores,
        "conversions": conversions,
    }


def _live_from_minutes(parts: list[dict], end: datetime) -> dict:
    pages = clicks = ignores = conversions = 0
    cutoff = end - timedelta(minutes=LIVE_WINDOW_MINUTES)
    floor = cutoff.replace(second=0, microsecond=0)
    for row in _minute_rows(parts):
        moment = datetime.fromisoformat(row["minute"])
        if moment >= floor and moment + timedelta(minutes=1) > cutoff:
            pages += int(row.get("impressions") or 0)
            clicks += int(row.get("clicks") or 0)
            ignores += int(row.get("ignores") or 0)
            conversions += int(row.get("conversions") or 0)
    return {
        "saw": pages,
        "clicked": clicks,
        "ignored": ignores,
        "converted_proxy": conversions,
        "ctr": rate(clicks, pages),
        "page_views": pages,
        "clicks": clicks,
        "ignores": ignores,
        "conversions": conversions,
    }


def _minutes_in_window(parts: list[dict], end: datetime | None) -> list[dict]:
    if end is None:
        return []
    cutoff = (end - timedelta(minutes=LIVE_WINDOW_MINUTES)).replace(second=0, microsecond=0)
    rows = [row for row in _minute_rows(parts) if datetime.fromisoformat(row["minute"]) >= cutoff]
    rows.sort(key=lambda row: row["minute"])
    return rows


def _minute_rows(parts: list[dict]) -> list[dict]:
    minutes: dict[str, dict[str, int]] = {}
    for part in parts:
        for row in part.get("funnel_minute") or []:
            slot = minutes.setdefault(
                row["minute"],
                {"impressions": 0, "clicks": 0, "ignores": 0, "conversions": 0},
            )
            for key in slot:
                slot[key] += int(row.get(key) or 0)
    return [{"minute": minute, **counts} for minute, counts in minutes.items()]


def _tail_in_window(parts: list[dict], end: datetime | None) -> list[dict]:
    if end is None:
        return []
    cutoff = int((end - timedelta(minutes=LIVE_WINDOW_MINUTES + 5)).timestamp() * 1_000_000)
    kept = []
    for part in parts:
        for item in part.get("tail") or []:
            if int(item["t"]) >= cutoff:
                kept.append(item)
    return kept


def _latest_events(parts: list[dict], limit: int) -> list[dict]:
    rows: list[dict] = []
    for part in parts:
        rows.extend(part.get("recent_events") or [])
    rows.sort(key=lambda item: item.get("event_time") or "", reverse=True)
    return rows[:limit]


def _dump(payload: dict) -> str:
    import json

    return json.dumps(payload, indent=2, sort_keys=True)
