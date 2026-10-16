"""Boostcamp: login with a JSON token cache, fetch history, normalize to models."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
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
    """Account default, used when a set doesn't carry its own weight_unit."""
    pref = ((profile.get("data") or {}).get("preference") or {})
    return _unit(pref.get("weightUnit"))


def _unit(raw: Any, default: str = "lb") -> str:
    if not raw:
        return default
    return "kg" if str(raw).lower().startswith("kg") else "lb"


def _logged(s: dict, archived: str, entered: str, empty_flag: str) -> float | None:
    """What was actually done. `archived_*` holds the logged result; `value`/
    `amount` are the prescription or in-app entry ("3+" on AMRAP sets, "" when
    left blank), so they're only a fallback."""
    if (v := _num(s.get(archived))) is not None:
        return v
    return None if s.get(empty_flag) else _num(s.get(entered))


def _normalize_set(s: dict, default_unit: str) -> WorkoutSet | None:
    """One Boostcamp set: weight in the set's `weight_unit`; reps, or seconds
    when `target_type` is "time"."""
    if s.get("skipped"):
        return None
    amount = _logged(s, "archived_reps", "amount", "amountEmpty")
    weight = _logged(s, "archived_weight", "value", "valueEmpty")
    if amount is None and weight is None:
        return None
    if weight is not None and _unit(s.get("weight_unit"), default_unit) == "lb":
        weight *= LB_TO_KG
    timed = s.get("target_type") == "time"
    return WorkoutSet(
        reps=None if timed or amount is None else int(amount),
        weight_kg=round(weight, 3) if weight else None,
        duration_s=amount if timed else None,
    )


def short_program_name(name: str) -> str:
    """Use a trailing bracketed abbreviation if there is one:
    "GZCL Program (GZCLP)" -> "GZCLP". Otherwise the name as given."""
    m = re.search(r"\(([^()]+)\)\s*$", name)
    return m.group(1).strip() if m else name.strip()


def normalize(
    profile: dict[str, Any], history: dict[str, Any], tz: ZoneInfo, since: date
) -> list[Workout]:
    """history["data"] is {"YYYY-MM-DD": [workout, ...]}. Each workout has
    `id`, `title`, `name` (the program), `finished_at` (local time, no zone)
    and `records`, one per exercise with `name`, `type` (equipment, e.g.
    "Barbell") and `sets`."""
    default_unit = weight_unit(profile)
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
            title = w.get("title")
            program = w.get("name")
            if not title:
                title, program = program or "Workout", None
            exercises = []
            for rec in w.get("records") or []:
                sets = [ws for s in rec.get("sets") or [] if (ws := _normalize_set(s, default_unit))]
                if sets:
                    exercises.append(
                        Exercise(name=str(rec.get("name")), sets=sets, equipment=rec.get("type") or None)
                    )
            if not exercises:
                continue
            out.append(
                Workout(
                    key=str(w.get("id") or f"{day.isoformat()}#{idx}:{title}"),
                    local_date=day,
                    title=str(title),
                    start=None,
                    end=_parse_ts(w.get("finished_at"), tz),
                    exercises=exercises,
                    program=short_program_name(str(program)) if program else None,
                )
            )
    return out


def lookback_start(tz: ZoneInfo, days: int) -> date:
    return (datetime.now(tz) - timedelta(days=days)).date()
