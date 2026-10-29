from datetime import datetime, timedelta, timezone

from interaction_insights.constants import MANY_EVENTS, SESSION_GAP_SECONDS
from interaction_insights.metrics import compute_snapshot
from interaction_insights.model import Ga4Event

BASE = datetime(2026, 10, 20, 14, 0, tzinfo=timezone.utc)


def _event(uid, session, seconds, name, path="", engagement=0, source="(direct)"):
    moment = BASE + timedelta(seconds=seconds)
    return Ga4Event(
        event_id=f"{uid}-{session}-{seconds}-{name}",
        event_time=moment,
        event_timestamp_micros=int(moment.timestamp() * 1_000_000),
        event_name=name,
        user_pseudo_id=uid,
        user_id="",
        session_id=session,
        page_path=path,
        page_title="",
        engagement_time_msec=engagement,
        source=source,
        medium="",
        device_category="desktop",
    )


def _fixture() -> list[Ga4Event]:
    return [
        _event("A", "a1", 0, "session_start"),
        _event("A", "a1", 5, "page_view", "/home", 5000),
        _event("A", "a1", 10, "click", "/home"),
        _event("A", "a1", 20, "page_view", "/pricing", 8000),
        _event("A", "a1", 25, "ad_impression", "/pricing"),
        _event("A", "a1", 28, "ad_ignore", "/pricing"),
        _event("A", "a1", 32, "ad_close", "/pricing"),
        _event("B", "b1", 100, "page_view", "/home", 2000, "organic"),
        _event("B", "b1", 110, "page_view", "/help", 1000, "organic"),
        _event("A", "a2", 2000, "app_reopen"),
        _event("A", "a2", 2005, "session_start"),
        _event("A", "a2", 2010, "page_view", "/checkout", 1000),
        _event("A", "a2", 2020, "purchase", "/checkout"),
    ]


def test_reaction_rates_journey_fatigue_and_power():
    snapshot = compute_snapshot(_fixture())
    rates = snapshot["reaction_rates"]
    assert rates["events"] == 13
    assert rates["page_views"] == 5
    assert rates["impressions"] == 5
    assert rates["clicks"] == 1
    assert rates["ignores"] == 1
    assert rates["ad_closes"] == 1
    assert rates["reopens"] == 1
    assert rates["conversions_proxy"] == 1
    assert rates["sessions"] == 3
    assert rates["click_rate"] == 0.2
    assert rates["ignore_rate"] == 0.2
    assert rates["no_click_pages"] == 4
    assert rates["no_click_rate"] == 0.8
    assert rates["close_rate"] == 1 / 3
    assert rates["reopen_rate"] == 1 / 3
    assert rates["close_reopen_users"] == 1
    assert rates["users_with_close"] == 1
    assert rates["close_reopen_rate"] == 1
    assert rates["conversion_rate_proxy"] == 1 / 3

    fatigue = {item["exposure_bucket"]: item for item in snapshot["fatigue"]}
    assert fatigue["1"]["impressions"] == 3 and fatigue["1"]["clicks"] == 1
    assert fatigue["2"]["impressions"] == 2 and fatigue["2"]["clicks"] == 0
    assert fatigue["10+"]["impressions"] == 0

    assert snapshot["session_breaks"]["gaps"] == 1
    assert snapshot["session_breaks"]["examples"][0]["gap_seconds"] == 1968
    endings = {item["last_event_type"]: item["users"] for item in snapshot["session_end_proxy"]}
    assert endings["ad_close"] == 1
    assert endings["purchase"] == 1
    assert endings["page_view"] == 1

    by_uid = {item["uid"]: item for item in snapshot["power_users"]}
    assert set(by_uid) == {"A"}
    assert by_uid["A"]["events"] == 11
    assert by_uid["A"]["sessions"] == 2
    assert by_uid["A"]["power_score"] == 11 + 10 * 2 + 20
    assert set(by_uid["A"]["pages"]) == {"/home", "/pricing", "/checkout"}
    assert snapshot["featured_journey"]["uid"] == "A"
    assert snapshot["featured_journey"]["steps"][0]["seconds_since_prev"] is None

    pages = {item["page_path"]: item for item in snapshot["page_times"]}
    assert pages["/home"]["views"] == 2
    assert pages["/home"]["engagement_seconds"] == 7.0
    assert pages["/pricing"]["avg_engagement_seconds"] == 8.0

    assert snapshot["live_15min"]["page_views"] == 1
    assert snapshot["live_15min"]["saw"] == 1
    assert snapshot["live_15min"]["conversions"] == 1
    assert snapshot["live_15min"]["clicks"] == 0
    assert MANY_EVENTS == 15
    assert SESSION_GAP_SECONDS == 1800
