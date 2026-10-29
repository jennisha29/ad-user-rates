"""Read GA4 event parquet shards from the goods-ga4-backup_analysis export.

The files are the standard GA4 BigQuery export shape (nested event_params) or a
flat extract with the same column names. The backup in this bucket is
events-000000000000.parquet through events-000000000999.parquet (1000 shards).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from interaction_insights.model import Ga4Event

SHARD_COUNT = 1000
BUCKET = "goods-ga4-backup_analysis"


def shard_uris() -> list[str]:
    return [
        f"gs://{BUCKET}/events-{index:012d}.parquet"
        for index in range(SHARD_COUNT)
    ]


def discover_parquet(data_dir: Path) -> list[Path]:
    files = sorted(data_dir.glob("events-*.parquet"))
    if files:
        return files
    return sorted(path for path in data_dir.glob("*.parquet") if path.is_file())


def read_parquet(paths: list[Path], max_rows: int | None = None) -> list[Ga4Event]:
    events: list[Ga4Event] = []
    for event in iter_events(paths):
        events.append(event)
        if max_rows is not None and len(events) >= max_rows:
            break
    return _finalize(events)


def iter_events(paths: list[Path]):
    """Yield flattened events. Session ids that are blank are left blank."""
    import pyarrow.parquet as pq

    seen = 0
    for path in paths:
        parquet = pq.ParquetFile(path)
        for batch in parquet.iter_batches(batch_size=8192):
            for index, row in enumerate(batch.to_pylist()):
                seen += 1
                event = normalize_row(row, fallback_id=f"{path.name}:{seen}")
                if event is not None:
                    yield event


def normalize_row(row: dict, fallback_id: str) -> Ga4Event | None:
    params = _param_map(row.get("event_params"))
    event_name = str(row.get("event_name") or params.get("event_name") or "").strip()
    if not event_name:
        return None
    micros = _to_micros(row.get("event_timestamp") or params.get("event_timestamp"))
    if micros is None:
        micros = _micros_from_date(row.get("event_date"))
    if micros is None:
        return None
    user = str(row.get("user_pseudo_id") or row.get("user_id") or params.get("user_pseudo_id") or "unknown")
    user_id = str(row.get("user_id") or "")
    session = params.get("ga_session_id")
    if session is None:
        session = row.get("ga_session_id") or row.get("session_id") or ""
    session_id = str(session) if session not in (None, "") else ""
    page_location = str(params.get("page_location") or row.get("page_location") or "")
    page_path = str(params.get("page_path") or row.get("page_path") or "") or _path_from_location(page_location)
    page_title = str(params.get("page_title") or row.get("page_title") or "")
    engagement = _as_int(params.get("engagement_time_msec") or row.get("engagement_time_msec") or 0)
    traffic = row.get("traffic_source") if isinstance(row.get("traffic_source"), dict) else {}
    source = str(
        params.get("campaign")
        or row.get("campaign")
        or traffic.get("name")
        or traffic.get("source")
        or params.get("source")
        or row.get("source")
        or "(direct)"
    )
    medium = str(params.get("medium") or traffic.get("medium") or row.get("medium") or "")
    device = row.get("device") if isinstance(row.get("device"), dict) else {}
    device_category = str(device.get("category") or row.get("device_category") or "")
    event_id = str(row.get("event_id") or fallback_id)
    return Ga4Event(
        event_id=event_id,
        event_time=datetime.fromtimestamp(micros / 1_000_000, tz=timezone.utc),
        event_timestamp_micros=micros,
        event_name=event_name,
        user_pseudo_id=user,
        user_id=user_id,
        session_id=session_id,
        page_path=page_path,
        page_title=page_title,
        engagement_time_msec=engagement,
        source=source,
        medium=medium,
        device_category=device_category,
    )


def assign_sessions(events: list[Ga4Event]) -> list[Ga4Event]:
    """Fill blank session ids using a 30-minute gap on each user."""
    from interaction_insights.constants import SESSION_GAP_SECONDS

    ordered = sorted(events, key=lambda event: (event.user_pseudo_id, event.event_timestamp_micros, event.event_id))
    output: list[Ga4Event] = []
    counters: dict[str, int] = {}
    previous: dict[str, int] = {}
    for event in ordered:
        if event.session_id:
            output.append(event)
            previous[event.user_pseudo_id] = event.event_timestamp_micros
            continue
        last = previous.get(event.user_pseudo_id)
        gap = None if last is None else (event.event_timestamp_micros - last) / 1_000_000
        if last is None or (gap is not None and gap >= SESSION_GAP_SECONDS):
            counters[event.user_pseudo_id] = counters.get(event.user_pseudo_id, 0) + 1
        session_id = f"gap-{counters[event.user_pseudo_id]}"
        output.append(
            Ga4Event(
                event_id=event.event_id,
                event_time=event.event_time,
                event_timestamp_micros=event.event_timestamp_micros,
                event_name=event.event_name,
                user_pseudo_id=event.user_pseudo_id,
                user_id=event.user_id,
                session_id=session_id,
                page_path=event.page_path,
                page_title=event.page_title,
                engagement_time_msec=event.engagement_time_msec,
                source=event.source,
                medium=event.medium,
                device_category=event.device_category,
                replay_run_id=event.replay_run_id,
            )
        )
        previous[event.user_pseudo_id] = event.event_timestamp_micros
    return output


def _finalize(events: list[Ga4Event]) -> list[Ga4Event]:
    return assign_sessions(events)


def _param_map(raw) -> dict:
    found: dict = {}
    if not raw:
        return found
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if not key:
            continue
        value = item.get("value")
        if isinstance(value, dict):
            for candidate in ("string_value", "int_value", "float_value", "double_value"):
                if value.get(candidate) is not None:
                    found[key] = value[candidate]
                    break
        elif value is not None:
            found[key] = value
    return found


def _to_micros(value) -> int | None:
    if value is None or value == "":
        return None
    number = int(value)
    if number < 10**11:
        return number * 1_000_000
    if number < 10**14:
        return number * 1_000
    return number


def _micros_from_date(value) -> int | None:
    if not value:
        return None
    text = str(value)
    if len(text) != 8 or not text.isdigit():
        return None
    moment = datetime.strptime(text, "%Y%m%d").replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1_000_000)


def _as_int(value) -> int:
    if value is None or value == "":
        return 0
    return int(float(value))


def _path_from_location(location: str) -> str:
    if not location:
        return ""
    if "://" not in location:
        return location.split("?", 1)[0]
    parsed = urlparse(location)
    return parsed.path or "/"
