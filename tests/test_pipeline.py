from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from interaction_insights.config import load_settings
from interaction_insights.ga4 import engagement_per_view
from interaction_insights.metrics import compute_snapshot
from interaction_insights.parquet_source import (
    SHARD_COUNT,
    normalize_row,
    read_parquet,
    shard_uris,
)
from interaction_insights.replay_job import run_replay
from interaction_insights.sample_data import generate_sample
from interaction_insights.sql_views import (
    VIEW_NAMES,
    assert_safe_identifier,
    render_view_statements,
)
from interaction_insights.warehouse import snapshot_from_view_rows


def test_shard_uris_cover_the_backup():
    uris = shard_uris()
    assert len(uris) == SHARD_COUNT == 50
    assert uris[0] == "gs://goods-ga4-backup_analysis/events-000000000000.parquet"
    assert uris[-1] == "gs://goods-ga4-backup_analysis/events-000000000049.parquet"


def test_nested_parquet_roundtrip(tmp_path: Path):
    value = pa.struct(
        [
            ("string_value", pa.string()),
            ("int_value", pa.int64()),
            ("float_value", pa.float64()),
            ("double_value", pa.float64()),
        ]
    )
    schema = pa.schema(
        [
            ("event_name", pa.string()),
            ("event_timestamp", pa.int64()),
            ("user_pseudo_id", pa.string()),
            (
                "event_params",
                pa.list_(pa.struct([("key", pa.string()), ("value", value)])),
            ),
            (
                "traffic_source",
                pa.struct(
                    [
                        ("name", pa.string()),
                        ("medium", pa.string()),
                        ("source", pa.string()),
                    ]
                ),
            ),
            ("device", pa.struct([("category", pa.string())])),
        ]
    )
    micros = 1_700_000_000_000_000
    table = pa.Table.from_pylist(
        [
            {
                "event_name": "page_view",
                "event_timestamp": micros,
                "user_pseudo_id": "u1",
                "event_params": [
                    {
                        "key": "page_location",
                        "value": {
                            "string_value": "https://shop.example/pricing",
                            "int_value": None,
                            "float_value": None,
                            "double_value": None,
                        },
                    },
                    {
                        "key": "ga_session_id",
                        "value": {
                            "string_value": None,
                            "int_value": 9,
                            "float_value": None,
                            "double_value": None,
                        },
                    },
                    {
                        "key": "engagement_time_msec",
                        "value": {
                            "string_value": None,
                            "int_value": 1500,
                            "float_value": None,
                            "double_value": None,
                        },
                    },
                ],
                "traffic_source": {"name": "spring", "medium": "cpc", "source": "google"},
                "device": {"category": "mobile"},
            }
        ],
        schema=schema,
    )
    path = tmp_path / "events-000000000000.parquet"
    pq.write_table(table, path)
    events = read_parquet([path])
    assert len(events) == 1
    event = events[0]
    assert event.event_name == "page_view"
    assert event.page_path == "/pricing"
    assert event.session_id == "9"
    assert event.engagement_time_msec == 1500
    assert event.source == "spring"
    assert event.medium == "cpc"
    assert event.device_category == "mobile"
    assert event.event_timestamp_micros == micros


def test_timestamp_units_and_event_date():
    micros = normalize_row(
        {"event_name": "click", "event_timestamp": 1_700_000_000, "user_pseudo_id": "u"},
        "s",
    )
    millis = normalize_row(
        {"event_name": "click", "event_timestamp": 1_700_000_000_000, "user_pseudo_id": "u"},
        "ms",
    )
    already = normalize_row(
        {"event_name": "click", "event_timestamp": 1_700_000_000_000_000, "user_pseudo_id": "u"},
        "us",
    )
    dated = normalize_row({"event_name": "click", "event_date": "20261020", "user_pseudo_id": "u"}, "d")
    assert micros is not None and millis is not None and already is not None and dated is not None
    assert micros.event_timestamp_micros == 1_700_000_000 * 1_000_000
    assert millis.event_timestamp_micros == 1_700_000_000_000 * 1_000
    assert already.event_timestamp_micros == 1_700_000_000_000_000
    assert dated.event_time == datetime(2026, 10, 20, tzinfo=timezone.utc)
    assert normalize_row({"event_timestamp": 1}, "blank") is None


def test_sample_has_power_users_and_breaks():
    events = generate_sample(500)
    assert events == generate_sample(500)
    assert events[0].event_timestamp_micros <= events[-1].event_timestamp_micros
    snapshot = compute_snapshot(events)
    assert snapshot["power_user_count"] >= 1
    assert snapshot["session_breaks"]["gaps"] >= 1
    rates = snapshot["reaction_rates"]
    assert rates["events"] == 500
    assert rates["page_views"] > 0
    assert rates["click_rate"] is not None
    assert 0 <= rates["click_rate"] <= 1


def test_views_are_sandbox_safe():
    sql = "\n".join(render_view_statements("demo-project", "interaction_insights"))
    for name in VIEW_NAMES:
        assert name in sql
    assert "ga4_events" in sql
    assert "page_view" in sql
    assert "events >= 15" in sql
    assert ">= 1800" in sql
    assert "NULLIF(COUNTIF(event_name = 'page_view'), 0)" in sql
    upper = sql.upper()
    for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "MERGE ", "TRUNCATE "):
        assert forbidden not in upper
    with pytest.raises(ValueError):
        assert_safe_identifier("my project", "project")
    with pytest.raises(ValueError):
        assert_safe_identifier("interaction-insights;drop", "dataset")


def test_view_rows_map_onto_dashboard_shape():
    mapped = snapshot_from_view_rows(
        {
            "reaction_rates": [{
                "events": 20,
                "page_views": 10,
                "clicks": 2,
                "ignores": 1,
                "ad_closes": 1,
                "reopens": 1,
                "conversions_proxy": 1,
                "sessions": 4,
                "click_rate": 0.2,
                "ignore_rate": 0.1,
                "conversion_rate_proxy": 0.25,
            }],
            "live_15min": [{
                "saw": 4,
                "clicked": 1,
                "ignored": 0,
                "converted_proxy": 0,
                "ctr": 0.25,
            }],
            "fatigue": [{"exposure_bucket": "1", "bucket_order": 1, "impressions": 4, "clicks": 1, "ctr": 0.25}],
            "run_info": [{"events": 20, "replay_run_id": "20261028T000000000000"}],
        }
    )
    local = compute_snapshot([])
    assert set(local) <= set(mapped) or set(mapped) >= set(local) - {"recent_events"}
    assert mapped["live_15min"]["saw"] == 4
    assert mapped["reaction_rates"]["page_views"] == 10
    assert mapped["reaction_rates"]["click_rate"] == 0.2
    assert mapped["fatigue"][0]["clicks"] == 1
    assert mapped["fatigue"][1]["impressions"] == 0
    assert mapped["run_info"]["events"] == 20


def test_dry_run_uses_sample_and_refuses_a_real_load(tmp_path: Path):
    result = run_replay(
        max_rows=100,
        speed=0,
        dry_run=True,
        allow_sample=False,
        data_dir=tmp_path,
        flush_seconds=60,
        sleep=lambda _seconds: None,
    )
    assert result.source == "sample"
    assert result.rows == 100
    assert result.events == 100
    assert result.loaded == 0
    assert result.page_views > 0
    with pytest.raises(FileNotFoundError):
        run_replay(
            max_rows=10,
            speed=0,
            dry_run=False,
            allow_sample=False,
            data_dir=tmp_path,
            sleep=lambda _seconds: None,
        )


def test_engagement_per_view_and_settings(tmp_path: Path, monkeypatch):
    assert engagement_per_view(90, 3) == 30
    assert engagement_per_view(10, 0) is None
    monkeypatch.delenv("GCP_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("GA4_PROPERTY_ID", raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "GCP_PROJECT=your-project-id-here\n"
        "GOOGLE_APPLICATION_CREDENTIALS=/Users/YOUR_MAC_USERNAME/keys/interaction-insights.json\n"
        "BQ_DATASET=interaction_insights\n"
        "GA4_PROPERTY_ID=\n",
        encoding="utf-8",
    )
    settings = load_settings(env, override=True)
    assert settings.gcp_project == ""
    assert settings.google_application_credentials == ""
    assert settings.bq_dataset == "interaction_insights"
    assert settings.bq_configured is False
    assert "GCP_PROJECT" in settings.missing_bigquery()
    assert "GA4_PROPERTY_ID" in settings.missing_ga4_read()
