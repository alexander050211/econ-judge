"""landing.js (the front page's live status) and the poll round-ui.js lends it
as window.econRoundState, run for real in node.

tests/landing_clock_harness.js loads both scripts into a vm context holding a
stand-in for the page — the landing markup INDEX_CONTENT serves, a navbar, a
fake clock with fake timers and a fake server — and runs one named scenario.
This file builds every payload with competition.py's own competition_status(),
takes every duration from competition.py's schedule and owns every
expectation, so a new year's schedule needs no edit here. Without node on
PATH the node-backed tests skip; the checks on the sources run anywhere.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
LANDING_JS = ROOT / "econ_judge" / "assets" / "landing.js"
ROUND_UI_JS = ROOT / "econ_judge" / "assets" / "round-ui.js"
HARNESS_JS = Path(__file__).with_name("landing_clock_harness.js")
NODE = shutil.which("node")

spec = importlib.util.spec_from_file_location(
    "landing_clock_competition", ROOT / "econ_judge" / "competition.py"
)
competition = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = competition
spec.loader.exec_module(competition)

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
MS = timedelta(milliseconds=1)
MINUTE = timedelta(minutes=1)


def status(now, *, start=None, rehearsal=False, unset=False):
    """The /api/v1/digital/competition payload a server would send at `now`."""
    env = {}
    if not unset:
        start = START if start is None else start
        env["ECON_JUDGE_COMPETITION_START"] = start if isinstance(start, str) else start.isoformat()
    if rehearsal:
        env["ECON_JUDGE_REHEARSAL"] = "true"
    with patch.dict(os.environ, env, clear=True):
        return competition.competition_status(now)


def spans(payload):
    """Round 1, the break and round 2, as a payload's timestamps draw them."""
    keys = ("round1_starts_at", "round1_ends_at", "round2_starts_at", "round2_ends_at")
    t = [datetime.fromisoformat(payload[key]) for key in keys]
    return t[1] - t[0], t[2] - t[1], t[3] - t[2]


# The schedule is competition.py's; the start is any instant, and this one is
# the camp's.
START = datetime(2026, 8, 4, 10, 0, tzinfo=timezone(timedelta(hours=9)))
ROUND_1 = competition.ROUND_1_DURATION
BREAK = competition.BREAK_DURATION
ROUND_2 = competition.ROUND_2_DURATION
ROUND_1_END = START + ROUND_1
ROUND_2_START = ROUND_1_END + BREAK
ROUND_2_END = ROUND_2_START + ROUND_2
REHEARSAL = spans(status(START, rehearsal=True))
# A moment well inside round 1, off any whole minute.
MID = START + ROUND_1 * 2 // 5 + timedelta(seconds=17)
# What INDEX_CONTENT serves in the ruler's total before landing.js writes it.
SERVED = {"total": [f"{ROUND_1 // MINUTE}분", f"{ROUND_2 // MINUTE}분"]}


def ms(moment):
    """A datetime as the timestamp Date.now() would give at that instant."""
    return (moment - EPOCH) // MS


def secs(value):
    return timedelta(seconds=value)


def left(delta):
    """Whole seconds on the clock: round-ui.js's Math.ceil, kept at zero."""
    return max(0, -(-(delta // MS) // 1000))


def at(moment, **options):
    """A payload generated at `moment`, read at `moment`."""
    return {"payload": status(moment, **options), "now": ms(moment)}


def stale(generated, read):
    """A payload generated at one instant and read at another, as happens when
    this machine's clock and the server's disagree around a boundary."""
    return {"payload": status(generated), "now": ms(read)}


# landing.js's arithmetic restated, where Python's would differ.
def js_round(value):
    """Math.round: a half goes up. Python's round() sends it to the even side."""
    return math.floor(value + 0.5)


def to_fixed_1(value):
    """Number.prototype.toFixed(1) on a non-negative double: of two equally
    near answers it takes the larger, so 31.25 is 31.3 where format() says 31.2."""
    return str(Decimal(value).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def share(elapsed, span):
    """--el: how much of a span has passed, clamped, as a one-decimal percentage."""
    whole = span // MS
    return f"{to_fixed_1(min(whole, max(0, elapsed // MS)) / whole * 100)}%"


def digits(remaining):
    """The hero clock's three groups for `remaining` whole seconds."""
    hours, rest = divmod(remaining, 3600)
    return {"hh": f"{hours}:" if hours else "", "mm": f"{rest // 60:02d}", "ss": f":{rest % 60:02d}"}


def pill_text(remaining):
    """The navbar pill's HH:MM:SS."""
    hours, rest = divmod(remaining, 3600)
    return f"{hours:02d}:{rest // 60:02d}:{rest % 60:02d}"


def alarms(remaining):
    """In a round, the last five minutes are the tail and the last one is red."""
    return {"tail": remaining <= 300, "warn": 60 < remaining <= 300, "crit": remaining <= 60}


def ruler(span):
    """A round's ruler: one tick a minute, a major tick every ten."""
    ticks = max(1, js_round(span / MINUTE))
    return {"ticks": ticks, "major": max(1, js_round(ticks / 10)), "total": f"{ticks}분"}


def elapsed_label(elapsed):
    return f"경과 {elapsed // MINUTE}분"


def run_node(scenario, **data):
    data.update(scenario=scenario, landing=str(LANDING_JS), roundUi=str(ROUND_UI_JS), served=SERVED)
    result = subprocess.run(
        [NODE, str(HARNESS_JS)],
        input=json.dumps(data),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    if result.returncode != 0:
        raise AssertionError(f"node exited {result.returncode}:\n{result.stderr}")
    return json.loads(result.stdout)


QUIET = {"warn": False, "crit": False, "tail": False}
DASHES = {"hh": "", "mm": "--", "ss": ":--"}
NO_RULER = {"ticks": None, "major": None, "elapsed": None, "total": None}
STAGE_PROPS = {"--el", "--ticks", "--major"}

# What #econ-landing-status says when the page moves on under a reader.
NEWS = {
    "before": "1라운드 시작 전입니다.",
    "round1": "1라운드가 시작되었습니다.",
    "break": "휴식 시간입니다. 2라운드는 곧 시작됩니다.",
    "round2": "2라운드가 시작되었습니다.",
    "finished": "온라인 라운드가 끝났습니다.",
    "open": "준비 모드입니다. 두 라운드가 모두 열려 있습니다.",
    "misconfigured": "대회 일정 설정에 오류가 있습니다.",
}
TAIL = {"round1": "1라운드가 5분 안에 마감됩니다.", "round2": "2라운드가 5분 안에 마감됩니다."}


def compute_table():
    """(name, payload, now_ms, signed_in, expected subset of compute()'s view)."""
    table = []

    def case(name, sample, expected, signed_in=False):
        table.append((name, sample["payload"], sample["now"], signed_in, expected))

    # before: counts to the start, never alarms, and the hours group appears
    # only with an hour or more left.
    sample = at(START - secs(725))
    case("before 12:05", sample, {
        "phase": "before", "hh": "", "mm": "12", "ss": ":05", **QUIET, **NO_RULER, "done": False,
        "long": False, "el": "0.0%", "remaining": 725, "target": sample["payload"]["round1_starts_at"],
    })
    for seconds in (300, 60, 1):
        case(f"before {seconds}s", at(START - secs(seconds)), {"phase": "before", "remaining": seconds, **QUIET})
    case("before, read after the start", stale(START - secs(1), START + 500 * MS), {
        "phase": "before", "remaining": 0, "hh": "", "mm": "00", "ss": ":00", **QUIET,
    })
    case("before 1:05:07", at(START - secs(3907)), {
        "phase": "before", "hh": "1:", "mm": "05", "ss": ":07", "long": False,
    })
    case("before 99:59:59", at(START - secs(99 * 3600 + 59 * 60 + 59)), {"hh": "99:", "long": False})
    case("before 100:00:05", at(START - secs(100 * 3600 + 5)), {
        "hh": "100:", "mm": "00", "ss": ":05", "long": True, **QUIET,
    })

    # The five- and one-minute thresholds, on both rounds.
    thresholds = (
        (301, {"mm": "05", "ss": ":01"}, QUIET),
        (300, {"mm": "05", "ss": ":00"}, {"warn": True, "crit": False, "tail": True}),
        (61, {"mm": "01", "ss": ":01"}, {"warn": True, "crit": False, "tail": True}),
        (60, {"mm": "01", "ss": ":00"}, {"warn": False, "crit": True, "tail": True}),
        (1, {"mm": "00", "ss": ":01"}, {"warn": False, "crit": True, "tail": True}),
        (0, {"mm": "00", "ss": ":00"}, {"warn": False, "crit": True, "tail": True}),
    )
    for phase, end in (("round1", ROUND_1_END), ("round2", ROUND_2_END)):
        for seconds, clock, flags in thresholds:
            # At zero the server has already moved on, so the payload is the
            # last one it sent a second earlier.
            sample = stale(end - secs(max(seconds, 1)), end - secs(seconds))
            case(f"{phase} {seconds}s", sample, {"phase": phase, "remaining": seconds, "hh": "", **clock, **flags})

    # The same ceiling round-ui.js takes: 300.4 s left is still 301.
    case("round1 300.4s", at(ROUND_1_END - secs(300.4)), {"remaining": 301, **QUIET})
    case("round1 60.2s", at(ROUND_1_END - secs(60.2)), {"remaining": 61, "warn": True, "crit": False, "tail": True})

    # break: counts to round 2, never alarms, draws no ruler.
    for part in (BREAK // 2, BREAK // 10, secs(1)):
        sample = at(ROUND_2_START - part)
        case(f"break {left(part)}s left", sample, {
            "phase": "break", "remaining": left(part), **digits(left(part)), **QUIET, **NO_RULER,
            "el": share(BREAK - part, BREAK), "target": sample["payload"]["round2_starts_at"],
        })

    # Mid-round: the ruler follows the timestamps.
    case("round1 mid-round", at(MID), {
        "phase": "round1", **digits(left(ROUND_1_END - MID)), **alarms(left(ROUND_1_END - MID)),
        "el": share(MID - START, ROUND_1), **ruler(ROUND_1), "elapsed": elapsed_label(MID - START),
    })
    case("round1 at its start", at(START), {
        "phase": "round1", **digits(left(ROUND_1)), **alarms(left(ROUND_1)), "el": "0.0%",
        **ruler(ROUND_1), "elapsed": "경과 0분",
    })
    quarter = ROUND_2 // 4
    case("round2 a quarter in", at(ROUND_2_START + quarter), {
        "phase": "round2", **digits(left(ROUND_2 - quarter)), "el": share(quarter, ROUND_2),
        **ruler(ROUND_2), "elapsed": elapsed_label(quarter),
    })
    # Math.round, not Python's: an 85-minute round has 8.5 tens of ticks, and
    # its ruler takes 9 majors (round() would say 8).
    span = {
        "phase": "round1", "round1_starts_at": START.isoformat(),
        "round1_ends_at": (START + timedelta(minutes=85)).isoformat(),
    }
    case("an 85-minute round", {"payload": span, "now": ms(START + MINUTE)}, {
        "ticks": 85, "major": 9, "total": "85분", "elapsed": "경과 1분",
    })

    # finished, open, misconfigured.
    case("finished", at(ROUND_2_END + timedelta(hours=1)), {
        "phase": "finished", "hh": "", "mm": "00", "ss": ":00", "done": True, **QUIET, **NO_RULER,
        "el": "100.0%", "remaining": None, "target": None,
    })
    case("open", at(START, unset=True), {
        "phase": "open", **DASHES, "done": False, **QUIET, **NO_RULER, "el": "0.0%",
        "remaining": None, "target": None,
    })
    case("misconfigured", at(START, start="2026-08-04T10:00:00"), {
        "phase": "misconfigured", **DASHES, "done": False, **QUIET, "el": "0.0%", "remaining": None,
    })

    # Missing or broken timestamps: the phase still shows, the clock does not.
    now = ms(START + secs(600))
    case("round1 without timestamps", {"payload": {"phase": "round1"}, "now": now}, {
        "phase": "round1", **DASHES, **QUIET, **NO_RULER, "el": "0.0%", "remaining": None, "target": None,
    })
    broken = dict(status(ROUND_2_START + secs(60)), round2_starts_at="not a date", round2_ends_at="not a date")
    case("round2 with unparseable timestamps", {"payload": broken, "now": ms(ROUND_2_START + secs(60))}, {
        "phase": "round2", **DASHES, **QUIET, **NO_RULER, "el": "0.0%",
    })
    no_start = dict(status(START - secs(60)), round1_starts_at=None)
    case("before without a start", {"payload": no_start, "now": ms(START - secs(60))}, {
        "phase": "before", **DASHES, "remaining": None,
    })
    half = dict(status(ROUND_1_END - secs(120)), round1_starts_at=None)
    case("round1 with an end but no start", {"payload": half, "now": ms(ROUND_1_END - secs(120))}, {
        "phase": "round1", "hh": "", "mm": "02", "ss": ":00", **alarms(120), **NO_RULER, "el": "0.0%",
    })

    # No payload, or one this script does not recognise: the served state.
    case("unknown phase", {"payload": dict(status(START), phase="loading"), "now": ms(START)}, {
        "phase": None, **DASHES, **QUIET, "el": "0.0%", "remaining": None,
    })
    case("no payload", {"payload": None, "now": ms(START)}, {"phase": None, **DASHES, **QUIET, "el": "0.0%"})
    case("a string for a payload", {"payload": "round1", "now": ms(START)}, {"phase": None, **DASHES})

    # Rehearsal: short rounds, and the ruler still follows the timestamps.
    r1, rest, r2 = REHEARSAL
    into = r1 * 3 // 4
    case("rehearsal round1", at(START + into, rehearsal=True), {
        "phase": "round1", "remaining": left(r1 - into), **alarms(left(r1 - into)), **ruler(r1),
        "elapsed": elapsed_label(into), "el": share(into, r1),
    })
    into = r2 // 4
    case("rehearsal round2", at(START + r1 + rest + into, rehearsal=True), {
        "phase": "round2", "remaining": left(r2 - into), **alarms(left(r2 - into)), **ruler(r2),
        "el": share(into, r2), "elapsed": elapsed_label(into),
    })

    # --el clamps when this machine's clock is outside the span.
    case("round1 read before its start", stale(START + secs(10), START - secs(5)), {
        "phase": "round1", "el": "0.0%", "elapsed": "경과 0분", **digits(left(ROUND_1 + secs(5))),
    })
    case("round1 read after its end", stale(ROUND_1_END - secs(1), ROUND_1_END + secs(30)), {
        "phase": "round1", "el": "100.0%", "elapsed": elapsed_label(ROUND_1), "remaining": 0,
    })
    case("break read before it", stale(ROUND_1_END + secs(1), ROUND_1_END - secs(10)), {"phase": "break", "el": "0.0%"})
    case("break read after it", stale(ROUND_2_START - secs(1), ROUND_2_START + secs(10)), {
        "phase": "break", "el": "100.0%", "remaining": 0, **QUIET,
    })

    # Auth is whatever the caller says; the payload has no part in it.
    case("signed in", at(MID), {"auth": "in"}, signed_in=True)
    case("signed out", at(MID), {"auth": "out"}, signed_in=False)
    return table


def news_table():
    """(name, what the last render showed, the next sample, the note it gets)."""

    def was(phase, tail=False):
        return {"phase": phase, "tail": tail}

    mid = at(MID)
    tail_2 = at(ROUND_2_END - secs(300))
    unknown = {"payload": dict(mid["payload"], phase="loading"), "now": mid["now"]}
    r1, _rest, _r2 = REHEARSAL
    return [
        ("first render", None, mid, ""),
        ("first render in the tail", None, at(ROUND_1_END - secs(100)), ""),
        ("a tick in the same phase", was("round1"), mid, ""),
        ("round 1 enters its tail", was("round1"), at(ROUND_1_END - secs(300)), TAIL["round1"]),
        ("still in the tail", was("round1", True), at(ROUND_1_END - secs(200)), ""),
        ("the tail lifts without a phase change", was("round1", True), mid, ""),
        ("round 1 opens", was("before"), at(START + secs(1)), NEWS["round1"]),
        ("round 1 closes", was("round1", True), at(ROUND_1_END + secs(1)), NEWS["break"]),
        ("round 2 opens", was("break"), at(ROUND_2_START + secs(1)), NEWS["round2"]),
        ("round 2 enters its tail", was("round2"), tail_2, TAIL["round2"]),
        ("round 2 closes", was("round2", True), at(ROUND_2_END + secs(1)), NEWS["finished"]),
        ("a start is set", was("open"), at(START - secs(60)), NEWS["before"]),
        ("the start breaks", was("before"), at(START, start="2026-08-04T10:00:00"), NEWS["misconfigured"]),
        ("the start is cleared", was("misconfigured"), at(START, unset=True), NEWS["open"]),
        # A rehearsal round is shorter than its tail, so it opens inside it.
        ("a rehearsal round opens", was("before"), at(START + secs(1), rehearsal=True),
         f"{NEWS['round1']} {TAIL['round1']}" if left(r1 - secs(1)) <= 300 else NEWS["round1"]),
        # A page that slept through the start first reads round 1 with four
        # minutes left: TAIL has to be true then too.
        ("round 1 first read inside its tail", was("before"), at(ROUND_1_END - secs(240)),
         f"{NEWS['round1']} {TAIL['round1']}"),
        ("a payload the page cannot read", was("round1"), unknown, ""),
    ]


# The four camp accounts are 1조…4조; the rest pin the particle rule.
USER_LABELS = (
    ("2조", "2조로 "),
    ("4조", "4조로 "),
    ("  1조 ", "1조로 "),
    ("운영진", "운영진으로 "),  # final ㄴ
    ("서울", "서울로 "),  # final ㄹ takes 로
    ("team3", "team3으로 "),  # 삼
    ("team7", "team7로 "),  # 칠
    ("team10", "team10으로 "),  # 십
    ("admin", "admin(으)로 "),  # no way to know the final
    ("", ""),
    ("   ", ""),
    (None, ""),
)


@unittest.skipUnless(NODE, "node is not installed")
class LandingComputeTests(unittest.TestCase):
    """compute(), announcement() and userLabel(): every value the page shows."""

    @classmethod
    def setUpClass(cls):
        cls.table = compute_table()
        cls.news = news_table()
        result = run_node(
            "compute",
            cases=[[payload, now, signed_in] for _name, payload, now, signed_in, _expected in cls.table],
            names=[name for name, _label in USER_LABELS],
            news=[[previous, sample["payload"], sample["now"]] for _name, previous, sample, _said in cls.news],
        )
        cls.views = result["views"]
        cls.labels = result["labels"]
        cls.said = result["news"]

    def test_every_phase_competition_can_return_is_covered(self):
        phases = {view["phase"] for view in self.views}
        self.assertLessEqual(
            {"before", "round1", "break", "round2", "finished", "open", "misconfigured"}, phases
        )

    def test_table(self):
        for (name, _payload, _now, _signed_in, expected), view in zip(self.table, self.views):
            with self.subTest(name):
                self.assertEqual({key: view[key] for key in expected}, expected)

    def test_alarms_are_exclusive_and_only_in_rounds(self):
        for (name, _payload, _now, _signed_in, _expected), view in zip(self.table, self.views):
            with self.subTest(name):
                self.assertFalse(view["warn"] and view["crit"])
                if view["phase"] not in ("round1", "round2"):
                    self.assertEqual((view["warn"], view["crit"], view["tail"]), (False, False, False))
                if view["tail"]:
                    self.assertTrue(view["warn"] or view["crit"])

    def test_el_is_a_clamped_one_decimal_percentage(self):
        for (name, _payload, _now, _signed_in, _expected), view in zip(self.table, self.views):
            with self.subTest(name):
                match = re.fullmatch(r"(\d{1,3}\.\d)%", view["el"])
                self.assertIsNotNone(match, view["el"])
                self.assertTrue(0 <= float(match.group(1)) <= 100)

    def test_the_status_line_speaks_only_when_the_page_moves_on(self):
        for (name, _previous, _sample, expected), said in zip(self.news, self.said):
            with self.subTest(name):
                self.assertEqual(said, expected)

    def test_every_phase_has_its_news(self):
        entered = {
            sample["payload"]["phase"] for _name, previous, sample, said in self.news
            if previous and said and sample["payload"]["phase"] != previous["phase"]
        }
        self.assertEqual(entered, set(NEWS))

    def test_user_label_carries_the_particle(self):
        for (name, expected), label in zip(USER_LABELS, self.labels):
            with self.subTest(name=name):
                self.assertEqual(label, expected)


@unittest.skipUnless(NODE, "node is not installed")
class LandingDomTests(unittest.TestCase):
    """landing.js against the stub page: gate, data source, writes, zero."""

    def assertOnlyRulerProps(self, view):
        self.assertLessEqual(set(view["stage"]["props"]), STAGE_PROPS)

    def test_gate_does_nothing_without_the_landing_markup(self):
        result = run_node("gate", **at(MID))
        self.assertEqual(result, {"timers": 0, "fetches": 0, "listeners": 0, "hasCompute": True})

    def test_fallback_polls_like_round_ui(self):
        result = run_node(
            "fallback", init={"userId": 0, "userName": None}, mid=at(MID), brk=at(ROUND_1_END + secs(5)),
        )
        # First paint: auth only. The phase waits for the server.
        first = result["firstPaint"]
        self.assertEqual(first["stage"], {"attrs": {"data-auth": "out"}, "props": {}, "classes": ["stage"]})
        self.assertEqual((first["mm"], first["ss"], first["status"]), ("--", ":--", ""))
        self.assertEqual(first["user"], [""])
        self.assertEqual(
            result["fetchOptions"],
            [["/api/v1/digital/competition", {"credentials": "same-origin", "cache": "no-store"}]],
        )
        self.assertEqual(result["intervals"], [250, 15000])
        self.assertEqual(result["listeners"], ["visibilitychange"])

        live = result["live"]
        r = ruler(ROUND_1)
        self.assertEqual(live["stage"]["attrs"], {"data-auth": "out", "data-phase": "round1"})
        self.assertEqual(
            live["stage"]["props"],
            {"--el": share(MID - START, ROUND_1), "--ticks": str(r["ticks"]), "--major": str(r["major"])},
        )
        self.assertEqual({key: live[key] for key in ("hh", "mm", "ss")}, digits(left(ROUND_1_END - MID)))
        self.assertEqual(live["elapsed"], [elapsed_label(MID - START)])
        self.assertEqual(live["total"], [r["total"]])
        self.assertEqual(live["classes"], ["clock"])
        self.assertEqual(live["status"], "")
        self.assertEqual(result["afterFailures"], live)

        flipped = result["afterFlip"]
        self.assertEqual(flipped["stage"]["attrs"], {"data-auth": "out", "data-phase": "break"})
        self.assertEqual(set(flipped["stage"]["props"]), {"--el"})
        self.assertEqual(flipped["classes"], ["clock"])
        self.assertEqual(flipped["status"], NEWS["break"])

    def zero_run(self, flips_at, steps):
        """landing.js alone at the end of round 1, with this machine's clock
        ahead of the server's: the server says round 1 until `flips_at` ms
        after this machine's zero (None: for the whole run)."""
        boot = ROUND_1_END - secs(5)
        timeline = [[ms(boot) - 1, status(boot)]]
        if flips_at is not None:
            timeline.append([ms(ROUND_1_END) + flips_at, status(ROUND_1_END + flips_at * MS)])
        return run_node("fallbackZero", boot=ms(boot), zero=ms(ROUND_1_END), timeline=timeline, steps=steps)

    def test_fallback_asks_again_while_the_server_lags(self):
        # The server flips 2.5 s after this machine's zero. One ask at zero
        # gets round 1 back; the retries 1 and 2 s later catch the flip.
        result = self.zero_run(2500, [["advance", 17000]])
        self.assertEqual(result["fetches"], [-5000, 0, 1000, 3000, 10000])
        self.assertEqual(result["changes"], [[3000, "break"]])
        self.assertEqual(result["view"]["status"], NEWS["break"])

    def test_fallback_retries_back_off_to_the_poll(self):
        # A server that never flips: 1, 2, 4, 8 s apart, then the 15 s poll
        # alone — no two asks more than 15 s apart, and none doubled up.
        result = self.zero_run(None, [["advance", 50000]])
        self.assertEqual(result["fetches"], [-5000, 0, 1000, 3000, 7000, 10000, 18000, 25000, 40000])
        self.assertEqual(result["changes"], [])
        gaps = [b - a for a, b in zip(result["fetches"][1:], result["fetches"][2:])]
        self.assertLessEqual(max(gaps), 15000)

    def test_fallback_retries_wait_while_the_tab_is_hidden(self):
        result = self.zero_run(None, [["advance", 4000], ["hide"], ["advance", 21000], ["show"], ["advance", 1000]])
        # Zero passes while hidden: one ask, then nothing until the viewer is
        # back, and the retries pick up from there.
        self.assertEqual(result["fetches"], [-5000, 0, 20000, 21000])

    def test_fallback_drops_an_answer_that_lost_the_race(self):
        result = run_node("fallbackOrder", r1=at(ROUND_1_END - secs(30)), brk=at(ROUND_1_END + secs(5)))
        self.assertEqual(result["fetches"], ["round1", "round1", "break"])
        self.assertEqual(result["overtaken"]["stage"]["attrs"]["data-phase"], "break")
        self.assertEqual(result["after"], result["overtaken"])
        self.assertEqual(result["statusWrites"], 1)

    def test_shares_round_ui_poll_and_agrees_with_the_navbar_clock(self):
        sweep_points = [
            MID,
            ROUND_1_END - secs(300.001),
            ROUND_1_END - secs(299.999),
            ROUND_1_END - secs(300),
            ROUND_1_END - secs(60.5),
            ROUND_1_END - secs(60),
            ROUND_1_END - secs(59.001),
            ROUND_1_END - secs(0.4),
            START + secs(0.25),
            START + secs(599.75),
        ]
        result = run_node(
            "shared",
            init={"userId": 3, "userName": "2조"},
            mid=at(MID),
            sweep=[ms(point) for point in sweep_points],
            late=at(ROUND_1_END - secs(30)),
            zeroNow=ms(ROUND_1_END + 100 * MS),
            brk=at(ROUND_1_END + secs(5)),
        )
        self.assertTrue(result["hasShared"])
        self.assertEqual(result["firstPaint"]["stage"]["attrs"], {"data-auth": "in"})
        self.assertEqual(result["firstPaint"]["user"], ["2조로 "])
        self.assertEqual(result["firstPaint"]["status"], "")
        # One poll on the page: round-ui.js's. Its two timers plus landing's tick.
        self.assertEqual(result["fetchesAtBoot"], 1)
        self.assertEqual(result["intervals"], [250, 250, 15000])

        live = result["live"]
        self.assertEqual(live["stage"]["attrs"], {"data-auth": "in", "data-phase": "round1"})
        self.assertEqual({key: live[key] for key in ("hh", "mm", "ss")}, digits(left(ROUND_1_END - MID)))
        self.assertEqual(result["pill"], pill_text(left(ROUND_1_END - MID)))
        self.assertEqual(result["get"]["phase"], "round1")
        self.assertEqual(result["idleWrites"], 0)
        self.assertOnlyRulerProps(live)

        for point in result["sweep"]:
            with self.subTest(now=point["now"]):
                hero = point["hero"]
                h, m, s = (int(part) for part in point["pill"].split(":"))
                hero_hours = int(hero["hh"].rstrip(":") or 0)
                self.assertEqual(
                    h * 3600 + m * 60 + s,
                    hero_hours * 3600 + int(hero["mm"]) * 60 + int(hero["ss"].lstrip(":")),
                )
                self.assertEqual(point["pillClasses"], [c for c in hero["classes"] if c.startswith("is-")])

        survived = result["afterBadSubscriber"]
        self.assertEqual(survived["pill"], pill_text(30))
        self.assertEqual({key: survived["hero"][key] for key in ("hh", "mm", "ss")}, digits(30))
        self.assertEqual(survived["hero"]["classes"], ["clock", "is-crit"])

        # At zero the asking is round-ui.js's own: landing.js adds no request
        # that could race it.
        self.assertEqual(result["zeroRefreshes"], 0)
        self.assertEqual(result["atZero"]["classes"], ["clock", "is-crit"])
        flipped = result["afterFlip"]
        self.assertEqual(flipped["stage"]["attrs"], {"data-auth": "in", "data-phase": "break"})
        self.assertEqual(flipped["classes"], ["clock"])
        self.assertEqual(flipped["status"], NEWS["break"])
        self.assertOnlyRulerProps(flipped)
        self.assertEqual(result["pillPhase"], "break")
        self.assertEqual(result["events"], [["econ:competition-change", "break"]])
        # Nothing in JS animates: no frame is ever asked for.
        self.assertEqual(result["frames"], 0)

    def test_round_ui_without_a_navbar_leaves_landing_to_poll(self):
        result = run_node("noNavbar", init={}, mid=at(MID))
        self.assertEqual(result["shared"], "undefined")
        self.assertEqual(result["fetches"], 1)
        self.assertEqual(result["intervals"], [250, 15000])
        self.assertEqual(result["live"]["stage"]["attrs"], {"data-auth": "out", "data-phase": "round1"})

    def test_a_start_days_away_marks_the_clock_long(self):
        # Three hour digits: the clock steps its size down (.is-long).
        result = run_node("noNavbar", init={}, mid=at(START - secs(100 * 3600 + 5)))
        live = result["live"]
        self.assertEqual(live["stage"]["attrs"], {"data-auth": "out", "data-phase": "before"})
        self.assertEqual((live["hh"], live["mm"], live["ss"]), ("100:", "00", ":05"))
        self.assertEqual(live["classes"], ["clock", "is-long"])

    def test_auth_follows_the_session_id_not_the_name(self):
        # CTFd renders userId from the session; a signed-in account with no
        # usable name is still signed in, and its sentence keeps no name.
        result = run_node("noNavbar", init={"userId": 7, "userName": " "}, mid=at(MID))
        self.assertEqual(result["live"]["stage"]["attrs"], {"data-auth": "in", "data-phase": "round1"})
        self.assertEqual(result["live"]["user"], [""])
        result = run_node("noNavbar", init={"userId": 0, "userName": "2조"}, mid=at(MID))
        self.assertEqual(result["live"]["stage"]["attrs"], {"data-auth": "out", "data-phase": "round1"})
        self.assertEqual(result["live"]["user"], [""])


@unittest.skipUnless(NODE, "node is not installed")
class StatusLineTests(unittest.TestCase):
    """#econ-landing-status through a whole contest: written only when the
    phase or the tail changes, never on the first render, never per tick."""

    def test_a_contest_is_told_line_by_line(self):
        # Round 1 is extended by ten minutes while it is in its tail.
        extended_end = ROUND_1_END + timedelta(minutes=10)
        extended = dict(status(ROUND_1_END - secs(100)), round1_ends_at=extended_end.isoformat())
        brk, brk_again = status(ROUND_1_END + secs(1)), status(ROUND_1_END + secs(2))
        unreadable = dict(brk, phase="loading")
        finished = status(ROUND_2_END + secs(1))

        def tick(moment, ticks=1):
            return {"now": ms(moment), "ticks": ticks}

        def answer(moment, payload, ticks=1):
            return {"now": ms(moment), "payload": payload, "ticks": ticks}

        # (what happens, the step, then what the page shows: phase, tail,
        # the status line, and how many times it has been written).
        steps = [
            ("opened in round 1", at(ROUND_1_END - secs(302)), "round1", None, "", 0),
            ("a tick", tick(ROUND_1_END - secs(301)), "round1", None, "", 0),
            ("five minutes left", tick(ROUND_1_END - secs(300)), "round1", "1", TAIL["round1"], 1),
            ("ticks in the tail", tick(ROUND_1_END - secs(120), 8), "round1", "1", TAIL["round1"], 1),
            ("the round is extended", answer(ROUND_1_END - secs(100), extended), "round1", None, TAIL["round1"], 1),
            ("its new tail", tick(extended_end - secs(300)), "round1", "1", TAIL["round1"], 2),
            ("the break", answer(extended_end - secs(299), brk), "break", None, NEWS["break"], 3),
            ("an unreadable answer", answer(extended_end - secs(298), unreadable), None, None, NEWS["break"], 3),
            ("the break again", answer(extended_end - secs(297), brk_again), "break", None, NEWS["break"], 3),
            ("another unreadable answer", answer(extended_end - secs(296), unreadable), None, None, NEWS["break"], 3),
            ("round 2, straight after it", at(ROUND_2_START + secs(1)), "round2", None, NEWS["round2"], 4),
            ("round 2's tail", tick(ROUND_2_END - secs(300)), "round2", "1", TAIL["round2"], 5),
            ("the end", answer(ROUND_2_END + secs(1), finished), "finished", None, NEWS["finished"], 6),
            ("after the end", answer(ROUND_2_END + secs(60), finished, 8), "finished", None, NEWS["finished"], 6),
        ]
        said = run_node("announce", steps=[step for _name, step, *_shown in steps])
        for (name, _step, *shown), line in zip(steps, said):
            with self.subTest(name):
                self.assertEqual([line["phase"], line["tail"], line["status"], line["writes"]], shown)
        # The clock's own classes along the way: alarms only in a tail, and a
        # finished contest's clock drawn done, with no alarm left on it.
        classes = {name: line["classes"] for (name, *_rest), line in zip(steps, said)}
        self.assertEqual(classes["a tick"], ["clock"])
        self.assertEqual(classes["five minutes left"], ["clock", "is-warn"])
        self.assertEqual(classes["ticks in the tail"], ["clock", "is-warn"])
        self.assertEqual(classes["the break"], ["clock"])
        self.assertEqual(classes["the end"], ["clock", "done"])
        self.assertEqual(classes["after the end"], ["clock", "done"])

    def test_opening_the_page_in_the_tail_says_nothing(self):
        steps = [at(ROUND_1_END - secs(100)), {"now": ms(ROUND_1_END - secs(99)), "ticks": 4}]
        said = run_node("announce", steps=steps)
        self.assertEqual(
            [(line["tail"], line["status"], line["writes"]) for line in said], [("1", "", 0), ("1", "", 0)]
        )


@unittest.skipUnless(NODE, "node is not installed")
class ToyKeyboardTests(unittest.TestCase):
    """The truth-table toy's 채점 box and 처음부터 buttons."""

    @classmethod
    def setUpClass(cls):
        cls.toy = run_node("toy", row0="a")

    def test_enter_grades_and_the_name_follows_the_label(self):
        toy = self.toy
        self.assertEqual(toy["atStart"], {"checked": False, "name": "채점하기", "writes": 0})
        self.assertEqual(toy["enter"], {"prevented": True, "checked": True, "name": "다시 풀기"})
        self.assertEqual(toy["enterAgain"], {"prevented": True, "checked": False, "name": "채점하기"})
        self.assertEqual(toy["clicked"], {"checked": True, "name": "다시 풀기"})

    def test_only_a_fresh_enter_counts(self):
        # A held key repeats, an IME owns Enter mid-composition, and Space is
        # the checkbox's own.
        for key in ("heldEnter", "composingEnter", "space"):
            with self.subTest(key):
                self.assertEqual(self.toy[key], {"prevented": False, "checked": True, "name": "다시 풀기"})

    def test_a_keyboard_reset_hands_focus_to_the_first_row(self):
        toy = self.toy
        self.assertEqual(toy["framesBeforeReset"], 0)
        # The reset lands first; focus and the name follow a frame later.
        self.assertEqual(
            toy["keyboardResetBeforeFrame"],
            {"checked": False, "name": "다시 풀기", "active": "BODY", "focusCalls": 0},
        )
        self.assertEqual(toy["keyboardReset"], {
            "checked": False, "name": "채점하기", "answer": "econ-toy-q0a",
            "focus": [{"id": "econ-toy-q0a", "options": {"preventScroll": False}}], "active": "econ-toy-q0a",
        })

    def test_a_pointer_reset_keeps_the_page_still(self):
        self.assertEqual(self.toy["pointerReset"], {
            "checked": False, "name": "채점하기",
            "focus": [{"id": "econ-toy-q0a", "options": {"preventScroll": True}}],
        })

    def test_a_reset_from_elsewhere_moves_no_focus(self):
        self.assertEqual(self.toy["unfocusedReset"], {"checked": False, "name": "채점하기", "focus": []})

    def test_focus_goes_to_whichever_answer_row_0_holds(self):
        toy = run_node("toy", row0="b")
        self.assertEqual(toy["keyboardReset"]["answer"], "econ-toy-q0b")
        self.assertEqual(toy["keyboardReset"]["focus"], [{"id": "econ-toy-q0b", "options": {"preventScroll": False}}])

    def test_the_toy_never_submits_and_only_a_reset_waits_a_frame(self):
        self.assertTrue(self.toy["submitPrevented"])
        self.assertEqual(self.toy["frames"], 3)


@unittest.skipUnless(NODE, "node is not installed")
class RoundStateBehaviourTests(unittest.TestCase):
    """window.econRoundState and the poll behind it, through round-ui.js itself."""

    def test_subscribers_hear_every_refresh_and_cannot_break_the_clock(self):
        first = status(ROUND_1_END - secs(600))
        second = status(ROUND_1_END + secs(5))
        result = run_node("roundState", payloads=[first, second], now=ms(ROUND_1_END - secs(600)))
        self.assertEqual(result["intervals"], [250, 15000])
        self.assertIsNone(result["getBefore"])
        self.assertEqual(result["earlyBeforeFirstPayload"], 0)
        self.assertEqual(result["getAfter"]["phase"], "round1")
        self.assertEqual(result["lateImmediate"], ["round1"])
        self.assertTrue(result["refreshReturnsPromise"])
        self.assertEqual(result["eventsAfterSamePhase"], [])
        heard = result["heard"]
        # early: first payload, same-phase refresh, new phase; the failed
        # refresh adds nothing.
        self.assertEqual(heard["early"], ["round1", "round1", "break"])
        self.assertEqual(heard["late"], ["round1", "round1", "break"])
        # Subscribed once a payload existed: heard at once, then on refresh.
        self.assertEqual(heard["bad"], 3)
        self.assertEqual(heard["gone"], ["round1", "round1"])
        self.assertEqual(result["events"], [["econ:competition-change", "break"]])
        self.assertEqual(result["getLast"]["phase"], "break")
        # The fake clock never moved: ten minutes of round 1 plus the break
        # still to run, drawn by a pill that a throwing subscriber sat beside.
        self.assertEqual(result["pill"], pill_text(600 + left(BREAK)))

    def test_the_poll_keeps_its_cadence_and_flips_once(self):
        boot = ROUND_1_END - secs(2)
        flip = ROUND_1_END + 600 * MS
        result = run_node(
            "roundPoll",
            boot=ms(boot),
            zero=ms(ROUND_1_END),
            window=3000,
            timeline=[[ms(boot) - 1, status(boot)], [ms(flip), status(flip)]],
        )
        self.assertEqual(
            result["first"], ["/api/v1/digital/competition", {"credentials": "same-origin", "cache": "no-store"}]
        )
        self.assertEqual(result["intervals"], [250, 15000])
        self.assertEqual(result["listeners"], ["visibilitychange"])
        # The 15 s poll pauses while the tab is hidden, and a return refreshes.
        self.assertEqual((result["pollWhileHidden"], result["onReturn"], result["pollWhileVisible"]), (0, 1, 1))
        # At zero the pill asks on every 250 ms tick until the server has
        # moved on, then stops; the change is announced once.
        self.assertEqual(result["aroundZero"], [[0, "round1"], [250, "round1"], [500, "round1"], [750, "break"]])
        self.assertEqual(result["events"], [["econ:competition-change", "break"]])
        self.assertEqual(result["pillPhase"], "break")
        self.assertEqual(result["pill"], pill_text(left(BREAK - secs(1))))

    def test_an_older_reply_never_overwrites_a_newer_one(self):
        # A reply asked for before the boundary lands after one asked for
        # after it: the page keeps break, and hears the change once.
        result = run_node("roundOrder", r1=at(ROUND_1_END - secs(2)), brk=at(ROUND_1_END + secs(1)))
        self.assertEqual(result["overtaken"]["phase"], "break")
        self.assertEqual(result["phase"], "break")
        self.assertEqual(result["view"]["stage"]["attrs"]["data-phase"], "break")
        self.assertEqual(result["view"], result["overtaken"]["view"])
        self.assertEqual(result["pillPhase"], "break")
        self.assertEqual(result["events"], [["econ:competition-change", "break"]])
        self.assertEqual(result["statusWrites"], 1)


VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Tree(HTMLParser):
    """HTML as nested {tag, attrs, text, children} — the harness's own shape."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = {"tag": "#root", "attrs": {}, "text": "", "children": []}
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": {name: value or "" for name, value in attrs}, "text": "", "children": []}
        self.stack[-1]["children"].append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        for depth in range(len(self.stack) - 1, 0, -1):
            if self.stack[depth]["tag"] == tag:
                del self.stack[depth:]
                return

    def handle_data(self, data):
        self.stack[-1]["text"] += data


def walk(node, ancestors=()):
    yield node, ancestors
    for child in node["children"]:
        yield from walk(child, ancestors + (node,))


def describe(landing):
    """What landing.js reads and writes in a page, as plain data. The stub
    page and INDEX_CONTENT have to give the same answer."""
    nodes = list(walk(landing))

    def text(node):
        return node["text"] + "".join(text(child) for child in node["children"])

    def has_class(node, name):
        return name in node["attrs"].get("class", "").split()

    by_id = {node["attrs"]["id"]: (node, ancestors) for node, ancestors in nodes if "id" in node["attrs"]}
    slots = {}
    for node, _ancestors in nodes:
        name = node["attrs"].get("data-econ-clock")
        if name is not None:
            # The total's served text is the page's copy, checked by
            # landing_page_test.py; here only the slot itself matters.
            slots.setdefault(name, []).append(None if name == "total" else text(node))
    run, run_ancestors = by_id.get("econ-toy-run", (None, ()))
    form = next((node for node in reversed(run_ancestors) if node["tag"] == "form"), None)

    def control(ident):
        node, ancestors = by_id.get(ident, (None, ()))
        if node is None:
            return None
        return {
            "tag": node["tag"], "type": node["attrs"].get("type"), "name": node["attrs"].get("name"),
            "checked": "checked" in node["attrs"], "in the toy's form": any(a is form for a in ancestors),
        }

    status_node, status_ancestors = by_id.get("econ-landing-status", (None, ()))
    clock = by_id.get("econ-landing-clock", (None, ()))[0]
    return {
        "stages": sum(has_class(node, "stage") for node, _ancestors in nodes),
        "clock": clock is not None and has_class(clock, "clock"),
        "slots": slots,
        "user": [text(node) for node, _ancestors in nodes if "data-econ-user" in node["attrs"]],
        "status": status_node and {
            "tag": status_node["tag"], "role": status_node["attrs"].get("role"),
            "aria-live": status_node["attrs"].get("aria-live"), "text": text(status_node),
            "in the stage": any(has_class(node, "stage") for node in status_ancestors),
        },
        "toy": {
            "run": control("econ-toy-run"),
            "q0a": control("econ-toy-q0a"),
            "q0b": control("econ-toy-q0b"),
            "resets": form and sum(
                1 for node, _ancestors in walk(form) if node["tag"] == "button" and node["attrs"].get("type") == "reset"
            ),
        },
    }


def index_landing():
    """#econ-landing as bin/bootstrap.py serves it (read with ast: importing
    bootstrap.py would need CTFd)."""
    tree = ast.parse((ROOT / "bin" / "bootstrap.py").read_text(encoding="utf-8"))
    for node in tree.body:
        targets = [getattr(target, "id", None) for target in getattr(node, "targets", ())]
        if isinstance(node, ast.Assign) and "INDEX_CONTENT" in targets:
            parser = Tree()
            parser.feed(ast.literal_eval(node.value))
            parser.close()
            return next(node for node, _ancestors in walk(parser.root) if node["attrs"].get("id") == "econ-landing")
    raise AssertionError("bin/bootstrap.py no longer defines INDEX_CONTENT")


@unittest.skipUnless(NODE, "node is not installed")
class StubPageTests(unittest.TestCase):
    """The harness's stand-in page is the one INDEX_CONTENT serves, as far as
    landing.js can tell."""

    def test_the_stub_is_the_served_page(self):
        stub = describe(run_node("page"))
        self.assertEqual(stub, describe(index_landing()))
        # And both are the page the tests above are written for.
        self.assertEqual(stub["slots"], {"hh": [""], "mm": ["--"], "ss": [":--"], "elapsed": [""], "total": [None]})
        self.assertEqual(stub["user"], [""])
        self.assertEqual(stub["toy"]["resets"], 3)


class RoundUiSourceTests(unittest.TestCase):
    """What the node tests cannot see, and a guard that runs without node."""

    def test_the_export_is_written_once_and_only_by_a_running_navbar_clock(self):
        source = ROUND_UI_JS.read_text(encoding="utf-8")
        writes = [match.start() for match in re.finditer(r"window\.econRoundState\s*=", source)]
        self.assertEqual(len(writes), 1)
        gate = re.search(
            r"if\s*\(\s*!\s*document\.querySelector\(\s*[\"']\.navbar[\"']\s*\)\s*\)\s*\{?\s*return\b", source
        )
        self.assertIsNotNone(gate)
        self.assertLess(gate.start(), writes[0])


class LandingSourceTests(unittest.TestCase):
    """Static checks on landing.js that no runtime test can see."""

    @classmethod
    def setUpClass(cls):
        cls.source = LANDING_JS.read_text(encoding="utf-8")

    def test_writes_text_never_markup(self):
        self.assertNotRegex(self.source, r"innerHTML|outerHTML|insertAdjacentHTML|document\.write")

    def test_never_claims_the_navbar_clock_id(self):
        self.assertNotIn("econ-round-countdown", self.source)

    def test_knows_exactly_the_phases_competition_can_return(self):
        # No "loading": the missing attribute IS the loading state.
        source = (ROOT / "econ_judge" / "competition.py").read_text(encoding="utf-8")
        returned = set(re.findall(r'CompetitionPhase\(\s*"(\w+)"', source))
        listed = re.search(r"const PHASES = \[([^\]]*)\];", self.source).group(1)
        self.assertEqual(set(re.findall(r'"(\w+)"', listed)), returned)
        self.assertEqual(len(returned), 7)


if __name__ == "__main__":
    unittest.main()
