"""Pair Boostcamp workouts with Garmin strength activities."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from zoneinfo import ZoneInfo

from .models import GarminActivity, Workout


def match(
    workouts: list[Workout],
    activities: list[GarminActivity],
    tz: ZoneInfo,
    window_minutes: int,
) -> list[tuple[Workout, GarminActivity]]:
    """Return (workout, activity) pairs. Each activity is used at most once.

    Workouts with a known time pair with the activity whose start is closest,
    within the window. The rest pair by local date, in start-time order.
    """
    window = timedelta(minutes=window_minutes)
    used: set[int] = set()
    pairs: list[tuple[Workout, GarminActivity]] = []

    # 1) time-based, closest pairs first
    candidates = []
    for w in workouts:
        if w.anchor is None:
            continue
        for a in activities:
            gap = abs(a.start - w.anchor)
            if gap <= window:
                candidates.append((gap, w.key, w, a))
    matched_keys: set[str] = set()
    for _, key, w, a in sorted(candidates, key=lambda c: (c[0], c[1])):
        if key in matched_keys or a.activity_id in used:
            continue
        pairs.append((w, a))
        matched_keys.add(key)
        used.add(a.activity_id)

    # 2) date-based for workouts without a usable time
    by_day: dict = defaultdict(list)
    for a in activities:
        if a.activity_id not in used:
            by_day[a.start.astimezone(tz).date()].append(a)
    for day_acts in by_day.values():
        day_acts.sort(key=lambda a: a.start)

    pending = defaultdict(list)
    for w in workouts:
        if w.key not in matched_keys and w.anchor is None:
            pending[w.local_date].append(w)
    for day, ws in pending.items():
        for w, a in zip(ws, by_day.get(day, [])):
            pairs.append((w, a))
            used.add(a.activity_id)

    pairs.sort(key=lambda p: p[1].start)
    return pairs
