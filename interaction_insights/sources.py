"""Resolve GA4 events from local parquet shards or the stand-in sample."""

from __future__ import annotations

from pathlib import Path

from interaction_insights.model import Ga4Event
from interaction_insights.parquet_source import discover_parquet, read_parquet
from interaction_insights.sample_data import generate_sample


def default_data_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data"


def resolve_events(
    max_rows: int,
    *,
    allow_sample: bool,
    data_dir: Path | None = None,
) -> tuple[list[Ga4Event], str]:
    directory = data_dir or default_data_dir()
    files = discover_parquet(directory)
    if files:
        return read_parquet(files, max_rows=max_rows), "ga4-parquet"
    if not allow_sample:
        raise FileNotFoundError(
            f"No events-*.parquet files in {directory}. "
            "Run scripts/download_ga4.sh to copy gs://goods-ga4-backup_analysis, "
            "or pass --allow-sample to load a stand-in. "
            "The stand-in is not written to BigQuery unless you ask for it."
        )
    return generate_sample(max_rows), "sample"
