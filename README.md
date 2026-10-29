# Interaction Insights

A product-analytics desk for a GA4 backup. Reaction rates, page time, journeys, session gaps, and power users are computed from the same events BigQuery stores after the load.

The source files are the parquet shards in `gs://goods-ga4-backup_analysis`:

`events-000000000000.parquet` through `events-000000000049.parquet` (50 shards).

`replay.py` flattens those events and appends them to BigQuery with load jobs. The dashboard and Looker Studio then read the views. A local preview runs without cloud keys. Until the shards are in `data/`, that preview is a labeled stand-in with the same schema. It is not the export, and it is not written to BigQuery unless you pass `--allow-sample`.

## Run the dashboard

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python live_app.py --port 8765 --speed 80 --max-rows 1000
```

Open http://127.0.0.1:8765/

- With `GCP_PROJECT`, a service-account JSON key, and `GA4_PROPERTY_ID` in `.env`, the page reads BigQuery and the GA4 Data API.
- Without those keys it previews local parquet if `data/events-*.parquet` is present, otherwise the stand-in.

Python 3.11 or newer. The commands below match a Mac setup; see [MAC_SETUP.md](MAC_SETUP.md).

## Load BigQuery

```bash
chmod +x scripts/download_ga4.sh
./scripts/download_ga4.sh
python setup_bq.py
python replay.py --speed 60 --max-rows 50000
```

`scripts/download_ga4.sh` runs `gcloud storage cp` for all 50 shards into `data/`. The bucket is private, so `gcloud auth login` has to succeed on your machine. `--speed` is events per second. Load jobs flush about every 60 seconds (`--flush-seconds`). `--dry-run` prints counts and does not call BigQuery.

This loader does not send the backup through the Measurement Protocol. The rows are already GA4 events, and sending them again would double-count. `--no-ga4` is accepted and ignored.

Sandbox rules the loader follows: no streaming inserts, no `INSERT` / `UPDATE` / `DELETE` / `MERGE`, only load jobs plus `SELECT` and `CREATE VIEW`. The table `ga4_events` expires about 60 days after it is created. Views keep the latest `replay_run_id`.

## Tests

```bash
pytest -q
```

## Where each answer lives

| Question | BigQuery view | What it uses |
|---|---|---|
| Click, ignore, convert | `v_reaction_rates` | clicks and `ad_ignore` over `page_view`; conversions over sessions |
| Close ad, reopen | `v_event_counts` | `ad_close`, `app_reopen` |
| Time on page | `v_page_time` | sum of `engagement_time_msec` ÷ page views |
| Journey | `v_journey_patterns`, `v_user_journey` | first four steps of each session |
| Why a session ended | `v_session_end_proxy`, `v_session_breaks` | last event in the session; gaps of 30 minutes |
| Power users | `v_power_users` | 15+ events, 3+ sessions, 8+ page views, or a conversion |

Definitions, Looker pages, and the optional GA4 Data API overlay are in [PRODUCT_ANALYTICS.md](PRODUCT_ANALYTICS.md), [LOOKER_STUDIO.md](LOOKER_STUDIO.md), and [GA4.md](GA4.md).

Do not commit the parquet shards, the JSON key, or a filled-in `.env`.
