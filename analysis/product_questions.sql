-- Product questions for Interaction Insights.
-- Source: gs://goods-ga4-backup_analysis events-000000000000.parquet
-- through events-000000000049.parquet, loaded into ga4_events.
-- Replace YOUR_PROJECT. Views always read the latest replay_run_id
-- (see v_events). Sandbox-safe: SELECT only.

-- Q1. Click rate, ignore rate, and conversions per session.
SELECT * FROM `YOUR_PROJECT.interaction_insights.v_reaction_rates`;

-- Q2. Engagement time per page (sum of engagement_time_msec / page views).
SELECT *
FROM `YOUR_PROJECT.interaction_insights.v_page_time`
ORDER BY engagement_seconds DESC
LIMIT 12;

-- Q2. Ordered events per session, with seconds between steps
-- measured on the export timestamps.
SELECT *
FROM `YOUR_PROJECT.interaction_insights.v_user_journey`
WHERE uid = 'USER_ID'
ORDER BY step_index;

-- Q2. Most common first-four-step paths.
SELECT *
FROM `YOUR_PROJECT.interaction_insights.v_journey_patterns`
LIMIT 20;

-- Q3. Last event in each session.
SELECT * FROM `YOUR_PROJECT.interaction_insights.v_session_end_proxy`;

-- Q3. Gaps of 30 minutes (1800 seconds) or more.
SELECT *
FROM `YOUR_PROJECT.interaction_insights.v_session_breaks`
ORDER BY gap_seconds DESC
LIMIT 50;

-- Q4. Power users: 15+ events, or 3+ sessions, or 8+ page views, or a conversion.
-- power_score = events + 10 * sessions + 20 * conversions.
SELECT *
FROM `YOUR_PROJECT.interaction_insights.v_power_users`
ORDER BY power_score DESC
LIMIT 50;

-- Live scorecards, events per minute, source funnel, fatigue.
SELECT * FROM `YOUR_PROJECT.interaction_insights.v_live_15min`;
SELECT * FROM `YOUR_PROJECT.interaction_insights.v_funnel_minute` ORDER BY minute;
SELECT * FROM `YOUR_PROJECT.interaction_insights.v_campaign_funnel` ORDER BY impressions DESC LIMIT 20;
SELECT * FROM `YOUR_PROJECT.interaction_insights.v_fatigue` ORDER BY bucket_order;
SELECT * FROM `YOUR_PROJECT.interaction_insights.v_event_counts` LIMIT 12;
