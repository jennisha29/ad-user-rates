#!/usr/bin/env python3
"""Load GA4 backup parquet events into BigQuery with load jobs."""

from __future__ import annotations

import argparse
import sys

from interaction_insights.config import load_settings
from interaction_insights.replay_job import run_replay


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load GA4 backup events into BigQuery.")
    parser.add_argument("--speed", type=float, default=None, help="Events per second. 0 loads as fast as possible.")
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--flush-seconds", type=float, default=60, help="Micro-batch interval for BigQuery load jobs.")
    parser.add_argument("--dry-run", action="store_true", help="Read events and print counts. Do not call BigQuery.")
    parser.add_argument(
        "--allow-sample",
        action="store_true",
        help="If the parquet shards are missing, load the stand-in. Off by default.",
    )
    parser.add_argument("--no-ga4", action="store_true", help="Accepted for compatibility. This loader does not send events.")
    args = parser.parse_args(argv)
    settings = load_settings()
    speed = settings.speed if args.speed is None else args.speed
    max_rows = settings.max_rows if args.max_rows is None else args.max_rows

    if not args.dry_run and not settings.bq_configured:
        print("BigQuery credentials are not configured.", file=sys.stderr)
        for item in settings.missing_bigquery():
            print(f"  missing {item}", file=sys.stderr)
        print("See MAC_SETUP.md. Pass --dry-run to read parquet without cloud keys.", file=sys.stderr)
        return 2

    try:
        result = run_replay(
            max_rows=max_rows,
            speed=speed,
            flush_seconds=args.flush_seconds,
            dry_run=args.dry_run,
            allow_sample=args.allow_sample,
            project=settings.gcp_project,
            dataset=settings.bq_dataset,
            location=settings.bq_location,
        )
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Load failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    print(
        f"run {result.run_id} source={result.source} rows={result.rows} "
        f"events={result.events} page_views={result.page_views} "
        f"clicks={result.clicks} loaded={result.loaded} dry_run={result.dry_run}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
