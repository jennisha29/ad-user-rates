import json
import threading
import time
from pathlib import Path

from live_app import AppServer
from interaction_insights.config import Settings
from interaction_insights.service import DashboardService


def _settings() -> Settings:
    return Settings(
        google_application_credentials="",
        gcp_project="",
        bq_dataset="interaction_insights",
        bq_location="US",
        speed=60,
        max_rows=50000,
        ga4_measurement_id="",
        ga4_api_secret="",
        ga4_property_id="",
    )


def test_dashboard_mentions_both_sources():
    html = Path("interaction_insights/dashboard.html").read_text(encoding="utf-8")
    for text in (
        "BigQuery",
        "Google Analytics",
        "How do users react?",
        "Time on page, and the journey",
        "What causes sessions to end?",
        "How do power users use the product?",
        "v_reaction_rates",
        "ad_close",
        "goods-ga4-backup_analysis",
        "ga4_events",
    ):
        assert text in html


def test_local_preview_serves_snapshot():
    app = DashboardService(_settings(), source="local", speed=0, max_rows=60, loop=False)
    app.start()
    server = AppServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        import urllib.request

        deadline = 0
        payload = {}
        while deadline < 50:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/snapshot", timeout=2) as response:
                payload = json.loads(response.read().decode())
            if payload["status"] == "complete" and payload["reaction_rates"]["events"] == 60:
                break
            deadline += 1
            time.sleep(0.05)
        assert payload["mode"] == "local"
        assert payload["reaction_rates"]["events"] == 60
        assert payload["progress"]["rows_done"] == 60
        assert payload["row_source"] == "sample"
        assert payload["connections"]["bigquery"]["ok"] is False
        assert payload["connections"]["ga4"]["configured"] is False
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
        assert b"Interaction Insights" in page.read()
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/control",
            data=b'{"action":"pause"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 200
    finally:
        app.stop()
        server.shutdown()
        server.server_close()
