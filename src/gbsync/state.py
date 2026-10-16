"""Remember which workouts were already written, so unchanged ones aren't re-sent."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


class State:
    def __init__(self, path: Path):
        self.path = path
        try:
            self.data: dict = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}
        self.data.setdefault("synced", {})

    def is_current(self, workout_key: str, activity_id: int, content_hash: str) -> bool:
        rec = self.data["synced"].get(workout_key)
        return bool(rec and rec["activity_id"] == activity_id and rec["hash"] == content_hash)

    def record(self, workout_key: str, activity_id: int, content_hash: str) -> None:
        self.data["synced"][workout_key] = {
            "activity_id": activity_id,
            "hash": content_hash,
            "synced_at": datetime.now(UTC).isoformat(),
        }

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
        tmp.replace(self.path)
