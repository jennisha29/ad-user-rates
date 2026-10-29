#!/usr/bin/env bash
# Copy the GA4 backup parquet shards into data/.
# Source: gs://goods-ga4-backup_analysis/events-000000000000.parquet
# through events-000000000049.parquet (50 shards).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/data"
BUCKET="gs://goods-ga4-backup_analysis"

mkdir -p "$DEST"

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud is not installed, so the private bucket cannot be copied from here." >&2
  echo "Install the Google Cloud SDK, run 'gcloud auth login', then rerun:" >&2
  echo "  ./scripts/download_ga4.sh" >&2
  echo "The dashboard still runs on a labeled stand-in until the shards are in data/." >&2
  exit 1
fi

echo "Copying 50 GA4 backup shards into $DEST"
for i in $(seq 0 49); do
  printf -v n "%012d" "$i"
  src="${BUCKET}/events-${n}.parquet"
  echo "  $src"
  gcloud storage cp "$src" "$DEST/"
done
echo "Saved events-000000000000.parquet through events-000000000049.parquet in $DEST"
echo "These files stay local. data/* is gitignored."
