"""BigQuery view definitions for the GA4 backup. SELECT and CREATE VIEW only."""

from __future__ import annotations

import re

from interaction_insights.constants import (
    JOURNEY_STEPS,
    MANY_EVENTS,
    MANY_PAGE_VIEWS,
    MANY_SESSIONS,
    POWER_CONVERSION_WEIGHT,
    POWER_SESSION_WEIGHT,
    SESSION_GAP_SECONDS,
)

PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
DATASET_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,1023}$")

VIEW_NAMES = (
    "v_events",
    "v_reaction_rates",
    "v_page_time",
    "v_user_journey",
    "v_journey_patterns",
    "v_session_end_proxy",
    "v_session_breaks",
    "v_power_users",
    "v_live_15min",
    "v_funnel_minute",
    "v_campaign_funnel",
    "v_fatigue",
    "v_event_counts",
)

CLICKS = "event_name IN ('click', 'ad_click')"
CONVERTS = "event_name IN ('purchase', 'sign_up', 'subscribe', 'ad_free_subscribe')"


def assert_safe_identifier(value: str, kind: str) -> str:
    pattern = PROJECT_RE if kind == "project" else DATASET_RE
    if not value or not pattern.match(value):
        raise ValueError(f"Unsafe {kind} id: {value!r}")
    return value


def render_view_statements(project: str, dataset: str) -> list[str]:
    project = assert_safe_identifier(project, "project")
    dataset = assert_safe_identifier(dataset, "dataset")
    table = f"`{project}.{dataset}.ga4_events`"
    events = f"`{project}.{dataset}.v_events`"
    return [
        f"""
CREATE OR REPLACE VIEW {events} AS
SELECT *
FROM {table}
WHERE replay_run_id = (
  SELECT MAX(replay_run_id) FROM {table}
)
""".strip(),
        f"""
CREATE OR REPLACE VIEW `{project}.{dataset}.v_reaction_rates` AS
SELECT
  COUNT(*) AS events,
  COUNTIF(event_name = 'page_view') AS page_views,
  COUNTIF({CLICKS}) AS clicks,
  COUNTIF(event_name = 'ad_ignore') AS ignores,
  COUNTIF(event_name = 'ad_close') AS ad_closes,
  COUNTIF(event_name = 'app_reopen') AS reopens,
  COUNTIF({CONVERTS}) AS conversions_proxy,
  COUNT(DISTINCT CONCAT(user_pseudo_id, ':', IFNULL(session_id, ''))) AS sessions,
  SAFE_DIVIDE(COUNTIF({CLICKS}), COUNTIF(event_name = 'page_view')) AS click_rate,
  SAFE_DIVIDE(COUNTIF(event_name = 'ad_ignore'), COUNTIF(event_name = 'page_view')) AS ignore_rate,
  SAFE_DIVIDE(
    COUNTIF({CONVERTS}),
    COUNT(DISTINCT CONCAT(user_pseudo_id, ':', IFNULL(session_id, '')))
  ) AS conversion_rate_proxy
FROM {events}
""".strip(),
        f"""
CREATE OR REPLACE VIEW `{project}.{dataset}.v_page_time` AS
SELECT
  page_path,
  COUNTIF(event_name = 'page_view') AS views,
  SUM(engagement_time_msec) / 1000.0 AS engagement_seconds,
  SAFE_DIVIDE(
    SUM(engagement_time_msec) / 1000.0,
    NULLIF(COUNTIF(event_name = 'page_view'), 0)
  ) AS avg_engagement_seconds
FROM {events}
WHERE page_path IS NOT NULL AND page_path != ''
GROUP BY page_path
ORDER BY engagement_seconds DESC
""".strip(),
        f"""
CREATE OR REPLACE VIEW `{project}.{dataset}.v_event_counts` AS
SELECT event_name, COUNT(*) AS events
FROM {events}
GROUP BY event_name
ORDER BY events DESC
""".strip(),
        f"""
CREATE OR REPLACE VIEW `{project}.{dataset}.v_user_journey` AS
SELECT
  user_pseudo_id AS uid,
  session_id,
  ROW_NUMBER() OVER (
    PARTITION BY user_pseudo_id, session_id
    ORDER BY event_timestamp_micros, event_id
  ) AS step_index,
  event_name,
  IF(event_name = 'page_view' AND page_path IS NOT NULL AND page_path != '', page_path, event_name) AS step_label,
  page_path,
  event_time,
  CAST((event_timestamp_micros - LAG(event_timestamp_micros) OVER (
    PARTITION BY user_pseudo_id, session_id
    ORDER BY event_timestamp_micros, event_id
  )) / 1000000 AS INT64) AS seconds_since_prev
FROM {events}
""".strip(),
        f"""
CREATE OR REPLACE VIEW `{project}.{dataset}.v_journey_patterns` AS
WITH ordered AS (
  SELECT
    user_pseudo_id,
    session_id,
    IF(event_name = 'page_view' AND page_path IS NOT NULL AND page_path != '', page_path, event_name) AS step_label,
    ROW_NUMBER() OVER (
      PARTITION BY user_pseudo_id, session_id
      ORDER BY event_timestamp_micros, event_id
    ) AS step_index
  FROM {events}
),
paths AS (
  SELECT
    CONCAT(user_pseudo_id, ':', IFNULL(session_id, '')) AS session_key,
    STRING_AGG(step_label, ' > ' ORDER BY step_index) AS path
  FROM ordered
  WHERE step_index <= {JOURNEY_STEPS}
  GROUP BY session_key
)
SELECT path, COUNT(*) AS users
FROM paths
GROUP BY path
ORDER BY users DESC, path
""".strip(),
        f"""
CREATE OR REPLACE VIEW `{project}.{dataset}.v_session_end_proxy` AS
WITH ordered AS (
  SELECT
    user_pseudo_id,
    session_id,
    event_name,
    ROW_NUMBER() OVER (
      PARTITION BY user_pseudo_id, session_id
      ORDER BY event_timestamp_micros DESC, event_id DESC
    ) AS rn
  FROM {events}
)
SELECT event_name AS last_event_type, COUNT(*) AS users
FROM ordered
WHERE rn = 1
GROUP BY event_name
ORDER BY users DESC
""".strip(),
        f"""
CREATE OR REPLACE VIEW `{project}.{dataset}.v_session_breaks` AS
WITH ordered AS (
  SELECT
    user_pseudo_id,
    event_name,
    event_timestamp_micros,
    LAG(event_timestamp_micros) OVER w AS prev_micros,
    LAG(event_name) OVER w AS prev_event_type
  FROM {events}
  WINDOW w AS (PARTITION BY user_pseudo_id ORDER BY event_timestamp_micros, event_id)
)
SELECT
  user_pseudo_id AS uid,
  prev_event_type,
  event_name AS event_type,
  CAST(prev_micros / 1000000 AS INT64) AS prev_source_timestamp,
  CAST(event_timestamp_micros / 1000000 AS INT64) AS source_timestamp,
  CAST((event_timestamp_micros - prev_micros) / 1000000 AS INT64) AS gap_seconds
FROM ordered
WHERE prev_micros IS NOT NULL
  AND (event_timestamp_micros - prev_micros) / 1000000 >= {SESSION_GAP_SECONDS}
""".strip(),
        f"""
CREATE OR REPLACE VIEW `{project}.{dataset}.v_power_users` AS
WITH per_user AS (
  SELECT
    user_pseudo_id AS uid,
    COUNT(*) AS events,
    COUNT(DISTINCT session_id) AS sessions,
    COUNTIF({CLICKS}) AS clicks,
    COUNTIF(event_name = 'page_view') AS page_views,
    COUNTIF({CONVERTS}) AS conversions,
    SUM(engagement_time_msec) / 1000.0 AS engagement_seconds
  FROM {events}
  GROUP BY user_pseudo_id
)
SELECT
  uid,
  events,
  sessions,
  clicks,
  page_views,
  conversions,
  engagement_seconds,
  events + {POWER_SESSION_WEIGHT} * sessions + {POWER_CONVERSION_WEIGHT} * conversions AS power_score
FROM per_user
WHERE events >= {MANY_EVENTS}
   OR sessions >= {MANY_SESSIONS}
   OR page_views >= {MANY_PAGE_VIEWS}
   OR conversions >= 1
""".strip(),
        f"""
CREATE OR REPLACE VIEW `{project}.{dataset}.v_live_15min` AS
WITH bounds AS (
  SELECT MAX(event_time) AS max_time FROM {events}
),
recent AS (
  SELECT e.*
  FROM {events} AS e
  CROSS JOIN bounds AS b
  WHERE b.max_time IS NOT NULL
    AND e.event_time >= TIMESTAMP_SUB(b.max_time, INTERVAL 15 MINUTE)
)
SELECT
  COUNTIF(event_name = 'page_view') AS saw,
  COUNTIF({CLICKS}) AS clicked,
  COUNTIF(event_name = 'ad_ignore') AS ignored,
  COUNTIF({CONVERTS}) AS converted_proxy,
  SAFE_DIVIDE(COUNTIF({CLICKS}), COUNTIF(event_name = 'page_view')) AS ctr
FROM recent
""".strip(),
        f"""
CREATE OR REPLACE VIEW `{project}.{dataset}.v_funnel_minute` AS
SELECT
  TIMESTAMP_TRUNC(event_time, MINUTE) AS minute,
  COUNTIF(event_name = 'page_view') AS impressions,
  COUNTIF({CLICKS}) AS clicks,
  COUNTIF(event_name = 'ad_ignore') AS ignores,
  COUNTIF({CONVERTS}) AS conversions
FROM {events}
GROUP BY minute
ORDER BY minute
""".strip(),
        f"""
CREATE OR REPLACE VIEW `{project}.{dataset}.v_campaign_funnel` AS
SELECT
  COALESCE(NULLIF(source, ''), '(direct)') AS campaign,
  COUNT(*) AS impressions,
  COUNTIF({CLICKS}) AS clicks,
  COUNTIF(event_name = 'page_view') AS page_views,
  SAFE_DIVIDE(COUNTIF({CLICKS}), COUNTIF(event_name = 'page_view')) AS ctr
FROM {events}
GROUP BY campaign
ORDER BY impressions DESC
""".strip(),
        f"""
CREATE OR REPLACE VIEW `{project}.{dataset}.v_fatigue` AS
WITH pages AS (
  SELECT
    user_pseudo_id,
    session_id,
    event_timestamp_micros AS ts,
    event_id,
    LEAD(event_timestamp_micros) OVER (
      PARTITION BY user_pseudo_id, session_id
      ORDER BY event_timestamp_micros, event_id
    ) AS next_ts,
    ROW_NUMBER() OVER (
      PARTITION BY user_pseudo_id, session_id
      ORDER BY event_timestamp_micros, event_id
    ) AS exposure_n
  FROM {events}
  WHERE event_name = 'page_view'
),
clicks AS (
  SELECT user_pseudo_id, session_id, event_timestamp_micros AS ts
  FROM {events}
  WHERE {CLICKS}
),
flagged AS (
  SELECT
    p.exposure_n,
    IF(COUNT(c.ts) > 0, 1, 0) AS clicked
  FROM pages AS p
  LEFT JOIN clicks AS c
    ON c.user_pseudo_id = p.user_pseudo_id
   AND c.session_id = p.session_id
   AND c.ts >= p.ts
   AND (p.next_ts IS NULL OR c.ts < p.next_ts)
  GROUP BY p.user_pseudo_id, p.session_id, p.ts, p.event_id, p.exposure_n
)
SELECT
  CASE
    WHEN exposure_n = 1 THEN '1'
    WHEN exposure_n = 2 THEN '2'
    WHEN exposure_n = 3 THEN '3'
    WHEN exposure_n = 4 THEN '4'
    WHEN exposure_n BETWEEN 5 AND 9 THEN '5-9'
    ELSE '10+'
  END AS exposure_bucket,
  CASE
    WHEN exposure_n = 1 THEN 1
    WHEN exposure_n = 2 THEN 2
    WHEN exposure_n = 3 THEN 3
    WHEN exposure_n = 4 THEN 4
    WHEN exposure_n BETWEEN 5 AND 9 THEN 5
    ELSE 6
  END AS bucket_order,
  COUNT(*) AS impressions,
  SUM(clicked) AS clicks,
  SAFE_DIVIDE(SUM(clicked), COUNT(*)) AS ctr
FROM flagged
GROUP BY exposure_bucket, bucket_order
ORDER BY bucket_order
""".strip(),
    ]


def read_queries(project: str, dataset: str) -> dict[str, str]:
    project = assert_safe_identifier(project, "project")
    dataset = assert_safe_identifier(dataset, "dataset")

    def view(name: str) -> str:
        return f"`{project}.{dataset}.{name}`"

    return {
        "reaction_rates": f"SELECT * FROM {view('v_reaction_rates')}",
        "live_15min": f"SELECT * FROM {view('v_live_15min')}",
        "funnel_minute": f"SELECT * FROM {view('v_funnel_minute')} ORDER BY minute",
        "campaign_funnel": (
            f"SELECT * FROM {view('v_campaign_funnel')} ORDER BY impressions DESC LIMIT 20"
        ),
        "fatigue": f"SELECT * FROM {view('v_fatigue')} ORDER BY bucket_order",
        "journey_patterns": f"SELECT * FROM {view('v_journey_patterns')} LIMIT 20",
        "session_end_proxy": f"SELECT * FROM {view('v_session_end_proxy')}",
        "session_break_count": f"SELECT COUNT(*) AS gaps FROM {view('v_session_breaks')}",
        "session_break_examples": (
            f"SELECT * FROM {view('v_session_breaks')} ORDER BY gap_seconds DESC LIMIT 8"
        ),
        "power_users": (
            f"SELECT * FROM {view('v_power_users')} ORDER BY power_score DESC LIMIT 50"
        ),
        "page_time": (
            f"SELECT * FROM {view('v_page_time')} ORDER BY engagement_seconds DESC LIMIT 12"
        ),
        "event_counts": f"SELECT * FROM {view('v_event_counts')} LIMIT 12",
        "run_info": (
            "SELECT COUNT(*) AS events, ANY_VALUE(replay_run_id) AS replay_run_id, "
            "MIN(event_time) AS event_time_min, MAX(event_time) AS event_time_max "
            f"FROM {view('v_events')}"
        ),
        "featured_user": (
            f"SELECT uid, power_score FROM {view('v_power_users')} "
            "ORDER BY power_score DESC LIMIT 1"
        ),
    }


def featured_journey_sql(project: str, dataset: str) -> str:
    project = assert_safe_identifier(project, "project")
    dataset = assert_safe_identifier(dataset, "dataset")
    return (
        f"SELECT * FROM `{project}.{dataset}.v_user_journey` "
        "WHERE uid = @uid ORDER BY step_index LIMIT 16"
    )
