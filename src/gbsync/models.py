"""Source-neutral workout model that the matcher and payload builder work on."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime


@dataclass
class WorkoutSet:
    reps: int | None
    weight_kg: float | None
    duration_s: float | None = None


@dataclass
class Exercise:
    name: str
    sets: list[WorkoutSet] = field(default_factory=list)
    equipment: str | None = None  # e.g. "Barbell", "Dumbbell"


@dataclass
class Workout:
    key: str  # stable id used in state.json
    local_date: date
    title: str
    start: datetime | None  # tz-aware when known
    end: datetime | None

    @property
    def anchor(self) -> datetime | None:
        """Time used to match against Garmin. Boostcamp's finished_at lands a few
        minutes after the watch activity starts, so either end is close enough."""
        return self.start or self.end
    exercises: list[Exercise] = field(default_factory=list)

    def content_hash(self) -> str:
        body = json.dumps([asdict(e) for e in self.exercises], sort_keys=True)
        return hashlib.sha256(body.encode()).hexdigest()[:16]


@dataclass
class GarminActivity:
    activity_id: int
    name: str
    start: datetime  # tz-aware
    duration_s: float | None
