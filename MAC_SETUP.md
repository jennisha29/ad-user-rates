# Mac setup

Python 3.11 or newer. The dashboard reads the GA4 backup parquet locally, and BigQuery plus the GA4 Data API when those are configured.

## 1. Python

```bash
python3.11 --version
```

If that command is missing, install Python 3.11 from [python.org](https://www.python.org/downloads/) or Homebrew (`brew install python@3.11`). Newer 3.x works too.

## 2. Clone and install

```bash
cd ~
git clone https://github.com/jennisha29/ad-user-rates.git
cd ad-user-rates
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Copy the GA4 backup

```bash
chmod +x scripts/download_ga4.sh
./scripts/download_ga4.sh
```

The backup already on this Mac is `$HOME/Downloads/ga4-export`: `events-000000000000.parquet` through `events-000000000999.parquet` (1000 files, 4.59 GB). Event time runs from 28 Oct 2025, 12:00 AM Eastern through 27 Oct 2026, 11:59 PM Eastern.

Count that folder. Do not link the shards from the home directory again.

```bash
cd ~/ad-user-rates
source .venv/bin/activate
python scripts/build_report.py "$HOME/Downloads/ga4-export"
```

That writes `data/report.json`. The dashboard then serves those counts. They come from the parquet files, not from a live GA4 Data API call and not from BigQuery.

`scripts/download_ga4.sh` copies every `events-*.parquet` object into `data/` if you need the shards in the repo folder. The bucket is private. Install the [Google Cloud SDK](https://cloud.google.com/sdk/docs/install), then `gcloud auth login`. The files stay on disk (`data/*` is gitignored).

You can open the dashboard before the report exists. It shows a labeled stand-in. The stand-in is not the export.

## 4. Configure `.env`

```bash
cp .env.example .env
open -e .env
```

```bash
GOOGLE_APPLICATION_CREDENTIALS=/Users/YOUR_MAC_USERNAME/keys/interaction-insights.json
GCP_PROJECT=your-project-id-here
BQ_DATASET=interaction_insights
BQ_LOCATION=US
SPEED=60
MAX_ROWS=50000
GA4_PROPERTY_ID=
```

`GA4_PROPERTY_ID` is the numeric property id (Admin → Property settings). It is optional and only used to overlay live Data API reports. The parquet backup does not need it.

## 5. Create BigQuery objects and load

```bash
source .venv/bin/activate
python setup_bq.py
python replay.py --speed 60 --max-rows 50000
```

`setup_bq.py` creates dataset `interaction_insights`, table `ga4_events`, and the views. Creating a dataset needs `bigquery.datasets.create` (included in BigQuery Admin). If you already created the empty dataset in the console, BigQuery Job User and BigQuery Data Editor are enough for tables, views, and load jobs.

`python replay.py --dry-run` reads the shards (or the stand-in) and prints counts without calling BigQuery.

## 6. Dashboard

```bash
python live_app.py --port 8765 --speed 80 --max-rows 1000
```

Open http://127.0.0.1:8765/

With the key and project set, and after a load, “Read BigQuery and GA4” queries the views. `--speed` and `--max-rows` apply to the local replay of whatever is in `data/`.

## Where to find every key

| Variable | What it is | Where to get it |
|---|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to the JSON key | [console.cloud.google.com](https://console.cloud.google.com) → IAM & Admin → Service accounts → your account → Keys → Add key → JSON. Save under `~/keys/`. |
| `GCP_PROJECT` | Project id, not the display name | Project picker → copy Project ID |
| `BQ_DATASET` | Dataset name | `interaction_insights` (`setup_bq.py` creates it) |
| `BQ_LOCATION` | Dataset location | `US` unless you created the dataset elsewhere |
| `GA4_PROPERTY_ID` | Numeric property id | Admin → Property settings. Optional overlay for the dashboard. |

### Service account

1. Create a project without billing if you are using the BigQuery sandbox.
2. Enable the BigQuery API. Enable the Google Analytics Data API only if you set `GA4_PROPERTY_ID`.
3. Create a service account named `interaction-insights-runner`.
4. Roles: BigQuery Job User and BigQuery Data Editor. Add BigQuery Admin only if `setup_bq.py` must create the dataset itself.
5. If you read GA4 reports, grant that service account Viewer on the property.
6. Download the JSON key:

```bash
mkdir -p ~/keys
mv ~/Downloads/your-project-*.json ~/keys/interaction-insights.json
chmod 600 ~/keys/interaction-insights.json
```

The same Google account that can read `gs://goods-ga4-backup_analysis` is the one that should run `scripts/download_ga4.sh`. The service account is for BigQuery and the Data API, not for that copy.

Never commit the JSON key, the parquet shards, or a filled-in `.env`.

## Sandbox rules

- No streaming inserts
- No DML (`INSERT`, `UPDATE`, `DELETE`, `MERGE`)
- Only load jobs, `SELECT`, and `CREATE VIEW`
- 10 GB storage, 1 TB of queries per month, tables expire about 60 days after creation

## Useful commands

```bash
python setup_bq.py
python replay.py --dry-run --max-rows 1000
python replay.py --speed 60 --max-rows 50000
python live_app.py --port 8765 --speed 80 --max-rows 1000
pytest -q
```
