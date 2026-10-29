"""Create BigQuery objects, append with load jobs, and read the views.

Sandbox rules this module follows:
- no streaming inserts
- no DML (INSERT, UPDATE, DELETE, MERGE)
- load jobs plus SELECT and CREATE VIEW
- tables expire about 60 days after they are created
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from interaction_insights.constants import DEFINITIONS, EXPOSURE_BUCKETS, TABLE_EXPIRATION_DAYS
from interaction_insights.metrics import blank_metrics, rate
from interaction_insights.model import Ga4Event
from interaction_insights.sql_views import (
    assert_safe_identifier,
    featured_journey_sql,
    read_queries,
    render_view_statements,
)

BQ_FIELDS = (
    ("event_id", "STRING", "REQUIRED"),
    ("event_time", "TIMESTAMP", "REQUIRED"),
    ("event_timestamp_micros", "INT64", "REQUIRED"),
    ("event_name", "STRING", "REQUIRED"),
    ("user_pseudo_id", "STRING", "REQUIRED"),
    ("user_id", "STRING", "NULLABLE"),
    ("session_id", "STRING", "NULLABLE"),
    ("page_path", "STRING", "NULLABLE"),
    ("page_title", "STRING", "NULLABLE"),
    ("engagement_time_msec", "INT64", "NULLABLE"),
    ("source", "STRING", "NULLABLE"),
    ("medium", "STRING", "NULLABLE"),
    ("device_category", "STRING", "NULLABLE"),
    ("replay_run_id", "STRING", "REQUIRED"),
)


def _client(project: str, location: str):
    from google.cloud import bigquery

    return bigquery.Client(project=project, location=location), bigquery


def _schema(bigquery):
    return [
        bigquery.SchemaField(name, field_type, mode=mode)
        for name, field_type, mode in BQ_FIELDS
    ]


def ensure_warehouse(project: str, dataset: str, location: str = "US") -> list[str]:
    """Create the dataset, ga4_events table, and views."""
    project = assert_safe_identifier(project, "project")
    dataset_id = assert_safe_identifier(dataset, "dataset")
    client, bigquery = _client(project, location)
    dataset_ref = bigquery.Dataset(f"{project}.{dataset_id}")
    dataset_ref.location = location
    dataset_ref.default_table_expiration_ms = TABLE_EXPIRATION_DAYS * 24 * 60 * 60 * 1000
    client.create_dataset(dataset_ref, exists_ok=True)

    table_id = f"{project}.{dataset_id}.ga4_events"
    table = bigquery.Table(table_id, schema=_schema(bigquery))
    table.expires = datetime.now(timezone.utc) + timedelta(days=TABLE_EXPIRATION_DAYS)
    table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY,
        field="event_time",
    )
    table.description = (
        "Flattened GA4 events from gs://goods-ga4-backup_analysis. "
        "Views keep the latest replay_run_id. Loaded with batch load jobs only."
    )
    client.create_table(table, exists_ok=True)

    for statement in render_view_statements(project, dataset_id):
        client.query(statement).result(timeout=180)
    return list(read_queries(project, dataset_id))


def load_events(project: str, dataset: str, events: Iterable[Ga4Event], location: str = "US") -> int:
    batch = list(events)
    if not batch:
        return 0
    project = assert_safe_identifier(project, "project")
    dataset = assert_safe_identifier(dataset, "dataset")
    client, bigquery = _client(project, location)
    import json
    from io import BytesIO

    payload = "\n".join(json.dumps(event.to_bq()) for event in batch).encode() + b"\n"
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        schema=_schema(bigquery),
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        ignore_unknown_values=False,
    )
    table_id = f"{project}.{dataset}.ga4_events"
    job = client.load_table_from_file(
        BytesIO(payload),
        table_id,
        job_config=job_config,
        rewind=True,
    )
    job.result(timeout=300)
    return len(batch)


def _cell(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    try:
        from decimal import Decimal

        if isinstance(value, Decimal):
            if value == value.to_integral_value():
                return int(value)
            return float(value)
    except Exception:
        pass
    return value


def _rows(result) -> list[dict]:
    return [{key: _cell(value) for key, value in dict(row).items()} for row in result]


def fetch_view_rows(project: str, dataset: str, location: str = "US") -> dict[str, list[dict]]:
    project = assert_safe_identifier(project, "project")
    dataset = assert_safe_identifier(dataset, "dataset")
    client, bigquery = _client(project, location)
    found: dict[str, list[dict]] = {}
    for name, sql in read_queries(project, dataset).items():
        found[name] = _rows(client.query(sql).result(timeout=180))
    featured = found.get("featured_user") or []
    found["featured_journey"] = []
    if featured and featured[0].get("uid"):
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("uid", "STRING", str(featured[0]["uid"]))
            ]
        )
        sql = featured_journey_sql(project, dataset)
        found["featured_journey"] = _rows(client.query(sql, job_config=job_config).result(timeout=180))
        found["featured_score"] = featured[0].get("power_score")
    return found


def snapshot_from_view_rows(rows: dict[str, list[dict]]) -> dict:
    snapshot = blank_metrics()
    rates = (rows.get("reaction_rates") or [{}])[0]
    if rates:
        page_views = int(rates.get("page_views") or 0)
        clicks = int(rates.get("clicks") or 0)
        ignores = int(rates.get("ignores") or 0)
        conversions = int(rates.get("conversions_proxy") or 0)
        sessions = int(rates.get("sessions") or 0)
        snapshot["reaction_rates"] = {
            "impressions": page_views,
            "page_views": page_views,
            "clicks": clicks,
            "ignores": ignores,
            "ad_closes": int(rates.get("ad_closes") or 0),
            "reopens": int(rates.get("reopens") or 0),
            "conversions_proxy": conversions,
            "sessions": sessions,
            "events": int(rates.get("events") or 0),
            "click_rate": _optional_float(rates.get("click_rate"), rate(clicks, page_views)),
            "ignore_rate": _optional_float(rates.get("ignore_rate"), rate(ignores, page_views)),
            "conversion_rate_proxy": _optional_float(
                rates.get("conversion_rate_proxy"), rate(conversions, sessions)
            ),
        }
    live = (rows.get("live_15min") or [{}])[0]
    if live:
        saw = int(live.get("saw") or 0)
        clicked = int(live.get("clicked") or 0)
        snapshot["live_15min"] = {
            "saw": saw,
            "clicked": clicked,
            "ignored": int(live.get("ignored") or 0),
            "converted_proxy": int(live.get("converted_proxy") or 0),
            "ctr": _optional_float(live.get("ctr"), rate(clicked, saw)),
            "page_views": saw,
            "clicks": clicked,
            "ignores": int(live.get("ignored") or 0),
            "conversions": int(live.get("converted_proxy") or 0),
        }
    snapshot["funnel_minute"] = [
        {
            "minute": item.get("minute"),
            "impressions": int(item.get("impressions") or 0),
            "clicks": int(item.get("clicks") or 0),
            "ignores": int(item.get("ignores") or 0),
            "conversions": int(item.get("conversions") or 0),
        }
        for item in rows.get("funnel_minute") or []
    ]
    funnel = []
    for item in rows.get("campaign_funnel") or []:
        page_views = int(item.get("page_views") or item.get("impressions") or 0)
        clicks = int(item.get("clicks") or 0)
        funnel.append(
            {
                "campaign": str(item.get("campaign") or ""),
                "impressions": int(item.get("impressions") or 0),
                "clicks": clicks,
                "ignores": 0,
                "conversions": 0,
                "page_views": page_views,
                "ctr": _optional_float(item.get("ctr"), rate(clicks, page_views)),
            }
        )
    snapshot["campaign_funnel"] = funnel
    snapshot["campaign_count"] = len(funnel)
    fatigue = {
        name: {"exposure_bucket": name, "bucket_order": order, "impressions": 0, "clicks": 0, "ctr": None}
        for name, order in EXPOSURE_BUCKETS
    }
    for item in rows.get("fatigue") or []:
        name = str(item.get("exposure_bucket"))
        if name not in fatigue:
            continue
        impressions = int(item.get("impressions") or 0)
        clicks = int(item.get("clicks") or 0)
        fatigue[name] = {
            "exposure_bucket": name,
            "bucket_order": int(item.get("bucket_order") or fatigue[name]["bucket_order"]),
            "impressions": impressions,
            "clicks": clicks,
            "ctr": _optional_float(item.get("ctr"), rate(clicks, impressions)),
        }
    snapshot["fatigue"] = [fatigue[name] for name, _order in EXPOSURE_BUCKETS]
    snapshot["journey_patterns"] = [
        {"path": item.get("path") or "", "users": int(item.get("users") or 0)}
        for item in rows.get("journey_patterns") or []
    ]
    snapshot["session_end_proxy"] = [
        {"last_event_type": item.get("last_event_type") or "", "users": int(item.get("users") or 0)}
        for item in rows.get("session_end_proxy") or []
    ]
    gap_row = (rows.get("session_break_count") or [{}])[0]
    snapshot["session_breaks"] = {
        "gaps": int(gap_row.get("gaps") or 0),
        "examples": [
            {
                "uid": str(item.get("uid") or ""),
                "prev_event_type": item.get("prev_event_type"),
                "event_type": item.get("event_type"),
                "gap_seconds": int(item.get("gap_seconds") or 0),
                "prev_source_timestamp": item.get("prev_source_timestamp"),
                "source_timestamp": item.get("source_timestamp"),
            }
            for item in rows.get("session_break_examples") or []
        ],
    }
    power = []
    for item in rows.get("power_users") or []:
        events = int(item.get("events") or 0)
        sessions = int(item.get("sessions") or 0)
        power.append(
            {
                "uid": str(item.get("uid") or ""),
                "events": events,
                "sessions": sessions,
                "clicks": int(item.get("clicks") or 0),
                "page_views": int(item.get("page_views") or 0),
                "conversions": int(item.get("conversions") or 0),
                "engagement_seconds": float(item.get("engagement_seconds") or 0),
                "power_score": int(item.get("power_score") or 0),
                "impressions": events,
                "campaigns": sessions,
            }
        )
    snapshot["power_users"] = power
    snapshot["power_user_count"] = len(power)
    journey_rows = rows.get("featured_journey") or []
    if journey_rows:
        snapshot["featured_journey"] = {
            "uid": str(journey_rows[0].get("uid") or ""),
            "power_score": rows.get("featured_score"),
            "steps": [
                {
                    "step_index": int(item.get("step_index") or 0),
                    "event_type": item.get("step_label") or item.get("event_name"),
                    "event_name": item.get("event_name"),
                    "page_path": item.get("page_path") or "",
                    "seconds_since_prev": item.get("seconds_since_prev"),
                    "event_time": item.get("event_time"),
                }
                for item in journey_rows
            ],
        }
    snapshot["page_times"] = [
        {
            "page_path": item.get("page_path") or "",
            "views": int(item.get("views") or 0),
            "engagement_seconds": float(item.get("engagement_seconds") or 0),
            "avg_engagement_seconds": _optional_float(item.get("avg_engagement_seconds"), None),
        }
        for item in rows.get("page_time") or []
    ]
    snapshot["event_counts"] = [
        {
            "event_name": item.get("event_name") or "",
            "events": int(item.get("events") or 0),
            "event_count": int(item.get("events") or 0),
        }
        for item in rows.get("event_counts") or []
    ]
    info = (rows.get("run_info") or [{}])[0]
    snapshot["window"] = {
        "event_time_min": info.get("event_time_min"),
        "event_time_max": info.get("event_time_max"),
    }
    snapshot["run_info"] = {
        "events": int(info.get("events") or 0),
        "replay_run_id": info.get("replay_run_id"),
    }
    snapshot["definitions"] = DEFINITIONS
    return snapshot


def _optional_float(value, fallback):
    if value is None:
        return fallback
    return float(value)


def read_warehouse_snapshot(project: str, dataset: str, location: str = "US") -> dict:
    return snapshot_from_view_rows(fetch_view_rows(project, dataset, location=location))
