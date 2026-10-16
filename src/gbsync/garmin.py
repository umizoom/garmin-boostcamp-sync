"""Garmin Connect: authentication (interactive and file-based MFA) and activity I/O."""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from garminconnect import Garmin, GarminConnectAuthenticationError

from .config import Config
from .models import GarminActivity

log = logging.getLogger(__name__)

STRENGTH_TYPE_KEY = "strength_training"


class MfaTimeout(Exception):
    """No MFA code arrived in data/mfa_code before the wait expired."""


def _token_login(cfg: Config) -> Garmin | None:
    """Log in with saved tokens only. Returns None if there are none or they're rejected."""
    token_file = cfg.garmin_token_dir / "garmin_tokens.json"
    if not token_file.exists():
        return None
    api = Garmin()
    try:
        api.login(str(cfg.garmin_token_dir))
    except GarminConnectAuthenticationError as e:
        log.warning("Saved Garmin tokens were rejected: %s", e)
        return None
    return api


def _require_creds(cfg: Config) -> tuple[str, str]:
    if not cfg.garmin_email or not cfg.garmin_password:
        raise GarminConnectAuthenticationError(
            "Garmin email/password missing from credentials.toml (or GARMIN_EMAIL/GARMIN_PASSWORD)"
        )
    return cfg.garmin_email, cfg.garmin_password


def _prepare_token_dir(cfg: Config) -> Path:
    cfg.garmin_token_dir.mkdir(parents=True, exist_ok=True)
    cfg.garmin_token_dir.chmod(0o700)
    return cfg.garmin_token_dir


def login_interactive(cfg: Config) -> Garmin:
    """Full credential login that prompts on stdin for the MFA code. Saves tokens."""
    email, password = _require_creds(cfg)
    token_dir = _prepare_token_dir(cfg)
    api = Garmin(
        email=email,
        password=password,
        prompt_mfa=lambda: input("Garmin MFA code: ").strip(),
    )
    # With no tokens present, login() does a credential login, prompts for MFA
    # through prompt_mfa, and writes the tokens into token_dir.
    api.login(str(token_dir))
    log.info("Garmin login OK; tokens saved to %s", token_dir)
    return api


def _wait_for_mfa_code(cfg: Config, deadline: float) -> str:
    while time.monotonic() < deadline:
        if cfg.mfa_code_path.exists():
            code = cfg.mfa_code_path.read_text().strip()
            cfg.mfa_code_path.unlink(missing_ok=True)
            if code:
                return code
        time.sleep(10)
    raise MfaTimeout(f"No MFA code written to {cfg.mfa_code_path} in time")


def login_with_mfa_file(cfg: Config) -> Garmin:
    """Credential login for unattended runs. If Garmin asks for MFA, wait for
    the code to appear in data/mfa_code (written by a person on the host)."""
    email, password = _require_creds(cfg)
    token_dir = _prepare_token_dir(cfg)
    api = Garmin(email=email, password=password, return_on_mfa=True)
    status, state = api.login()

    if status == "needs_mfa":
        cfg.mfa_code_path.unlink(missing_ok=True)  # ignore stale codes
        cfg.mfa_pending_path.write_text(datetime.now(UTC).isoformat() + "\n")
        log.warning(
            "Garmin MFA required: write the code to %s within %d minutes "
            "(e.g. `echo 123456 > %s`)",
            cfg.mfa_code_path,
            cfg.mfa_wait_minutes,
            cfg.mfa_code_path,
        )
        deadline = time.monotonic() + cfg.mfa_wait_minutes * 60
        try:
            while True:
                code = _wait_for_mfa_code(cfg, deadline)
                try:
                    api.resume_login(state, code)
                    break
                except GarminConnectAuthenticationError as e:
                    # A wrong code keeps the pending MFA session; wait for another.
                    log.warning("MFA code rejected (%s); write a new one to %s", e, cfg.mfa_code_path)
        finally:
            cfg.mfa_pending_path.unlink(missing_ok=True)

    api.client.dump(str(token_dir))
    log.info("Garmin login OK; tokens saved to %s", token_dir)
    # Re-open from the saved tokens so profile/settings are loaded the normal way.
    return _token_login(cfg) or api


def connect(cfg: Config, interactive: bool = False) -> Garmin:
    """Return a logged-in client: saved tokens first, then a credential login."""
    api = _token_login(cfg)
    if api is not None:
        return api
    log.info("No usable Garmin tokens; doing a credential login")
    return login_interactive(cfg) if interactive else login_with_mfa_file(cfg)


def _parse_gmt(value: str) -> datetime:
    # Garmin returns e.g. "2026-10-14 17:32:05" in startTimeGMT.
    return datetime.fromisoformat(value.replace(" ", "T")).replace(tzinfo=UTC)


def list_strength_activities(api: Garmin, start: date, end: date) -> list[GarminActivity]:
    # Filter locally on typeKey rather than trusting the server-side parent-type filter.
    raw = api.get_activities_by_date(start.isoformat(), end.isoformat(), sortorder="asc")
    out = []
    for a in raw:
        if (a.get("activityType") or {}).get("typeKey") != STRENGTH_TYPE_KEY:
            continue
        out.append(
            GarminActivity(
                activity_id=int(a["activityId"]),
                name=a.get("activityName") or "",
                start=_parse_gmt(a["startTimeGMT"]),
                duration_s=a.get("duration"),
            )
        )
    return out


def get_exercise_sets(api: Garmin, activity_id: int) -> dict[str, Any]:
    return api.get_activity_exercise_sets(activity_id)


def put_exercise_sets(api: Garmin, activity_id: int, payload: dict[str, Any]) -> Any:
    return api.set_activity_exercise_sets(activity_id, payload)
