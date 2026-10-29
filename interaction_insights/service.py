"""Dashboard state: BigQuery + GA4 when configured, local preview otherwise."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from interaction_insights.config import Settings
from interaction_insights.ga4 import empty_ga4, fetch_ga4_reports
from interaction_insights.metrics import compute_snapshot
from interaction_insights.model import Ga4Event, make_run_id
from interaction_insights.sources import default_data_dir, resolve_events

EASTERN = ZoneInfo("America/New_York")

CLOUD_TTL_SECONDS = 15


class DashboardService:
    def __init__(
        self,
        settings: Settings,
        *,
        source: str = "auto",
        speed: float = 80,
        max_rows: int = 1000,
        loop: bool = True,
        data_dir: Path | None = None,
        report_path: Path | None = None,
    ) -> None:
        self.settings = settings
        self.requested_source = source
        self.speed = speed
        self.max_rows = max_rows
        self.data_dir = data_dir
        self.report_path = report_path
        self.loop = loop and speed > 0
        self.mode = "local"
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._events_all: list[Ga4Event] = []
        self._shown = 0
        self._run_id = ""
        self._row_source = "sample"
        self._paused = False
        self._finished = False
        self._hold_until = 0.0
        self._cloud_snapshot: dict | None = None
        self._cloud_at = 0.0
        self._banner = ""
        self._errors = {"bigquery": "", "ga4": ""}
        self._status = "idle"
        self._report: dict | None = None

    def start(self) -> None:
        self._choose_initial_mode()
        if self.mode == "report":
            return
        if self.mode == "local":
            self._begin_local(reset_rows=True)
            self._thread = threading.Thread(target=self._loop, name="replay", daemon=True)
            self._thread.start()
        else:
            self.refresh_cloud(force=True)

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> dict:
        if self.mode == "report":
            return self._report_snapshot()
        if self.mode == "cloud":
            if self._cloud_snapshot is None or (time.monotonic() - self._cloud_at) > CLOUD_TTL_SECONDS:
                self.refresh_cloud(force=False)
            payload = dict(self._cloud_snapshot or {})
            return payload
        return self._local_snapshot()

    def handle(self, action: str) -> None:
        if action == "pause":
            with self.lock:
                self._paused = True
                self._status = "paused"
        elif action == "resume":
            with self.lock:
                self._paused = False
                if self._finished:
                    self._status = "complete"
                else:
                    self._status = "replaying"
        elif action == "restart":
            if self.mode == "report":
                self._read_report_file()
                return
            self._begin_local(reset_rows=False)
        elif action == "preview":
            self.mode = "local"
            self._begin_local(reset_rows=True)
            self._ensure_thread()
        elif action == "cloud":
            self.refresh_cloud(force=True)
            if self._cloud_has_data():
                self.mode = "cloud"
                with self.lock:
                    self._paused = True
        elif action == "refresh":
            if self.mode == "cloud":
                self.refresh_cloud(force=True)
        else:
            raise ValueError(f"Unknown action: {action}")

    def refresh_cloud(self, force: bool = False) -> None:
        if not force and self._cloud_snapshot is not None and (time.monotonic() - self._cloud_at) < CLOUD_TTL_SECONDS:
            return
        errors = {"bigquery": "", "ga4": ""}
        metrics = None
        ga4 = empty_ga4()
        run_events = 0
        if self.settings.bq_configured:
            try:
                from interaction_insights.warehouse import read_warehouse_snapshot

                metrics = read_warehouse_snapshot(
                    self.settings.gcp_project,
                    self.settings.bq_dataset,
                    self.settings.bq_location,
                )
                run_events = int((metrics.get("run_info") or {}).get("events") or 0)
            except Exception as exc:
                errors["bigquery"] = f"{exc.__class__.__name__}: {exc}"
        else:
            missing = ", ".join(self.settings.missing_bigquery()) or "credentials"
            errors["bigquery"] = f"Not configured ({missing})."

        if self.settings.ga4_read_configured:
            try:
                ga4 = fetch_ga4_reports(self.settings.ga4_property_id)
            except Exception as exc:
                errors["ga4"] = f"{exc.__class__.__name__}: {exc}"
                ga4 = empty_ga4(errors["ga4"])
        else:
            missing = ", ".join(self.settings.missing_ga4_read()) or "credentials"
            errors["ga4"] = f"Not configured ({missing})."
            ga4 = empty_ga4(errors["ga4"])

        if metrics is None:
            from interaction_insights.metrics import blank_metrics

            metrics = blank_metrics()
        banner = _cloud_banner(self.settings, errors, run_events, ga4)
        payload = {
            **metrics,
            "source": "bigquery+ga4",
            "mode": "cloud",
            "status": "live" if run_events or _ga4_has_rows(ga4) else "empty",
            "banner": banner,
            "warnings": [item for item in errors.values() if item],
            "errors": errors,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "connections": _connections(self.settings, errors, run_events),
            "progress": {
                "rows_total": run_events,
                "rows_done": run_events,
                "speed": None,
                "events": run_events,
                "run_id": (metrics.get("run_info") or {}).get("replay_run_id"),
            },
            "ga4": ga4,
            "row_source": "bigquery",
        }
        self._errors = errors
        self._cloud_snapshot = payload
        self._cloud_at = time.monotonic()

    def _cloud_has_data(self) -> bool:
        snap = self._cloud_snapshot or {}
        events = int((snap.get("run_info") or {}).get("events") or 0)
        return events > 0 or _ga4_has_rows(snap.get("ga4") or {})

    def _choose_initial_mode(self) -> None:
        if self.requested_source == "cloud":
            self.mode = "cloud"
            return
        if self.requested_source == "auto" and (self.settings.bq_configured or self.settings.ga4_read_configured):
            self.refresh_cloud(force=True)
            if self._cloud_has_data():
                self.mode = "cloud"
                return
        explicit_report = self.report_path is not None
        if (explicit_report or self.requested_source != "local") and self._read_report_file():
            self.mode = "report"
            return
        self.mode = "local"

    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="replay", daemon=True)
        self._thread.start()

    def _begin_local(self, reset_rows: bool) -> None:
        if reset_rows or not self._events_all:
            self._events_all, self._row_source = resolve_events(
                self.max_rows,
                allow_sample=True,
                data_dir=self.data_dir,
            )
        with self.lock:
            self._shown = 0
            self._run_id = make_run_id()
            self._paused = False
            self._finished = False
            self._hold_until = 0.0
            self._status = "replaying"
            self._banner = _sample_banner(self._row_source, self.settings)

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.mode != "local":
                time.sleep(0.2)
                continue
            with self.lock:
                paused = self._paused
                finished = self._finished
                shown = self._shown
                hold_until = self._hold_until
                total = len(self._events_all)
            if paused:
                time.sleep(0.1)
                continue
            if finished:
                if not self.loop or time.monotonic() < hold_until:
                    time.sleep(0.1)
                    continue
                self._begin_local(reset_rows=False)
                continue
            if shown >= total:
                with self.lock:
                    self._finished = True
                    self._status = "complete"
                    self._hold_until = time.monotonic() + 8
                continue
            if self.speed <= 0:
                step = total
                delay = 0.0
            else:
                step = max(1, int(self.speed * 0.25))
                delay = 0.25
            with self.lock:
                self._shown = min(total, self._shown + step)
                self._status = "replaying"
            if delay:
                time.sleep(delay)

    def _local_snapshot(self) -> dict:
        with self.lock:
            shown = self._shown
            events = list(self._events_all[:shown])
            status = self._status
            run_id = self._run_id
            banner = self._banner
            row_source = self._row_source
        metrics = compute_snapshot(events)
        total = len(self._events_all)
        connections = _connections(self.settings, {"bigquery": "", "ga4": ""}, None)
        if row_source == "ga4-parquet":
            connections["ga4"] = {
                "configured": True,
                "ok": True,
                "detail": f"GA4 backup parquet · {total} events",
                "property_id": self.settings.ga4_property_id,
            }
        return {
            **metrics,
            "source": "ga4-parquet" if row_source == "ga4-parquet" else "local-preview",
            "mode": "local",
            "status": status,
            "banner": banner,
            "warnings": [],
            "errors": {
                "bigquery": "" if self.settings.bq_configured else "Not configured.",
                "ga4": "" if row_source == "ga4-parquet" or self.settings.ga4_read_configured else "Backup parquet is not in data/.",
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "connections": connections,
            "progress": {
                "rows_total": total,
                "rows_done": min(shown, total),
                "speed": self.speed,
                "events": len(events),
                "run_id": run_id,
            },
            "ga4": empty_ga4("") if row_source == "ga4-parquet" else empty_ga4(
                "The GA4 backup parquet is not in data/ yet, so this preview is a stand-in."
            ),
            "row_source": row_source,
            "coverage": {"missing_event_names": []},
        }

    def _report_file(self) -> Path:
        if self.report_path is not None:
            return self.report_path
        return default_data_dir() / "report.json"

    def _read_report_file(self) -> dict | None:
        path = self._report_file()
        if not path.is_file():
            self._report = None
            return None
        self._report = json.loads(path.read_text(encoding="utf-8"))
        return self._report

    def _report_snapshot(self) -> dict:
        report = dict(self._report or self._read_report_file() or {})
        report.pop("tail", None)
        coverage = report.get("coverage") or {}
        events = int(coverage.get("events") or (report.get("reaction_rates") or {}).get("events") or 0)
        banner = _report_banner(report)
        connections = _connections(self.settings, {"bigquery": "", "ga4": ""}, None)
        detail = (
            f"GA4 property {coverage.get('property_id')} · {events:,} events"
            if coverage.get("property_id")
            else f"GA4 backup report · {events:,} events"
        )
        connections["ga4"] = {
            "configured": True,
            "ok": True,
            "detail": detail,
            "property_id": coverage.get("property_id") or self.settings.ga4_property_id,
        }
        if not self.settings.bq_configured:
            connections["bigquery"]["detail"] = "Not connected. This page is the parquet report."
        return {
            **report,
            "source": "ga4-parquet-report",
            "mode": "report",
            "status": "complete",
            "banner": banner,
            "warnings": [],
            "errors": {
                "bigquery": "" if self.settings.bq_configured else "Not configured.",
                "ga4": "",
            },
            "generated_at": report.get("generated_at") or datetime.now(timezone.utc).isoformat(),
            "connections": connections,
            "progress": {
                "rows_total": events,
                "rows_done": events,
                "speed": None,
                "events": events,
                "run_id": "report",
            },
            "ga4": empty_ga4(""),
            "row_source": "ga4-parquet-report",
            "coverage": coverage,
        }


def _connections(settings: Settings, errors: dict, run_events: int | None) -> dict:
    bq_ok = settings.bq_configured and not errors.get("bigquery")
    ga_ok = settings.ga4_read_configured and not errors.get("ga4")
    bq_detail = errors.get("bigquery") or (
        f"{settings.gcp_project}.{settings.bq_dataset}"
        + (f" · {run_events} events" if run_events else "")
    )
    if not settings.bq_configured:
        bq_detail = "Set GCP_PROJECT and GOOGLE_APPLICATION_CREDENTIALS."
    ga_detail = errors.get("ga4") or (
        f"Property {settings.ga4_property_id}" if settings.ga4_property_id else ""
    )
    if not settings.ga4_read_configured:
        ga_detail = "Set GA4_PROPERTY_ID and the service account key."
    return {
        "bigquery": {
            "configured": settings.bq_configured,
            "ok": bq_ok,
            "detail": bq_detail,
            "project": settings.gcp_project,
            "dataset": settings.bq_dataset,
        },
        "ga4": {
            "configured": settings.ga4_read_configured,
            "ok": ga_ok,
            "detail": ga_detail,
            "property_id": settings.ga4_property_id,
        },
    }


def _ga4_has_rows(ga4: dict) -> bool:
    for key in ("realtime_events", "events_7d", "pages", "landing_pages", "audiences_proxy"):
        if ga4.get(key):
            return True
    return False


def _sample_banner(row_source: str, settings: Settings) -> str:
    if row_source == "ga4-parquet":
        origin = (
            "Reading the GA4 backup parquet from data/. "
            "This is not a live GA4 Data API call and not a BigQuery query."
        )
    else:
        origin = (
            "These numbers are a stand-in. They are not from GA4 and not from BigQuery. "
            "The real export is the parquet in ~/Downloads/ga4-export "
            "(28 Oct 2025 through 27 Oct 2026, Eastern). "
            "On the Mac, from the ad-user-rates folder, run "
            "python scripts/build_report.py \"$HOME/Downloads/ga4-export\" "
            "and then start this app again."
        )
    if settings.bq_configured:
        tail = " “Read BigQuery and GA4” queries ga4_events after replay.py has loaded it."
    else:
        tail = " BigQuery is not connected."
    return origin + tail


def _report_banner(report: dict) -> str:
    coverage = report.get("coverage") or {}
    window = report.get("window") or {}
    events = int(coverage.get("events") or (report.get("reaction_rates") or {}).get("events") or 0)
    start = _eastern(coverage.get("event_time_min") or window.get("event_time_min"))
    end = _eastern(coverage.get("event_time_max") or window.get("event_time_max"))
    span = f"{start} through {end}" if start and end else "the exported event times"
    missing = coverage.get("missing_event_names") or []
    extra = ""
    if missing:
        extra = " Not in this export: " + ", ".join(missing) + "."
    if coverage.get("warehouse"):
        return (
            f"BigQuery {coverage['warehouse']}: {events:,} events, {span}. "
            + (coverage.get("scope_label") or "GA4 export.")
            + extra
        )
    return (
        f"Full count of the GA4 backup parquet: {events} events, {span}. "
        "Counted from the local parquet files. Not a live GA4 Data API call and not a BigQuery query."
        + extra
    )


def _eastern(value: str | None) -> str:
    if not value:
        return ""
    moment = datetime.fromisoformat(value)
    return moment.astimezone(EASTERN).strftime("%d %b %Y, %I:%M %p ET")


def _cloud_banner(settings: Settings, errors: dict, run_events: int, ga4: dict) -> str:
    parts = []
    if settings.bq_configured and not errors.get("bigquery"):
        if run_events:
            parts.append(
                f"BigQuery {settings.gcp_project}.{settings.bq_dataset} has {run_events} events in the latest replay."
            )
        else:
            parts.append(
                f"Connected to BigQuery {settings.gcp_project}.{settings.bq_dataset}, but the latest run has no events. "
                "Run setup_bq.py and replay.py."
            )
    elif errors.get("bigquery"):
        parts.append(f"BigQuery: {errors['bigquery']}")
    if settings.ga4_read_configured and not errors.get("ga4"):
        parts.append(f"GA4 property {settings.ga4_property_id} is connected.")
        if not _ga4_has_rows(ga4):
            parts.append("GA4 returned no rows for the last 7 days.")
    elif errors.get("ga4"):
        parts.append(f"GA4: {errors['ga4']}")
    return " ".join(parts)
