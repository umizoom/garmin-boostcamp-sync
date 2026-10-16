"""Build the Garmin exerciseSets PUT body from a normalized workout."""

from __future__ import annotations

from datetime import UTC, timedelta
from typing import Any

from .mapping import ExerciseMapper
from .models import GarminActivity, Workout

# Garmin stores strength weights in grams.
GRAMS_PER_KG = 1000.0
# Placeholder timing used to lay sets out in order; Boostcamp doesn't give per-set times.
DEFAULT_SET_SECONDS = 45.0
DEFAULT_REST_SECONDS = 90.0


def _ts(dt) -> str:
    # Garmin uses e.g. "2026-10-14T17:32:05.0" in UTC.
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.0")


def build(workout: Workout, activity: GarminActivity, mapper: ExerciseMapper) -> dict[str, Any]:
    sets: list[dict[str, Any]] = []
    t = activity.start
    total = sum(len(e.sets) for e in workout.exercises)

    # If the activity duration is known, spread sets evenly across it.
    step = None
    if activity.duration_s and total:
        step = activity.duration_s / total

    for ex in workout.exercises:
        category, name = mapper.lookup(ex.name, ex.equipment)
        for s in ex.sets:
            active = s.duration_s or DEFAULT_SET_SECONDS
            sets.append(
                {
                    "setType": "ACTIVE",
                    "startTime": _ts(t),
                    "duration": active,
                    "repetitionCount": s.reps,
                    "weight": round(s.weight_kg * GRAMS_PER_KG, 1) if s.weight_kg is not None else None,
                    "exercises": [{"category": category, "name": name, "probability": 100.0}],
                }
            )
            gap = step if step and step > active else active + DEFAULT_REST_SECONDS
            t = t + timedelta(seconds=gap)

    return {"activityId": activity.activity_id, "exerciseSets": sets}
