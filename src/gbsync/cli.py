"""gbsync command line: login | sync | daemon | dump."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
from datetime import datetime, timedelta

from garminconnect import GarminConnectTooManyRequestsError

from . import boostcamp, garmin, matcher, payload
from .config import Config, load_config
from .mapping import ExerciseMapper
from .state import State

log = logging.getLogger("gbsync")


def cmd_login(cfg: Config, args) -> int:
    garmin.login_interactive(cfg)
    boostcamp.fetch_raw(cfg)  # also caches Boostcamp tokens
    log.info("Boostcamp login OK")
    return 0


def run_sync(cfg: Config, *, days: int, dry_run: bool, force: bool, interactive: bool) -> int:
    since = boostcamp.lookback_start(cfg.timezone, days)
    profile, history = boostcamp.fetch_raw(cfg)
    workouts = boostcamp.normalize(profile, history, cfg.timezone, since)
    log.info("Boostcamp: %d workout(s) since %s", len(workouts), since)
    if not workouts:
        return 0

    api = garmin.connect(cfg, interactive=interactive)
    # Pad a day each side so UTC/local date edges don't drop activities.
    activities = garmin.list_strength_activities(
        api, since - timedelta(days=1), datetime.now(cfg.timezone).date() + timedelta(days=1)
    )
    log.info("Garmin: %d strength activit(y/ies) in window", len(activities))

    pairs = matcher.match(workouts, activities, cfg.timezone, cfg.match_window_minutes)
    matched = {w.key for w, _ in pairs}
    for w in workouts:
        if w.key not in matched:
            log.warning("No Garmin strength activity found for %s %r", w.local_date, w.title)

    mapper = ExerciseMapper(cfg.exercise_map_path)
    state = State(cfg.state_path)
    written = 0
    for w, a in pairs:
        h = w.content_hash()
        label = f"{w.local_date} {w.title!r} -> Garmin {a.activity_id} ({a.name!r})"
        if not force and state.is_current(w.key, a.activity_id, h):
            log.info("Up to date: %s", label)
            continue
        body = payload.build(w, a, mapper)
        if dry_run:
            log.info("DRY RUN would write %d set(s): %s", len(body["exerciseSets"]), label)
            print(json.dumps(body, indent=2))
            continue
        garmin.backup_exercise_sets(api, a.activity_id, cfg.backups_dir)
        if new_bad := garmin.put_exercise_sets(api, a.activity_id, body, state.rejected_names):
            state.add_rejected(new_bad)
            log.warning(
                "Remember to remap these in exercise_map.toml: %s", ", ".join(sorted(new_bad))
            )
        state.record(w.key, a.activity_id, h)
        state.save()
        written += 1
        log.info("Wrote %d set(s): %s", len(body["exerciseSets"]), label)

    if mapper.unmapped:
        log.warning("Unmapped exercises (add to exercise_map.toml): %s", sorted(mapper.unmapped))
    log.info("Sync done: %d activit(y/ies) updated", written)
    return 0


def cmd_sync(cfg: Config, args) -> int:
    return run_sync(
        cfg,
        days=args.since or cfg.lookback_days,
        dry_run=args.dry_run,
        force=args.force,
        interactive=sys.stdin.isatty(),
    )


def cmd_daemon(cfg: Config, args) -> int:
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    log.info("Daemon started; syncing every %.1f h", cfg.interval_hours)
    while not stop.is_set():
        try:
            run_sync(cfg, days=cfg.lookback_days, dry_run=False, force=False, interactive=False)
        except garmin.MfaTimeout as e:
            log.error("%s; will retry next cycle", e)
        except GarminConnectTooManyRequestsError:
            log.error("Garmin rate limited (429); will retry next cycle")
        except Exception:
            log.exception("Sync cycle failed; will retry next cycle")
        stop.wait(cfg.interval_hours * 3600)
    log.info("Daemon stopped")
    return 0


def cmd_dump(cfg: Config, args) -> int:
    """Save raw API responses to data/dumps/ for inspecting real field names."""
    out = cfg.dumps_dir
    out.mkdir(parents=True, exist_ok=True)
    profile, history = boostcamp.fetch_raw(cfg)
    (out / "boostcamp_profile.json").write_text(json.dumps(profile, indent=2))
    (out / "boostcamp_history.json").write_text(json.dumps(history, indent=2))

    api = garmin.connect(cfg, interactive=sys.stdin.isatty())
    end = datetime.now(cfg.timezone).date()
    raw = api.get_activities_by_date((end - timedelta(days=args.since)).isoformat(), end.isoformat())
    (out / "garmin_activities.json").write_text(json.dumps(raw, indent=2))
    strength = [a for a in raw if (a.get("activityType") or {}).get("typeKey") == garmin.STRENGTH_TYPE_KEY]
    for a in strength[:3]:
        sets = garmin.get_exercise_sets(api, a["activityId"])
        (out / f"garmin_sets_{a['activityId']}.json").write_text(json.dumps(sets, indent=2))
    log.info("Wrote dumps to %s (%d strength activities)", out, len(strength))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gbsync", description=__doc__)
    p.add_argument("--config", help="path to credentials.toml (default: ./credentials.toml)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login", help="interactive login (prompts for Garmin MFA), saves tokens")

    s = sub.add_parser("sync", help="run one sync")
    s.add_argument("--dry-run", action="store_true", help="print payloads, don't write to Garmin")
    s.add_argument("--since", type=int, help="days to look back (default: lookback_days)")
    s.add_argument("--force", action="store_true", help="rewrite even if already synced")

    sub.add_parser("daemon", help="sync now, then every interval_hours")

    d = sub.add_parser("dump", help="save raw Boostcamp/Garmin JSON to data/dumps/")
    d.add_argument("--since", type=int, default=14)

    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = load_config(args.config)
    handler = {"login": cmd_login, "sync": cmd_sync, "daemon": cmd_daemon, "dump": cmd_dump}[args.cmd]
    return handler(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
