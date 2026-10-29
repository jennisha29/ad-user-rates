# Google Analytics 4

The product reads the backup in `gs://goods-ga4-backup_analysis`. Those parquet shards are already a GA4 export. `replay.py` loads them into BigQuery and does not post them to the Measurement Protocol. Sending the same events again would double-count.

## What the loader keeps

Each row becomes one `ga4_events` record:

| Field | Taken from |
|---|---|
| `event_name` | `event_name` |
| `event_timestamp_micros` | `event_timestamp` (seconds, milliseconds, or microseconds) or `event_date` |
| `user_pseudo_id` | `user_pseudo_id`, else `user_id` |
| `session_id` | `event_params.ga_session_id`, else a 30-minute gap id |
| `page_path` | `page_path`, else the path of `page_location` |
| `engagement_time_msec` | `event_params.engagement_time_msec` |
| `source`, `medium` | `traffic_source`, else event params |
| `device_category` | `device.category` |

Nested `event_params` (`key` plus `string_value` / `int_value` / `float_value` / `double_value`) are flattened on read.

## Reading the live property (optional)

The dashboard can also call the GA4 Data API when these are set:

| Variable | Used by |
|---|---|
| `GA4_PROPERTY_ID` | numeric property id, Admin → Property settings |
| `GOOGLE_APPLICATION_CREDENTIALS` | service account with Viewer on that property |

`GA4_PROPERTY_ID` is not the `G-` measurement id.

1. Enable the Google Analytics Data API on the Cloud project.
2. Admin → Property access management → add the service account as Viewer.
3. Put the numeric id in `.env`.
4. Restart `live_app.py`.

The overlay returns realtime events, 7-day event counts, page engagement (`userEngagementDuration / screenPageViews`), landing pages, and new vs returning. Path exploration, funnel exploration, retention, and a Power users audience still live in the GA4 interface.

Page time and journeys on the main scorecards come from the backup, not from this overlay.
