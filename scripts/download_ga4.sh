#!/usr/bin/env bash
# Copy every GA4 backup parquet shard into data/.
# The bucket holds events-000000000000.parquet through
# events-000000000999.parquet (1000 shards, about 4.59 GB).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/data"
BUCKET="gs://goods-ga4-backup_analysis"

mkdir -p "$DEST"

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud is not installed, so the private bucket cannot be copied from here." >&2
  echo "Install the Google Cloud SDK, run 'gcloud auth login', then rerun:" >&2
  echo "  ./scripts/download_ga4.sh" >&2
  echo "If the shards are already in ~/Downloads/ga4-export, skip this script and run:" >&2
  echo "  python scripts/build_report.py \"\$HOME/Downloads/ga4-export\"" >&2
  exit 1
fi

echo "Copying every events-*.parquet shard from $BUCKET into $DEST"
gcloud storage cp "${BUCKET}/events-*.parquet" "$DEST/"
echo "Saved the GA4 backup shards in $DEST"
echo "These files stay local. data/* is gitignored."
echo "Next: python scripts/build_report.py \"$DEST\""
