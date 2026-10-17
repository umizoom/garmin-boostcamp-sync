"""Load settings from credentials.toml, with environment variable overrides."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Config:
    garmin_email: str | None
    garmin_password: str | None
    boostcamp_email: str | None
    boostcamp_password: str | None
    timezone: ZoneInfo
    data_dir: Path
    interval_hours: float
    lookback_days: int
    match_window_minutes: int
    mfa_wait_minutes: int
    exercise_map_path: Path
    rename_activities: bool
    discord_webhook: str | None

    @property
    def garmin_token_dir(self) -> Path:
        return self.data_dir / "garmin_tokens"

    @property
    def boostcamp_session_path(self) -> Path:
        return self.data_dir / "boostcamp_session.json"

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def mfa_code_path(self) -> Path:
        return self.data_dir / "mfa_code"

    @property
    def mfa_pending_path(self) -> Path:
        return self.data_dir / "mfa_pending"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def dumps_dir(self) -> Path:
        return self.data_dir / "dumps"


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path or os.getenv("GBSYNC_CONFIG", "credentials.toml"))
    raw: dict = {}
    if path.exists():
        with path.open("rb") as fh:
            raw = tomllib.load(fh)

    garmin = raw.get("garmin", {})
    boostcamp = raw.get("boostcamp", {})
    sync = raw.get("sync", {})
    notify = raw.get("notify", {})

    def pick(env: str, section: dict, key: str, default=None):
        return os.getenv(env) or section.get(key, default)

    data_dir = Path(pick("GBSYNC_DATA_DIR", sync, "data_dir", "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    data_dir.chmod(0o700)

    return Config(
        garmin_email=pick("GARMIN_EMAIL", garmin, "email"),
        garmin_password=pick("GARMIN_PASSWORD", garmin, "password"),
        boostcamp_email=pick("BOOSTCAMP_EMAIL", boostcamp, "email"),
        boostcamp_password=pick("BOOSTCAMP_PASSWORD", boostcamp, "password"),
        timezone=ZoneInfo(pick("GBSYNC_TIMEZONE", sync, "timezone", "UTC")),
        data_dir=data_dir,
        interval_hours=float(pick("GBSYNC_INTERVAL_HOURS", sync, "interval_hours", 24)),
        lookback_days=int(pick("GBSYNC_LOOKBACK_DAYS", sync, "lookback_days", 7)),
        match_window_minutes=int(sync.get("match_window_minutes", 120)),
        mfa_wait_minutes=int(sync.get("mfa_wait_minutes", 10)),
        exercise_map_path=Path(
            os.getenv("GBSYNC_EXERCISE_MAP", sync.get("exercise_map", "exercise_map.toml"))
        ),
        rename_activities=_bool(pick("GBSYNC_RENAME_ACTIVITIES", sync, "rename_activities", True)),
        discord_webhook=pick("DISCORD_WEBHOOK_URL", notify, "discord_webhook") or None,
    )


def _bool(value) -> bool:
    # TOML gives a real bool; env vars give strings.
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")
