"""Stand-in GA4 events used only when the backup parquet shards are not on disk.

These rows are not the goods-ga4-backup_analysis export. replay.py will not load
them into BigQuery unless --allow-sample is set.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from interaction_insights.model import Ga4Event

PAGES = ("/", "/shop", "/product", "/cart", "/checkout", "/account", "/help")
SOURCES = ("(direct)", "newsletter", "organic", "cpc")
BASE = datetime(2026, 10, 20, 14, 0, tzinfo=timezone.utc)


def generate_sample(n_rows: int, seed: int = 7) -> list[Ga4Event]:
    if n_rows <= 0:
        return []
    rng = random.Random(seed)
    events: list[Ga4Event] = []
    user_i = 0
    forced = ("power", "power", "power", "buyer", "browser", "browser")
    while len(events) < n_rows:
        user_i += 1
        kind = forced[user_i - 1] if user_i <= len(forced) else _kind(rng)
        produced = _user_events(rng, user_i, kind)
        for event in produced:
            if len(events) >= n_rows:
                break
            events.append(event)
    events.sort(key=lambda event: (event.event_timestamp_micros, event.event_id))
    return events


def _kind(rng: random.Random) -> str:
    roll = rng.randrange(10)
    if roll < 5:
        return "browser"
    if roll < 8:
        return "buyer"
    return "power"


def _user_events(rng: random.Random, user_i: int, kind: str) -> list[Ga4Event]:
    uid = f"u{user_i:05d}"
    if kind == "power":
        session_lengths = [6, 5, 4]
    elif kind == "buyer":
        session_lengths = [5]
    else:
        session_lengths = [rng.randint(2, 4)]
    clock = BASE + timedelta(seconds=user_i * 30)
    source = SOURCES[user_i % len(SOURCES)]
    events: list[Ga4Event] = []
    for session_index, length in enumerate(session_lengths):
        if session_index:
            clock += timedelta(seconds=2_400 + rng.randint(0, 600))
        session_id = f"{uid}.{session_index + 1}"
        if session_index == 0:
            events.append(_event(uid, session_id, clock, "session_start", "", 0, source))
        else:
            events.append(_event(uid, session_id, clock, "app_reopen", "", 0, source))
            clock += timedelta(seconds=2)
            events.append(_event(uid, session_id, clock, "session_start", "", 0, source))
        for step in range(length):
            clock += timedelta(seconds=rng.randint(8, 40))
            page = PAGES[(user_i + step) % len(PAGES)]
            engagement = int(12000 * (0.8 ** step) + rng.randint(0, 2000))
            events.append(_event(uid, session_id, clock, "page_view", page, engagement, source))
            clock += timedelta(seconds=1)
            events.append(_event(uid, session_id, clock, "user_engagement", page, engagement, source))
            if rng.random() < _click_p(kind, step):
                clock += timedelta(seconds=1)
                events.append(_event(uid, session_id, clock, "click", page, 0, source))
            if step == 0 and kind == "power":
                clock += timedelta(seconds=1)
                events.append(_event(uid, session_id, clock, "ad_impression", page, 0, source))
                clock += timedelta(seconds=1)
                name = "ad_click" if rng.random() < 0.35 else "ad_ignore"
                events.append(_event(uid, session_id, clock, name, page, 0, source))
                if name == "ad_ignore" and rng.random() < 0.5:
                    clock += timedelta(seconds=1)
                    events.append(_event(uid, session_id, clock, "ad_close", page, 0, source))
        if kind in {"power", "buyer"}:
            clock += timedelta(seconds=3)
            events.append(_event(uid, session_id, clock, "purchase", "/checkout", 0, source))
    return events


def _click_p(kind: str, step: int) -> float:
    base = {"browser": 0.15, "buyer": 0.45, "power": 0.4}[kind]
    return base * (0.75 ** step)


def _event(
    uid: str,
    session_id: str,
    when: datetime,
    name: str,
    page: str,
    engagement: int,
    source: str,
) -> Ga4Event:
    micros = int(when.timestamp() * 1_000_000)
    return Ga4Event(
        event_id=f"{uid}:{session_id}:{micros}:{name}:{page}",
        event_time=when,
        event_timestamp_micros=micros,
        event_name=name,
        user_pseudo_id=uid,
        user_id="",
        session_id=session_id,
        page_path=page,
        page_title=page.strip("/") or "home",
        engagement_time_msec=engagement,
        source=source,
        medium="referral" if source == "newsletter" else "organic" if source == "organic" else "",
        device_category="mobile" if uid.endswith(("1", "3", "5")) else "desktop",
    )
