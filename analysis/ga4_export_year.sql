CREATE TEMP TABLE base AS
SELECT
  event_timestamp,
  event_name,
  user_pseudo_id AS uid,
  (SELECT COALESCE(value.int_value, SAFE_CAST(value.string_value AS INT64))
     FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS session_id,
  (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'firebase_screen_class') AS screen_class,
  (SELECT COALESCE(value.int_value, CAST(value.double_value AS INT64), SAFE_CAST(value.string_value AS INT64))
     FROM UNNEST(event_params) WHERE key = 'seconds') AS exit_seconds
FROM `goodsuniteus-4441a.analytics_175644420.export_raw_events_20251028_20261027`
WHERE user_pseudo_id IS NOT NULL;

CREATE TEMP TABLE marked AS
SELECT
  *,
  LAG(screen_class) OVER (
    PARTITION BY uid, session_id ORDER BY event_timestamp, event_name
  ) AS prev_screen,
  ROW_NUMBER() OVER (
    PARTITION BY uid, session_id ORDER BY event_timestamp DESC, event_name DESC
  ) = 1 AS is_last,
  (
    REGEXP_CONTAINS(event_name, r'(?i)(_click|_tapped)$')
    OR event_name IN ('brand_list_tap', 'tab_selected', 'menu_item_selected', 'brand_follow')
  ) AS is_click,
  event_name = 'interstitial_shown' AS ad_shown,
  event_name = 'interstitial_did_dismiss' AS ad_close,
  event_name = 'app_open_shown' AS open_shown,
  event_name = 'app_open_skipped' AS open_skipped,
  event_name = 'in_app_purchase' AS is_purchase,
  event_name = 'session_start' AS is_start
FROM base
WHERE session_id IS NOT NULL;

CREATE TEMP TABLE sequenced AS
SELECT
  * EXCEPT(prev_screen),
  SUM(IF(screen_class IS NOT NULL AND screen_class IS DISTINCT FROM prev_screen, 1, 0))
    OVER (PARTITION BY uid, session_id ORDER BY event_timestamp, event_name) AS screen_seq
FROM marked;

CREATE TEMP TABLE visits AS
SELECT
  uid,
  session_id,
  screen_seq,
  MAX(screen_class) AS screen,
  MIN(event_timestamp) AS start_ts,
  MAX(IF(is_click, 1, 0)) = 1 AS clicked
FROM sequenced
WHERE screen_seq > 0
GROUP BY 1, 2, 3;

CREATE TEMP TABLE visit_time AS
SELECT
  *,
  LEAD(start_ts) OVER (PARTITION BY uid, session_id ORDER BY screen_seq) AS next_ts
FROM visits;

CREATE TEMP TABLE paths AS
SELECT
  uid,
  session_id,
  STRING_AGG(screen, ' > ' ORDER BY screen_seq) AS path
FROM visits
WHERE screen_seq <= 4
GROUP BY 1, 2;

CREATE TEMP TABLE user_stats AS
SELECT
  s.uid,
  COUNT(DISTINCT s.session_id) AS sessions,
  COUNT(*) AS events,
  COUNTIF(s.is_purchase) AS purchases,
  IFNULL(v.visits, 0) AS visits,
  IFNULL(v.screens, 0) AS screens
FROM sequenced s
LEFT JOIN (
  SELECT uid, COUNT(*) AS visits, COUNT(DISTINCT screen) AS screens
  FROM visits
  GROUP BY 1
) v USING (uid)
GROUP BY s.uid, v.visits, v.screens;

CREATE TEMP TABLE power AS
SELECT *
FROM user_stats
WHERE sessions >= 8 OR purchases >= 1 OR visits >= 30;

SELECT TO_JSON_STRING(STRUCT(
  (SELECT AS STRUCT
      COUNT(*) AS events,
      COUNT(DISTINCT uid) AS users,
      COUNTIF(session_id IS NULL) AS events_without_session,
      MIN(TIMESTAMP_MICROS(event_timestamp)) AS event_time_min,
      MAX(TIMESTAMP_MICROS(event_timestamp)) AS event_time_max
    FROM base) AS coverage,
  (SELECT AS STRUCT
      COUNT(*) AS events,
      COUNT(DISTINCT CONCAT(uid, '-', CAST(session_id AS STRING))) AS sessions,
      COUNT(DISTINCT uid) AS users,
      COUNTIF(is_click) AS clicks,
      (SELECT COUNT(*) FROM visits) AS screen_visits,
      (SELECT COUNTIF(NOT clicked) FROM visits) AS no_click_visits,
      COUNTIF(ad_shown) AS ads_shown,
      COUNTIF(ad_close) AS ads_closed,
      COUNTIF(open_shown) AS app_open_shown,
      COUNTIF(open_skipped) AS app_open_skipped,
      COUNTIF(is_purchase) AS purchases,
      COUNTIF(event_name = 'notification_dismiss') AS notification_dismisses,
      COUNTIF(event_name = 'subscribe_tapped') AS subscribe_taps
    FROM sequenced) AS rates,
  (SELECT AS STRUCT
      COUNTIF(shown AND NOT closed) AS ignored_sessions,
      COUNTIF(shown) AS shown_sessions,
      COUNTIF(closed) AS closed_sessions
    FROM (
      SELECT
        LOGICAL_OR(ad_shown) AS shown,
        LOGICAL_OR(ad_close) AS closed
      FROM sequenced
      GROUP BY uid, session_id
    )) AS ads,
  (SELECT AS STRUCT
      (SELECT COUNT(DISTINCT uid) FROM sequenced WHERE ad_close) AS users_with_close,
      (SELECT COUNT(DISTINCT c.uid)
        FROM (
          SELECT uid, session_id, MIN(event_timestamp) AS ts
          FROM sequenced WHERE ad_close GROUP BY 1, 2
        ) c
        JOIN (
          SELECT uid, session_id, MIN(event_timestamp) AS ts
          FROM sequenced WHERE is_start GROUP BY 1, 2
        ) s
          ON c.uid = s.uid AND s.session_id != c.session_id AND s.ts > c.ts
      ) AS close_reopen_users
  ) AS reopen,
  (SELECT AS STRUCT
      COUNT(*) AS exits,
      ROUND(AVG(IF(exit_seconds BETWEEN 0 AND 1800, exit_seconds, NULL)), 1) AS avg_seconds,
      APPROX_QUANTILES(IF(exit_seconds BETWEEN 0 AND 1800, exit_seconds, NULL), 100)[OFFSET(50)] AS median_seconds
    FROM base
    WHERE event_name = 'brand_detail_exit') AS brand_exit,
  (SELECT COUNTIF(gap_seconds >= 1800) FROM (
      SELECT
        (event_timestamp - LAG(event_timestamp) OVER (PARTITION BY uid ORDER BY event_timestamp)) / 1000000 AS gap_seconds
      FROM base
    )) AS gaps_30min,
  ARRAY(
    SELECT AS STRUCT screen AS page_path, views,
      ROUND(seconds, 1) AS engagement_seconds,
      ROUND(SAFE_DIVIDE(seconds, views), 1) AS avg_engagement_seconds
    FROM (
      SELECT
        screen,
        COUNT(*) AS views,
        SUM(LEAST(1800, GREATEST(0, IFNULL(next_ts, start_ts) - start_ts) / 1000000)) AS seconds
      FROM visit_time
      GROUP BY 1
    )
    ORDER BY seconds DESC
    LIMIT 12
  ) AS page_times,
  ARRAY(
    SELECT AS STRUCT path, COUNT(*) AS users
    FROM paths
    WHERE path IS NOT NULL AND path != ''
    GROUP BY 1
    ORDER BY users DESC
    LIMIT 12
  ) AS journeys,
  ARRAY(
    SELECT AS STRUCT event_name AS last_event_type, COUNT(*) AS users
    FROM sequenced
    WHERE is_last
    GROUP BY 1
    ORDER BY users DESC
    LIMIT 12
  ) AS endings,
  (SELECT AS STRUCT
      COUNT(*) AS power_users,
      COUNTIF(sessions >= 3) AS sessions_3,
      COUNTIF(sessions >= 8) AS sessions_8,
      COUNTIF(sessions >= 15) AS sessions_15,
      COUNTIF(purchases >= 1) AS buyers,
      COUNTIF(visits >= 30) AS heavy_screens,
      ROUND(AVG(sessions), 1) AS avg_sessions,
      ROUND(AVG(visits), 1) AS avg_visits,
      ROUND(AVG(events), 1) AS avg_events
    FROM power) AS power_summary,
  (SELECT AS STRUCT
      COUNT(*) AS users,
      COUNTIF(sessions >= 3) AS sessions_3,
      COUNTIF(sessions >= 8) AS sessions_8,
      COUNTIF(purchases >= 1) AS buyers
    FROM user_stats) AS user_summary,
  ARRAY(
    SELECT AS STRUCT screen AS page_path, COUNT(*) AS views
    FROM visit_time
    WHERE uid IN (SELECT uid FROM power)
    GROUP BY 1
    ORDER BY views DESC
    LIMIT 8
  ) AS power_screens,
  ARRAY(
    SELECT AS STRUCT path, COUNT(*) AS users
    FROM paths
    WHERE uid IN (SELECT uid FROM power)
    GROUP BY 1
    ORDER BY users DESC
    LIMIT 8
  ) AS power_journeys
)) AS answers;
