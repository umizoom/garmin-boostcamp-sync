from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from gbsync import boostcamp, matcher, payload
from gbsync.mapping import FALLBACK, ExerciseMapper
from gbsync.models import Exercise, GarminActivity, Workout, WorkoutSet
from gbsync.state import State

TZ = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]


def _workout(key, day, start=None, exercises=None):
    return Workout(
        key=key,
        local_date=day,
        title="Day A",
        start=start,
        end=None,
        exercises=exercises or [Exercise("Bench Press", [WorkoutSet(5, 100.0)])],
    )


def _act(aid, start, duration=None):
    return GarminActivity(activity_id=aid, name="Strength", start=start, duration_s=duration)


# --- matcher ----------------------------------------------------------------

def test_match_by_time_picks_closest_within_window():
    w = _workout("w1", date(2026, 10, 14), start=datetime(2026, 10, 14, 18, 0, tzinfo=TZ))
    near = _act(1, datetime(2026, 10, 14, 22, 5, tzinfo=UTC))  # 18:05 local
    far = _act(2, datetime(2026, 10, 14, 13, 0, tzinfo=UTC))  # 09:00 local
    pairs = matcher.match([w], [far, near], TZ, 120)
    assert [(p[0].key, p[1].activity_id) for p in pairs] == [("w1", 1)]


def test_match_by_time_outside_window_is_unmatched():
    w = _workout("w1", date(2026, 10, 14), start=datetime(2026, 10, 14, 18, 0, tzinfo=TZ))
    a = _act(1, datetime(2026, 10, 14, 13, 0, tzinfo=UTC))
    assert matcher.match([w], [a], TZ, 120) == []


def test_match_by_local_date_when_no_start_time():
    # 01:30 UTC on the 15th is 21:30 on the 14th in New York.
    w = _workout("w1", date(2026, 10, 14))
    a = _act(1, datetime(2026, 10, 15, 1, 30, tzinfo=UTC))
    pairs = matcher.match([w], [a], TZ, 120)
    assert [(p[0].key, p[1].activity_id) for p in pairs] == [("w1", 1)]


def test_each_activity_used_once():
    w1 = _workout("w1", date(2026, 10, 14))
    w2 = _workout("w2", date(2026, 10, 14))
    a = _act(1, datetime(2026, 10, 14, 22, 0, tzinfo=UTC))
    assert len(matcher.match([w1, w2], [a], TZ, 120)) == 1


# --- mapping ----------------------------------------------------------------

def test_override_wins():
    m = ExerciseMapper(ROOT / "exercise_map.toml")
    assert m.lookup("bench press") == ("BENCH_PRESS", "BARBELL_BENCH_PRESS")


def test_catalog_exact_and_normalized():
    m = ExerciseMapper()
    assert m.lookup("Barbell Row") == ("ROW", "BARBELL_ROW")
    assert m.lookup("barbell rows") == ("ROW", "BARBELL_ROW")


def test_parenthesised_equipment_variant():
    m = ExerciseMapper()
    assert m.lookup("Deadlift (Barbell)") == ("DEADLIFT", "BARBELL_DEADLIFT")


def test_keyword_fallback_and_unmapped_tracking():
    m = ExerciseMapper()
    assert m.lookup("Seated Leg Curl Machine Thing") == ("LEG_CURL", None)
    assert m.lookup("Zzz Mystery Move") == FALLBACK
    assert m.unmapped == {"Seated Leg Curl Machine Thing", "Zzz Mystery Move"}


# --- payload ----------------------------------------------------------------

def test_payload_shape_and_units():
    w = _workout(
        "w1",
        date(2026, 10, 14),
        exercises=[
            Exercise("Barbell Row", [WorkoutSet(8, 61.235), WorkoutSet(8, None)]),
        ],
    )
    a = _act(42, datetime(2026, 10, 14, 22, 0, tzinfo=UTC))
    body = payload.build(w, a, ExerciseMapper())
    assert body["activityId"] == 42
    s0, s1 = body["exerciseSets"]
    assert s0["setType"] == "ACTIVE"
    assert s0["repetitionCount"] == 8
    assert s0["weight"] == 61235.0  # grams
    assert s0["exercises"][0]["category"] == "ROW"
    assert s0["startTime"] == "2026-10-14T22:00:00.0"
    assert s1["weight"] is None
    assert s1["startTime"] > s0["startTime"]


# --- boostcamp normalization ------------------------------------------------

def test_normalize_converts_lb_and_skips_incomplete():
    profile = {"data": {"preference": {"weightUnit": "lb"}}}
    history = {
        "data": {
            "2026-10-14": [
                {
                    "title": "Day A",
                    "records": [
                        {
                            "name": "Bench Press",
                            "sets": [
                                {"reps": 5, "weight": 225, "completed": True},
                                {"reps": 5, "weight": 225, "completed": False},
                            ],
                        }
                    ],
                }
            ],
            "2026-09-01": [{"title": "Old", "records": [{"name": "X", "sets": [{"reps": 1}]}]}],
        }
    }
    out = boostcamp.normalize(profile, history, TZ, since=date(2026, 10, 1))
    assert len(out) == 1
    (ex,) = out[0].exercises
    assert len(ex.sets) == 1
    assert ex.sets[0].reps == 5
    assert abs(ex.sets[0].weight_kg - 102.058) < 0.01


# --- state ------------------------------------------------------------------

def test_state_roundtrip(tmp_path):
    s = State(tmp_path / "state.json")
    s.record("w1", 42, "abc")
    s.save()
    s2 = State(tmp_path / "state.json")
    assert s2.is_current("w1", 42, "abc")
    assert not s2.is_current("w1", 42, "changed")
    assert not s2.is_current("w1", 43, "abc")
