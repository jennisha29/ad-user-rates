"""Environment settings for BigQuery and GA4."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PLACEHOLDERS = {
    "",
    "your-project-id-here",
    "your-project-id",
    "G-XXXXXXXX",
}


def _clean(value: str | None) -> str:
    text = (value or "").strip().strip('"').strip("'")
    if "YOUR_MAC_USERNAME" in text or "YOUR_GITHUB_USERNAME" in text:
        return ""
    if text in PLACEHOLDERS:
        return ""
    return text


@dataclass(frozen=True)
class Settings:
    google_application_credentials: str
    gcp_project: str
    bq_dataset: str
    bq_location: str
    speed: float
    max_rows: int
    ga4_measurement_id: str
    ga4_api_secret: str
    ga4_property_id: str

    @property
    def credentials_path(self) -> Path | None:
        if not self.google_application_credentials:
            return None
        return Path(self.google_application_credentials).expanduser()

    @property
    def bq_configured(self) -> bool:
        path = self.credentials_path
        return bool(self.gcp_project and path and path.is_file())

    @property
    def ga4_read_configured(self) -> bool:
        path = self.credentials_path
        return bool(self.ga4_property_id and path and path.is_file())

    @property
    def ga4_collect_configured(self) -> bool:
        return bool(self.ga4_measurement_id and self.ga4_api_secret)

    def missing_bigquery(self) -> list[str]:
        missing = []
        if not self.gcp_project:
            missing.append("GCP_PROJECT")
        path = self.credentials_path
        if path is None:
            missing.append("GOOGLE_APPLICATION_CREDENTIALS")
        elif not path.is_file():
            missing.append(f"GOOGLE_APPLICATION_CREDENTIALS file ({path})")
        return missing

    def missing_ga4_read(self) -> list[str]:
        missing = []
        if not self.ga4_property_id:
            missing.append("GA4_PROPERTY_ID")
        path = self.credentials_path
        if path is None:
            missing.append("GOOGLE_APPLICATION_CREDENTIALS")
        elif not path.is_file():
            missing.append(f"GOOGLE_APPLICATION_CREDENTIALS file ({path})")
        return missing


def load_settings(env_path: Path | None = None, *, override: bool = False) -> Settings:
    try:
        from dotenv import load_dotenv
    except ImportError:
        load_dotenv = None
    if load_dotenv is not None:
        path = env_path
        if path is None:
            candidate = Path.cwd() / ".env"
            path = candidate if candidate.exists() else None
        if path is not None and Path(path).exists():
            load_dotenv(path, override=override)

    def env(name: str, default: str = "") -> str:
        return _clean(os.environ.get(name, default))

    speed_text = env("SPEED") or "60"
    max_rows_text = env("MAX_ROWS") or "50000"
    try:
        speed = float(speed_text)
    except ValueError:
        speed = 60.0
    try:
        max_rows = int(float(max_rows_text))
    except ValueError:
        max_rows = 50000
    return Settings(
        google_application_credentials=env("GOOGLE_APPLICATION_CREDENTIALS"),
        gcp_project=env("GCP_PROJECT"),
        bq_dataset=env("BQ_DATASET") or "interaction_insights",
        bq_location=env("BQ_LOCATION") or "US",
        speed=speed,
        max_rows=max_rows,
        ga4_measurement_id=env("GA4_MEASUREMENT_ID"),
        ga4_api_secret=env("GA4_API_SECRET"),
        ga4_property_id=env("GA4_PROPERTY_ID"),
    )
