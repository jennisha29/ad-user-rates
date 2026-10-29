#!/usr/bin/env python3
"""Count every GA4 backup shard into data/report.json.

The dashboard serves that file as the full-year report. It does not call the
GA4 Data API or BigQuery.

Example, from the ad-user-rates folder on the Mac:

    python scripts/build_report.py "$HOME/Downloads/ga4-export"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from interaction_insights.report import build_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the GA4 backup report the dashboard serves.")
    parser.add_argument("directory", type=Path, help="Folder that contains events-*.parquet.")
    parser.add_argument("--output", type=Path, default=Path("data/report.json"))
    parser.add_argument("--partitions", type=int, default=64, help="How many user splits to count. Raise this if the Mac runs out of memory.")
    args = parser.parse_args(argv)
    directory = args.directory.expanduser()
    if not directory.is_dir():
        print(f"No such folder: {directory}", file=sys.stderr)
        return 1
    payload = build_report(directory, args.output, partitions=args.partitions)
    rates = payload["reaction_rates"]
    window = payload.get("window") or {}
    print(f"Wrote {args.output}")
    print(
        f"Events {rates['events']} · page views {rates['page_views']} · "
        f"sessions {rates['sessions']} · clicks {rates['clicks']}"
    )
    print(f"Event time {window.get('event_time_min')} through {window.get('event_time_max')}")
    missing = (payload.get("coverage") or {}).get("missing_event_names") or []
    if missing:
        print("Not in this export: " + ", ".join(missing))
    print("Start the dashboard with: python live_app.py --port 8765")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
