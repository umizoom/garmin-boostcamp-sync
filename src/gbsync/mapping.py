"""Map Boostcamp exercise names to Garmin (category, exercise) keys."""

from __future__ import annotations

import logging
import re
import tomllib
from pathlib import Path

from garminconnect import exercises as catalog

log = logging.getLogger(__name__)

# Garmin rejects unknown categories, so the last-resort fallback must be a real one.
FALLBACK = ("TOTAL_BODY", None)

# Word-prefix keyword -> category, checked in order (more specific first),
# for names we can't resolve exactly.
CATEGORY_HINTS: list[tuple[str, str]] = [
    ("leg curl", "LEG_CURL"),
    ("hamstring curl", "LEG_CURL"),
    ("leg raise", "LEG_RAISE"),
    ("split squat", "LUNGE"),
    ("bench", "BENCH_PRESS"),
    ("squat", "SQUAT"),
    ("deadlift", "DEADLIFT"),
    ("rdl", "DEADLIFT"),
    ("lunge", "LUNGE"),
    ("pull up", "PULL_UP"),
    ("pullup", "PULL_UP"),
    ("chin up", "PULL_UP"),
    ("pulldown", "PULL_UP"),
    ("push up", "PUSH_UP"),
    ("pushup", "PUSH_UP"),
    ("dip", "TRICEPS_EXTENSION"),
    ("row", "ROW"),
    ("overhead press", "SHOULDER_PRESS"),
    ("shoulder press", "SHOULDER_PRESS"),
    ("military press", "SHOULDER_PRESS"),
    ("lateral raise", "LATERAL_RAISE"),
    ("curl", "CURL"),
    ("tricep", "TRICEPS_EXTENSION"),
    ("skull", "TRICEPS_EXTENSION"),
    ("fly", "FLYE"),
    ("flye", "FLYE"),
    ("calf", "CALF_RAISE"),
    ("shrug", "SHRUG"),
    ("hip thrust", "HIP_RAISE"),
    ("glute bridge", "HIP_RAISE"),
    ("plank", "PLANK"),
    ("crunch", "CRUNCH"),
    ("sit up", "SIT_UP"),
    ("carry", "CARRY"),
    ("clean", "OLYMPIC_LIFT"),
    ("snatch", "OLYMPIC_LIFT"),
]


def _norm(name: str) -> str:
    s = name.lower()
    s = re.sub(r"\(.*?\)", " ", s)  # drop "(Barbell)" style suffixes
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\bbodyweight\b", "body weight", s)  # Garmin: "Body-weight Dip"
    s = re.sub(r"\b(\w+?)s\b", r"\1", s)  # crude singular
    return " ".join(s.split())


_CATALOG_BY_NORM: dict[str, tuple[str, str]] = {}
for _e in catalog.EXERCISES:
    _CATALOG_BY_NORM.setdefault(_norm(_e["name"]), (_e["category"], _e["exercise"]))


_TIER_TAG = re.compile(r"\s*\(T\d\)\s*", re.IGNORECASE)  # GZCLP "Squat (T1)"
_PAREN = re.compile(r"^(.*?)\s*\((.+)\)\s*$")


def _strip_tier(name: str) -> str:
    return _TIER_TAG.sub(" ", name).strip()


def _bc_variants(name: str, equipment: str | None = None) -> list[str]:
    """Catalog names to try for a Boostcamp exercise, most specific first.

    "Pull-Up (Weighted)" -> "Weighted Pull-Up"; equipment "Barbell" turns
    "Bench Press" into "Barbell Bench Press" (or "... with Barbell"). The plain
    name comes last so it doesn't win over the specific entry.
    """
    base = _strip_tier(name)
    paren = None
    if m := _PAREN.match(base):
        paren = f"{m.group(2)} {m.group(1)}"
    equip = equipment.strip() if equipment else None
    out = []
    for cand in (paren, base):
        if not cand:
            continue
        if equip and equip.lower() not in cand.lower():
            out.append(f"{equip} {cand}")
            out.append(f"{cand} with {equip}")  # Garmin: "Bent-over Row with Dumbbell"
        out.append(cand)
    return out


class ExerciseMapper:
    def __init__(self, overrides_path: Path | None = None):
        self.overrides: dict[str, tuple[str, str | None]] = {}
        if overrides_path and overrides_path.exists():
            with overrides_path.open("rb") as fh:
                for k, v in (tomllib.load(fh).get("exercises") or {}).items():
                    self.overrides[k.strip().lower()] = (v[0], v[1] or None)
        self.unmapped: set[str] = set()

    def lookup(self, name: str, equipment: str | None = None) -> tuple[str, str | None]:
        for key in (name, _strip_tier(name)):
            if hit := self.overrides.get(key.strip().lower()):
                return hit
        for variant in _bc_variants(name, equipment):
            if entry := catalog.resolve(variant):
                return entry["category"], entry["exercise"] or None
            if hit := _CATALOG_BY_NORM.get(_norm(variant)):
                return hit[0], hit[1] or None
        self.unmapped.add(name)
        words = " ".join(re.sub(r"[^a-z0-9]+", " ", name.lower()).split())
        for kw, category in CATEGORY_HINTS:
            if re.search(rf"\b{re.escape(kw)}", words):
                log.info("unmapped exercise %r -> category %s (add to exercise_map.toml)", name, category)
                return category, None
        log.warning("unmapped exercise %r -> %s (add to exercise_map.toml)", name, FALLBACK[0])
        return FALLBACK
