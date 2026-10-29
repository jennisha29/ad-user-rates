#!/usr/bin/env python3
"""Create the BigQuery dataset, ga4_events table, and product views."""

from __future__ import annotations

import argparse
import sys

from interaction_insights.config import load_settings
from interaction_insights.sql_views import VIEW_NAMES, render_view_statements


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create Interaction Insights BigQuery objects.")
    parser.add_argument("--print-sql", action="store_true", help="Print view SQL and exit.")
    parser.add_argument("--project", default="")
    parser.add_argument("--dataset", default="")
    parser.add_argument("--location", default="")
    args = parser.parse_args(argv)
    settings = load_settings()
    project = args.project or settings.gcp_project or "your-project-id"
    dataset = args.dataset or settings.bq_dataset
    location = args.location or settings.bq_location

    if args.print_sql:
        if project == "your-project-id":
            project = "demo-project"
        for statement in render_view_statements(project, dataset):
            print(statement)
            print()
        return 0

    missing = settings.missing_bigquery()
    if args.project:
        missing = [item for item in missing if not item.startswith("GCP_PROJECT")]
    if missing and not args.project:
        print("BigQuery is not configured.", file=sys.stderr)
        for item in missing:
            print(f"  missing {item}", file=sys.stderr)
        print("Copy .env.example to .env and fill in the service account path and project id.", file=sys.stderr)
        return 2
    project = args.project or settings.gcp_project
    from interaction_insights.warehouse import ensure_warehouse

    try:
        ensure_warehouse(project, dataset, location)
    except Exception as exc:
        print(f"Could not create BigQuery objects: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        print(
            "Dataset creation needs bigquery.datasets.create (BigQuery Admin). "
            "If the dataset already exists, BigQuery Job User and BigQuery Data Editor are enough.",
            file=sys.stderr,
        )
        return 1
    print(f"Ready: {project}.{dataset}")
    for name in VIEW_NAMES:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
