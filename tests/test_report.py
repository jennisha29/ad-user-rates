import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from interaction_insights.metrics import compute_snapshot
from interaction_insights.report import build_report, report_from_events
from interaction_insights.service import DashboardService
from tests.test_dashboard import _settings
from tests.test_metrics import _fixture


def test_partitioned_counts_match_full_snapshot():
    events = _fixture()
    full = compute_snapshot(events)
    merged = report_from_events(events, partitions=4)
    assert merged["reaction_rates"] == full["reaction_rates"]
    assert merged["live_15min"] == full["live_15min"]
    assert merged["session_breaks"]["gaps"] == full["session_breaks"]["gaps"]
    assert merged["session_breaks"]["examples"][0]["gap_seconds"] == 1968
    assert merged["session_end_proxy"] == full["session_end_proxy"]
    assert merged["power_user_count"] == full["power_user_count"]
    assert merged["power_users"][0]["uid"] == "A"
    assert set(merged["power_users"][0]["pages"]) == set(full["power_users"][0]["pages"])
    assert merged["featured_journey"]["uid"] == "A"
    assert [step["event_name"] for step in merged["featured_journey"]["steps"]] == [
        step["event_name"] for step in full["featured_journey"]["steps"]
    ]
    pages = {row["page_path"]: row for row in merged["page_times"]}
    assert pages["/home"]["views"] == 2
    assert pages["/home"]["engagement_seconds"] == 7.0
    assert pages["/pricing"]["avg_engagement_seconds"] == 8.0
    assert merged["fatigue"] == full["fatigue"]


def test_build_report_reads_parquet(tmp_path: Path):
    rows = []
    for event in _fixture():
        rows.append(
            {
                "event_name": event.event_name,
                "event_timestamp": event.event_timestamp_micros,
                "user_pseudo_id": event.user_pseudo_id,
                "user_id": event.user_id,
                "session_id": event.session_id,
                "page_path": event.page_path,
                "engagement_time_msec": event.engagement_time_msec,
                "source": event.source,
            }
        )
    directory = tmp_path / "shards"
    directory.mkdir()
    pq.write_table(pa.Table.from_pylist(rows), directory / "events-000000000000.parquet")
    output = tmp_path / "report.json"
    payload = build_report(directory, output, partitions=4)
    assert payload["row_source"] == "ga4-parquet-report"
    assert payload["reaction_rates"]["events"] == 13
    assert payload["reaction_rates"]["no_click_pages"] == 4
    assert payload["reaction_rates"]["close_reopen_users"] == 1
    assert payload["coverage"]["missing_event_names"] == []
    assert output.is_file()

    saved = json.loads(output.read_text(encoding="utf-8"))
    app = DashboardService(
        _settings(),
        source="local",
        speed=0,
        max_rows=10,
        loop=False,
        report_path=output,
    )
    app.start()
    try:
        snapshot = app.snapshot()
    finally:
        app.stop()
    assert snapshot["mode"] == "report"
    assert snapshot["row_source"] == "ga4-parquet-report"
    assert snapshot["reaction_rates"]["page_views"] == saved["reaction_rates"]["page_views"] == 5
    assert "Not a live GA4 Data API call" in snapshot["banner"]
    assert "not a BigQuery query" in snapshot["banner"]
    assert snapshot["connections"]["ga4"]["ok"] is True
    assert snapshot["connections"]["bigquery"]["ok"] is False
