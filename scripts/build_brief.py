#!/usr/bin/env python3
"""Regenerate docs/Interaction_Insights_Shareable_Brief.pdf. Requires fpdf2."""

from pathlib import Path

from fpdf import FPDF

OUT = Path(__file__).resolve().parent.parent / "docs" / "Interaction_Insights_Shareable_Brief.pdf"


def _write(pdf: FPDF, text: str, size: int = 11, style: str = "", height: float = 6) -> None:
    pdf.set_font("Helvetica", style, size)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(pdf.epw, height, text)


def main() -> None:
    pdf = FPDF(format="Letter")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(18, 18, 18)
    pdf.add_page()
    _write(pdf, "Interaction Insights", size=20, style="B", height=10)
    pdf.ln(2)
    _write(
        pdf,
        "Shareable brief. The source is the GA4 backup in "
        "gs://goods-ga4-backup_analysis, shards events-000000000000.parquet "
        "through events-000000000049.parquet. Reaction rates, page time, "
        "journeys, session gaps, and power users are read from BigQuery "
        "after those shards are loaded.",
    )
    pdf.ln(3)
    _write(pdf, "Four questions", size=13, style="B", height=8)
    blocks = [
        (
            "1. How do users react?",
            "BigQuery view v_reaction_rates. Click rate is click and ad_click "
            "divided by page views. Ignore rate is ad_ignore divided by page views. "
            "Conversion rate is purchase, sign_up, subscribe, and ad_free_subscribe "
            "divided by sessions. ad_close and app_reopen are counted on their own.",
        ),
        (
            "2. Time on a page, and the journey",
            "v_page_time sums engagement_time_msec on a page and divides by page views. "
            "v_user_journey and v_journey_patterns show the first four steps of a session. "
            "A page_view is labeled with its page path. Seconds between steps use the "
            "export timestamps.",
        ),
        (
            "3. What causes a session to end?",
            "v_session_end_proxy is the last event in each session. "
            "v_session_breaks is a gap of 30 minutes or more between a user's events. "
            "Path exploration and retention in the GA4 interface add the why.",
        ),
        (
            "4. How do power users use the product?",
            "v_power_users keeps a user with 15 or more events, or 3 or more sessions, "
            "or 8 or more page views, or a conversion. "
            "power_score = events + 10 x sessions + 20 x conversions.",
        ),
    ]
    for title, body in blocks:
        _write(pdf, title, size=12, style="B", height=7)
        _write(pdf, body)
        pdf.ln(1)

    _write(pdf, "How the warehouse is filled", size=13, style="B", height=8)
    _write(
        pdf,
        "scripts/download_ga4.sh copies the 50 parquet shards into data/ with "
        "gcloud storage cp. The bucket is private. setup_bq.py creates dataset "
        "interaction_insights, table ga4_events, and the views. replay.py appends "
        "events with BigQuery load jobs about every 60 seconds. No streaming inserts "
        "and no DML. Views keep the latest replay_run_id. The table expires about "
        "60 days after it is created. The loader does not send the backup through "
        "the Measurement Protocol.",
    )
    pdf.ln(2)
    _write(pdf, "Open it", size=13, style="B", height=8)
    _write(
        pdf,
        "python live_app.py --port 8765 --speed 80 --max-rows 1000\n"
        "http://127.0.0.1:8765/\n"
        "With the shards in data/, the page replays that export. Without them it "
        "shows a labeled stand-in and says so. With GCP_PROJECT and the service "
        "account set, the page can read the same views from BigQuery. "
        "Never commit the parquet, the JSON key, or a filled-in .env.",
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(OUT))
    print(OUT)


if __name__ == "__main__":
    main()
