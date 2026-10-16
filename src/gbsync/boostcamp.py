"""Boostcamp: login with a JSON token cache, fetch history, normalize to models.

The Boostcamp history payload is undocumented. Field names below are the
candidates we look for; confirm them against a real dump (`gbsync dump`) and
tighten as needed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from boostcampapi import BoostcampAPI, BoostcampAuthException

from .config import Config
from .models import Exercise, Workout, WorkoutSet

log = logging.getLogger(__name__)

LB_TO_KG = 0.45359237


# --- auth -------------------------------------------------------------------

def _load_session(api: BoostcampAPI, cfg: Config) -> bool:
    try:
        data = json.loads(cfg.boostcamp_session_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    api.set_token(data["token"])
    api._refresh_token = data.get("refresh_token")  # library has no public setter
    return True


def _save_session(api: BoostcampAPI, cfg: Config) -> None:
    path = cfg.boostcamp_session_path
    path.write_text(json.dumps({"token": api.token, "refresh_token": api._refresh_token}))
    os.chmod(path, 0o600)


async def _client(cfg: Config) -> BoostcampAPI:
    # session_file points into data/ so the library never writes a pickle elsewhere.
    api = BoostcampAPI(timeout=30, session_file=str(cfg.data_dir / ".unused_bc_session.pickle"))
    if _load_session(api, cfg):
        # Keep creds in memory so the library can re-login if the refresh token dies.
        api._email, api._password = cfg.boostcamp_email, cfg.boostcamp_password
        return api
    if not cfg.boostcamp_email or not cfg.boostcamp_password:
        raise BoostcampAuthException("Boostcamp email/password missing from credentials.toml")
    await api.login(cfg.boostcamp_email, cfg.boostcamp_password, save_session=False)
    _save_session(api, cfg)
    return api


def _utc_offset_minutes(tz: ZoneInfo) -> int:
    # Boostcamp wants e.g. -300 for US Eastern (UTC-5).
    return int(datetime.now(tz).utcoffset().total_seconds() // 60)


async def _fetch(cfg: Config) -> tuple[dict[str, Any], dict[str, Any]]:
    api = await _client(cfg)
    profile = await api.get_user_profile()
    history = await api.get_training_history(timezone_offset=_utc_offset_minutes(cfg.timezone))
    _save_session(api, cfg)  # tokens may have been refreshed
    return profile, history


def fetch_raw(cfg: Config) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (profile, history) raw JSON from Boostcamp."""
    return asyncio.run(_fetch(cfg))


# --- normalization ----------------------------------------------------------

def _first(d: dict, *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_ts(v: Any, tz: ZoneInfo) -> datetime | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        # epoch seconds or milliseconds
        return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, UTC)
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


def weight_unit(profile: dict[str, Any]) -> str:
    pref = ((profile.get("data") or {}).get("preference") or {})
    unit = str(pref.get("weightUnit") or "lb").lower()
    return "kg" if unit.startswith("kg") else "lb"


def _set_completed(s: dict) -> bool:
    for k in ("completed", "is_completed", "isCompleted", "done", "checked"):
        if k in s:
            return bool(s[k])
    return True  # no flag present: assume it was logged because it was done


def _normalize_set(s: dict, unit: str) -> WorkoutSet | None:
    if not _set_completed(s):
        return None
    reps = _num(_first(s, "reps", "rep", "repetitions", "value"))
    weight = _num(_first(s, "weight", "load", "kg", "lbs"))
    if weight is not None and unit == "lb":
        weight *= LB_TO_KG
    duration = _num(_first(s, "duration", "time", "seconds"))
    if reps is None and weight is None and duration is None:
        return None
    return WorkoutSet(
        reps=int(reps) if reps is not None else None,
        weight_kg=round(weight, 3) if weight is not None else None,
        duration_s=duration,
    )


def normalize(
    profile: dict[str, Any], history: dict[str, Any], tz: ZoneInfo, since: date
) -> list[Workout]:
    unit = weight_unit(profile)
    out: list[Workout] = []
    for day_str, workouts in sorted((history.get("data") or {}).items()):
        try:
            day = date.fromisoformat(day_str[:10])
        except ValueError:
            log.debug("Skipping non-date history key %r", day_str)
            continue
        if day < since:
            continue
        for idx, w in enumerate(workouts or []):
            title = _first(w, "title", "name") or "Workout"
            exercises = []
            for rec in w.get("records") or []:
                sets = [ws for s in rec.get("sets") or [] if (ws := _normalize_set(s, unit))]
                if sets:
                    exercises.append(Exercise(name=str(_first(rec, "name", "title")), sets=sets))
            if not exercises:
                continue
            wid = _first(w, "id", "_id", "history_id", "workout_id")
            out.append(
                Workout(
                    key=str(wid) if wid is not None else f"{day.isoformat()}#{idx}:{title}",
                    local_date=day,
                    title=str(title),
                    start=_parse_ts(_first(w, "start_time", "started_at", "startTime", "start"), tz),
                    end=_parse_ts(
                        _first(w, "end_time", "finished_at", "endTime", "completed_at", "end"), tz
                    ),
                    exercises=exercises,
                )
            )
    return out


def lookback_start(tz: ZoneInfo, days: int) -> date:
    return (datetime.now(tz) - timedelta(days=days)).date()
