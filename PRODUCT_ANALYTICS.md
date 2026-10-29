# Product questions

Every number below is computed from the GA4 backup (`gs://goods-ga4-backup_analysis`, shards `events-000000000000.parquet` through `events-000000000049.parquet`) after it is flattened into `ga4_events`.

Each view reads `v_events`, which is the latest `replay_run_id` only. Re-running `replay.py` does not require a delete.

Click events are `click` and `ad_click`. Ignore events are `ad_ignore`. Conversions are `purchase`, `sign_up`, `subscribe`, and `ad_free_subscribe`. Page views are `page_view`. A session is `user_pseudo_id` plus `session_id`. Blank session ids are filled with a 30-minute gap before the load (`gap-N`).

## Q1. How do users react?

| Reaction | How it is measured |
|---|---|
| Click | `click_rate` = clicks ÷ page views |
| Ignore | `ignore_rate` = `ad_ignore` ÷ page views. Zero when the export has no `ad_ignore` events. |
| Convert | `conversion_rate_proxy` = conversion events ÷ sessions |
| Close ad | count of `ad_close` |
| Reopen | count of `app_reopen` |

```sql
SELECT * FROM `YOUR_PROJECT.interaction_insights.v_reaction_rates`;
```

The optional GA4 Data API overlay (Reports → Realtime, Engagement → Events) shows the live property. It is not a second copy of the backup.

## Q2. Time on a page, and the journey

`v_page_time` sums `engagement_time_msec` on each `page_path` and divides by page views. A `page_view` is labeled with `page_path` (from `page_location` when the path column is empty). Other events keep their `event_name`.

| View | Meaning |
|---|---|
| `v_page_time` | Engagement seconds and average per page |
| `v_user_journey` | Ordered events for each session, plus seconds since the previous step |
| `v_journey_patterns` | Most common first four steps |

Seconds between steps use `event_timestamp` from the export, not the load clock.

```sql
SELECT * FROM `YOUR_PROJECT.interaction_insights.v_journey_patterns` LIMIT 20;
```

## Q3. What causes a session to end?

| View | Meaning |
|---|---|
| `v_session_end_proxy` | The last event name in each session, counted up |
| `v_session_breaks` | Gaps of at least 30 minutes (1800 seconds) between a user’s events |

```sql
SELECT * FROM `YOUR_PROJECT.interaction_insights.v_session_end_proxy`;
```

Path exploration, funnel drop-off, and retention in the GA4 interface remain the place to read intent. These views describe what the backup recorded.

## Q4. How do power users use the product?

A user is in `v_power_users` when any of these is true:

- 15 or more events
- 3 or more sessions
- 8 or more page views
- at least one conversion event

`power_score = events + 10 × sessions + 20 × conversions`.

```sql
SELECT *
FROM `YOUR_PROJECT.interaction_insights.v_power_users`
ORDER BY power_score DESC
LIMIT 50;
```

## Fatigue and the live window

`v_fatigue` asks, of the nth page view in a session, how often a click happens before the next page view. Buckets are 1, 2, 3, 4, 5–9, and 10+.

`v_live_15min` is the last 15 minutes of `event_time` in the latest run, ending at `MAX(event_time)`. A multi-day export can have a sparse window. The cumulative views still answer Q1–Q4.

`v_campaign_funnel` groups by traffic `source` (shown as campaign on the dashboard).
