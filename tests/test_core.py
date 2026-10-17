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

def _bc_set(value, amount, unit="lbs", **extra):
    # Trimmed from a real Boostcamp /programs/history set.
    return {
        "value": value,
        "amount": amount,
        "weight_unit": unit,
        "skipped": False,
        "valueEmpty": False,
        "amountEmpty": False,
        "target_type": "reps",
        **extra,
    }


def test_normalize_real_boostcamp_shape():
    profile = {"data": {"preference": {"weightUnit": "lbs"}}}
    history = {
        "data": {
            "2026-10-14": [
                {
                    "id": "DvChH9zKFEnSEDEQ",
                    "title": "Week 1 · Day 1",
                    "name": "GZCL Program (GZCLP)",
                    "finished_at": "2026-10-14 19:11:28",
                    "records": [
                        {
                            "name": "Squat (T1)",
                            "type": "Barbell",
                            "sets": [
                                _bc_set("155", "3", archived_weight=155, archived_reps=3),
                                _bc_set("155", "3", skipped=True),
                                _bc_set("70", "5", unit="kg"),
                            ],
                        },
                        {
                            # AMRAP set: amount is the target "15+", value left blank;
                            # archived_* hold what was actually done.
                            "name": "Pull-Up (Weighted)",
                            "type": "Bodyweight",
                            "sets": [
                                _bc_set("", "15+", target_type="reps_max", archived_weight=20, archived_reps=5),
                            ],
                        },
                        {"name": "Plank", "type": "Bodyweight", "sets": [_bc_set("", "60", valueEmpty=True, target_type="time")]},
                    ],
                }
            ],
            "2026-09-01": [{"title": "Old", "records": [{"name": "X", "sets": [_bc_set("1", "1")]}]}],
        }
    }
    (w,) = boostcamp.normalize(profile, history, TZ, since=date(2026, 10, 1))
    assert w.key == "DvChH9zKFEnSEDEQ"
    assert w.garmin_name == "GZCLP · Week 1 · Day 1"
    assert w.anchor == datetime(2026, 10, 14, 19, 11, 28, tzinfo=TZ)
    squat, pullup, plank = w.exercises
    assert squat.equipment == "Barbell"
    assert [(s.reps, s.weight_kg) for s in squat.sets] == [(3, 70.307), (5, 70.0)]
    assert [(s.reps, s.weight_kg) for s in pullup.sets] == [(5, 9.072)]
    assert plank.sets[0].reps is None and plank.sets[0].duration_s == 60 and plank.sets[0].weight_kg is None


def test_short_program_name():
    assert boostcamp.short_program_name("GZCL Program (GZCLP)") == "GZCLP"
    assert boostcamp.short_program_name("5/3/1 for Beginners") == "5/3/1 for Beginners"


def test_garmin_name_without_program():
    assert _workout("k", date(2026, 10, 14)).garmin_name == "Day A"


def test_match_uses_finished_at():
    # Real data: Boostcamp finished_at lands a few minutes after the watch starts.
    w = _workout("w1", date(2026, 10, 14))
    w.end = datetime(2026, 10, 14, 19, 11, 28, tzinfo=TZ)
    other = _act(1, datetime(2026, 10, 14, 13, 0, tzinfo=UTC))
    right = _act(2, datetime(2026, 10, 14, 23, 3, 57, tzinfo=UTC))  # 19:03:57 local
    pairs = matcher.match([w], [other, right], TZ, 120)
    assert [(p[0].key, p[1].activity_id) for p in pairs] == [("w1", 2)]


def test_gzclp_names_with_equipment():
    m = ExerciseMapper(ROOT / "exercise_map.toml")
    assert m.lookup("Squat (T1)", "Barbell") == ("SQUAT", "BARBELL_BACK_SQUAT")
    assert m.lookup("Bench Press (T2)", "Barbell") == ("BENCH_PRESS", "BARBELL_BENCH_PRESS")
    assert m.lookup("Pull-Up (Weighted)", "Bodyweight") == ("PULL_UP", "WEIGHTED_PULL_UP")
    assert m.lookup("Dumbbell Row (T3)", "Dumbbell") == ("ROW", "DUMBBELL_ROW")
    assert m.lookup("Dip (Bodyweight)", "Bodyweight") == ("TRICEPS_EXTENSION", "BODY_WEIGHT_DIP")
    assert m.lookup("Bent Over Row (T3)", "Dumbbell") == ("ROW", "BENT_OVER_ROW_WITH_DUMBELL")
    assert m.unmapped == set()


# --- state ------------------------------------------------------------------

def test_state_roundtrip(tmp_path):
    s = State(tmp_path / "state.json")
    s.record("w1", 42, "abc")
    s.save()
    s2 = State(tmp_path / "state.json")
    assert s2.is_current("w1", 42, "abc")
    assert not s2.is_current("w1", 42, "changed")
    assert not s2.is_current("w1", 43, "abc")


# --- garmin write fallback --------------------------------------------------

class _FakeGarmin:
    """Rejects any payload containing a name in `bad`, like Garmin's 400."""

    def __init__(self, bad):
        self.bad = bad
        self.calls = []

    def set_activity_exercise_sets(self, activity_id, body):
        from garminconnect import GarminConnectConnectionError

        self.calls.append(body)
        names = {f"{e['category']}/{e['name']}" for s in body["exerciseSets"] for e in s["exercises"]}
        if names & self.bad:
            raise GarminConnectConnectionError("API Error 400 - Invalid Sub-Category Passed in the request")


def _body(*pairs):
    return {
        "activityId": 1,
        "exerciseSets": [{"exercises": [{"category": c, "name": n}]} for c, n in pairs],
    }


def test_put_finds_and_nulls_rejected_name():
    from gbsync import garmin

    api = _FakeGarmin(bad={"SQUAT/SQUAT"})
    body = _body(("SQUAT", "SQUAT"), ("BENCH_PRESS", "BENCH_PRESS"))
    assert garmin.put_exercise_sets(api, 1, body, set()) == {"SQUAT/SQUAT"}
    final = api.calls[-1]["exerciseSets"]
    assert final[0]["exercises"][0]["name"] is None
    assert final[1]["exercises"][0]["name"] == "BENCH_PRESS"


def test_put_skips_known_rejected_without_probing():
    from gbsync import garmin

    api = _FakeGarmin(bad={"SQUAT/SQUAT"})
    body = _body(("SQUAT", "SQUAT"), ("BENCH_PRESS", "BENCH_PRESS"))
    assert garmin.put_exercise_sets(api, 1, body, {"SQUAT/SQUAT"}) == set()
    assert len(api.calls) == 1


# --- config -----------------------------------------------------------------

def test_rename_activities_default_and_overrides(tmp_path, monkeypatch):
    from gbsync.config import load_config

    monkeypatch.delenv("GBSYNC_RENAME_ACTIVITIES", raising=False)
    cfg_file = tmp_path / "credentials.toml"
    cfg_file.write_text(f'[sync]\ndata_dir = "{tmp_path / "data"}"\n')
    assert load_config(cfg_file).rename_activities is True

    cfg_file.write_text(cfg_file.read_text() + "rename_activities = false\n")
    assert load_config(cfg_file).rename_activities is False

    monkeypatch.setenv("GBSYNC_RENAME_ACTIVITIES", "true")
    assert load_config(cfg_file).rename_activities is True


# --- notifications ----------------------------------------------------------

def _cfg(tmp_path, webhook):
    from gbsync.config import load_config

    cfg_file = tmp_path / "credentials.toml"
    body = f'[sync]\ndata_dir = "{tmp_path / "data"}"\n'
    if webhook:
        body += f'[notify]\ndiscord_webhook = "{webhook}"\n'
    cfg_file.write_text(body)
    return load_config(cfg_file)


def test_discord_webhook_from_toml_and_env(tmp_path, monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    assert _cfg(tmp_path, None).discord_webhook is None
    assert _cfg(tmp_path, "https://example.test/a").discord_webhook == "https://example.test/a"
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://example.test/b")
    assert _cfg(tmp_path, "https://example.test/a").discord_webhook == "https://example.test/b"


def test_notify_posts_and_never_raises(tmp_path, monkeypatch):
    import json
    import urllib.request

    from gbsync import notify

    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    sent = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        sent.append(req)
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    notify.send(_cfg(tmp_path, None), "hi")
    assert sent == []

    notify.send(_cfg(tmp_path, "https://example.test/hook"), "hi")
    (req,) = sent
    assert req.full_url == "https://example.test/hook"
    assert json.loads(req.data) == {"content": "gbsync: hi"}

    def broken(req, timeout):
        raise OSError("down")

    monkeypatch.setattr(urllib.request, "urlopen", broken)
    notify.send(_cfg(tmp_path, "https://example.test/hook"), "hi")  # logged, not raised


def test_state_unmapped_notified_roundtrip(tmp_path):
    s = State(tmp_path / "state.json")
    s.add_unmapped_notified({"Dead Hang"})
    s.save()
    s2 = State(tmp_path / "state.json")
    assert {"Dead Hang", "Plank"} - s2.unmapped_notified == {"Plank"}
