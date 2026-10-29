"""Read GA4 Data API reports. The backup is already GA4, so nothing is re-sent."""

from __future__ import annotations


def engagement_per_view(duration: float, views: float) -> float | None:
    if views <= 0:
        return None
    return duration / views


def fetch_ga4_reports(property_id: str) -> dict:
    """Read the GA4 reports that sit beside the BigQuery backup views."""
    if not property_id.isdigit():
        raise ValueError("GA4_PROPERTY_ID must be the numeric property id")
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        DateRange,
        Dimension,
        Metric,
        OrderBy,
        RunRealtimeReportRequest,
        RunReportRequest,
    )

    client = BetaAnalyticsDataClient()
    property_name = f"properties/{property_id}"
    errors: list[str] = []

    def safe(label: str, fn):
        try:
            return fn()
        except Exception as exc:
            errors.append(f"{label}: {exc.__class__.__name__}")
            return []

    def realtime():
        response = client.run_realtime_report(
            RunRealtimeReportRequest(
                property=property_name,
                dimensions=[Dimension(name="eventName")],
                metrics=[Metric(name="eventCount")],
                limit=25,
                order_bys=[
                    OrderBy(
                        metric=OrderBy.MetricOrderBy(metric_name="eventCount"),
                        desc=True,
                    )
                ],
            )
        )
        return _pairs(response, "event_name", "event_count")

    def events_7d():
        response = client.run_report(
            RunReportRequest(
                property=property_name,
                dimensions=[Dimension(name="eventName")],
                metrics=[Metric(name="eventCount")],
                date_ranges=[DateRange(start_date="7daysAgo", end_date="today")],
                limit=25,
                order_bys=[
                    OrderBy(
                        metric=OrderBy.MetricOrderBy(metric_name="eventCount"),
                        desc=True,
                    )
                ],
            )
        )
        return _pairs(response, "event_name", "event_count")

    def pages():
        response = client.run_report(
            RunReportRequest(
                property=property_name,
                dimensions=[Dimension(name="pagePath")],
                metrics=[
                    Metric(name="userEngagementDuration"),
                    Metric(name="screenPageViews"),
                    Metric(name="sessions"),
                ],
                date_ranges=[DateRange(start_date="7daysAgo", end_date="today")],
                limit=12,
                order_bys=[
                    OrderBy(
                        metric=OrderBy.MetricOrderBy(metric_name="userEngagementDuration"),
                        desc=True,
                    )
                ],
            )
        )
        rows = []
        for row in response.rows:
            duration = float(row.metric_values[0].value or 0)
            views = float(row.metric_values[1].value or 0)
            sessions = float(row.metric_values[2].value or 0)
            rows.append(
                {
                    "page_path": row.dimension_values[0].value,
                    "engagement_seconds": duration,
                    "views": int(views),
                    "sessions": int(sessions),
                    "avg_engagement_seconds": engagement_per_view(duration, views),
                }
            )
        return rows

    def landing():
        response = client.run_report(
            RunReportRequest(
                property=property_name,
                dimensions=[Dimension(name="landingPage")],
                metrics=[
                    Metric(name="sessions"),
                    Metric(name="averageSessionDuration"),
                    Metric(name="bounceRate"),
                ],
                date_ranges=[DateRange(start_date="7daysAgo", end_date="today")],
                limit=8,
                order_bys=[
                    OrderBy(
                        metric=OrderBy.MetricOrderBy(metric_name="sessions"),
                        desc=True,
                    )
                ],
            )
        )
        rows = []
        for row in response.rows:
            rows.append(
                {
                    "landing_page": row.dimension_values[0].value,
                    "sessions": int(float(row.metric_values[0].value or 0)),
                    "avg_session_seconds": float(row.metric_values[1].value or 0),
                    "bounce_rate": float(row.metric_values[2].value or 0),
                }
            )
        return rows

    def returning():
        response = client.run_report(
            RunReportRequest(
                property=property_name,
                dimensions=[Dimension(name="newVsReturning")],
                metrics=[
                    Metric(name="sessions"),
                    Metric(name="engagedSessions"),
                    Metric(name="eventCount"),
                    Metric(name="averageSessionDuration"),
                ],
                date_ranges=[DateRange(start_date="7daysAgo", end_date="today")],
            )
        )
        rows = []
        for row in response.rows:
            rows.append(
                {
                    "segment": row.dimension_values[0].value,
                    "sessions": int(float(row.metric_values[0].value or 0)),
                    "engaged_sessions": int(float(row.metric_values[1].value or 0)),
                    "event_count": int(float(row.metric_values[2].value or 0)),
                    "avg_session_seconds": float(row.metric_values[3].value or 0),
                }
            )
        return rows

    return {
        "property_id": property_id,
        "realtime_events": safe("realtime events", realtime),
        "events_7d": safe("events (7 days)", events_7d),
        "pages": safe("pages", pages),
        "landing_pages": safe("landing pages", landing),
        "audiences_proxy": safe("new vs returning", returning),
        "errors": errors,
    }


def empty_ga4(detail: str = "") -> dict:
    return {
        "property_id": "",
        "realtime_events": [],
        "events_7d": [],
        "pages": [],
        "landing_pages": [],
        "audiences_proxy": [],
        "errors": [detail] if detail else [],
    }


def _pairs(response, dimension_key: str, metric_key: str) -> list[dict]:
    rows = []
    for row in response.rows:
        rows.append(
            {
                dimension_key: row.dimension_values[0].value,
                metric_key: int(float(row.metric_values[0].value or 0)),
            }
        )
    return rows
