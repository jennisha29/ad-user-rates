"""Shared definitions for the GA4 backup views and the local metrics."""

MANY_EVENTS = 15
MANY_SESSIONS = 3
MANY_PAGE_VIEWS = 8
SESSION_GAP_SECONDS = 30 * 60
POWER_SESSION_WEIGHT = 10
POWER_CONVERSION_WEIGHT = 20
LIVE_WINDOW_MINUTES = 15
JOURNEY_STEPS = 4
TABLE_EXPIRATION_DAYS = 60

CLICK_EVENTS = frozenset({"click", "ad_click"})
IGNORE_EVENTS = frozenset({"ad_ignore"})
CLOSE_EVENTS = frozenset({"ad_close"})
REOPEN_EVENTS = frozenset({"app_reopen"})
CONVERT_EVENTS = frozenset({"purchase", "sign_up", "subscribe", "ad_free_subscribe"})
PAGE_EVENTS = frozenset({"page_view"})

EXPOSURE_BUCKETS = (
    ("1", 1),
    ("2", 2),
    ("3", 3),
    ("4", 4),
    ("5-9", 5),
    ("10+", 6),
)

DEFINITIONS = {
    "click_rate": "Clicks (click and ad_click) divided by page views.",
    "ignore_rate": "ad_ignore events divided by page views. Zero when the export has no ad_ignore events.",
    "conversion_rate": "purchase, sign_up, subscribe, and ad_free_subscribe events divided by sessions.",
    "power_user": (
        f"{MANY_EVENTS} or more events, or {MANY_SESSIONS} or more sessions, "
        f"or {MANY_PAGE_VIEWS} or more page views, or a conversion event."
    ),
    "power_score": (
        f"events + {POWER_SESSION_WEIGHT} × sessions + {POWER_CONVERSION_WEIGHT} × conversions."
    ),
    "session_break": "A gap of 30 minutes or more between a user's events.",
    "live_window": "Events in the last 15 minutes of event time in this run.",
    "time_on_page": "Sum of engagement_time_msec on a page, divided by page views.",
    "fatigue": "Of the nth page view in a session, how often a click happens before the next page view.",
}
