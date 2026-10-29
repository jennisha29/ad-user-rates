# Looker Studio

The report reads the BigQuery views over `ga4_events`. It does not read the local stand-in.

1. Open [lookerstudio.google.com](https://lookerstudio.google.com) with the same Google account as the Cloud project.
2. Create → Report → BigQuery → the project and dataset `interaction_insights`.
3. Name the report **Interaction Insights**.
4. Add one page per view.

| Page | View | Charts |
|---|---|---|
| Live scorecards | `v_live_15min` | Page views, clicks, ignores, conversions, click rate |
| Events / minute | `v_funnel_minute` | Time series of page views, clicks, ignores, conversions |
| Source funnel | `v_campaign_funnel` | Bars plus a table. `campaign` is the traffic source |
| Fatigue | `v_fatigue` | Click rate by `exposure_bucket`, sorted by `bucket_order` |
| Reactions and rates | `v_reaction_rates` | Scorecards for click, ignore, and conversion rates |
| Page time | `v_page_time` | Table of engagement seconds and average |
| Journeys | `v_journey_patterns` | Table of `path` and `users` |
| Session end | `v_session_end_proxy` | Bar of `last_event_type` |
| Session breaks | `v_session_breaks` | Table of gaps of 30 minutes or more |
| Power users | `v_power_users` | Table sorted by `power_score` |

Set data freshness to the shortest option. Use Refresh data while `replay.py` is appending load jobs.

These views already filter to the latest `replay_run_id`. An older load stays in `ga4_events` until the table expires (about 60 days) and does not affect the report.
