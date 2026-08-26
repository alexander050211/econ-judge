"""What of /api/v1/digital/health and /api/v1/digital/export runs offline.

The repository does not vendor CTFd, so these routes are reached through
tests/plugin_surface_test.py's stubs. That draws a hard line through the two
endpoints:

* /health touches neither the database nor a JVM — its whole job is to read
  three files and some env vars — so the route itself is exercised here, both
  the ready (200) and the failing (503) answer, and both the public payload
  and the admin-gated ?verbose=1 one.
* /export is one long database query. The stubs emulate no SQLAlchemy result,
  so the route body is NOT covered; only `_export_csv`, the pure flattening
  step underneath it, is. What that leaves untested is stated in the module
  docstring of plugin_surface_test.py and nothing here pretends otherwise.
* `admins_only` is a no-op under the stubs, so the gate on the views that
  return team names, submission IPs or server paths is read out of the source
  instead. The public /health payload is checked by value rather than by that
  gate: what makes an unauthenticated route safe is what it answers with.
"""

from __future__ import annotations

import ast
import contextlib
import csv
import datetime
import importlib
import importlib.util
import io
import shutil
import tempfile
import types
import unittest
from pathlib import Path

from flask import Flask


ROOT = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Loaded by path rather than imported, so this file also runs on its own; the
# stub context manager is the only thing taken from it.
ctfd_stubs = load("health_surface", ROOT / "tests" / "plugin_surface_test.py").ctfd_stubs
register = load("health_register", ROOT / "tests" / "register_challenges.py")
# Imports nothing from CTFd, so it loads outside the stubs; only the two
# artifact locations are read from it, resolved the same way the grader does.
grader = load("health_grader", ROOT / "econ_judge" / "grader.py")


@contextlib.contextmanager
def plugin_app():
    """A bare Flask app carrying econ_judge's API routes, plus the modules
    whose globals a test may need to point somewhere else.

    `econ_judge.load` is deliberately NOT called: it also installs a
    before_request hook that syncs challenge visibility through
    Challenges.query, which the stubs do not emulate, so every request would
    answer 500. register_endpoints alone gives the shipped route functions
    under the real Flask dispatcher without faking a database.
    """
    with ctfd_stubs():
        endpoints = importlib.import_module("econ_judge.endpoints")
        app = Flask(__name__)
        endpoints.register_endpoints(app)
        yield types.SimpleNamespace(
            client=app.test_client(),
            endpoints=endpoints,
            health=importlib.import_module("econ_judge.health"),
            grader=importlib.import_module("econ_judge.grader"),
        )


def grading_artifacts_missing():
    """Why /health would answer 503 here for reasons that are not a bug."""
    if shutil.which(grader.JAVA) is None:
        return f"java executable '{grader.JAVA}' not found on PATH"
    if not Path(grader.DIGITAL_JAR).is_file():
        return f"Digital.jar not found at {grader.DIGITAL_JAR}"
    return None


def strings_in(value):
    """Every string anywhere in a JSON body, so a leak can be looked for by
    value rather than by the key someone happened to put it under."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings_in(item)


class HealthRouteTests(unittest.TestCase):
    """/health has two audiences and two payloads.

    The default answer is UNAUTHENTICATED — a host health check polls it — so
    it names each check and whether it passed, and nothing else. Everything a
    failure needs for diagnosis describes the server rather than readiness:
    the absolute path Digital.jar was looked for at, the resolved java binary,
    the secret-tests directory with its per-challenge row counts, and the
    ECON_JUDGE_* grading budget (GRADE_CONCURRENCY beside the worst-case
    seconds one request can spend in the grader is a map of exactly the
    bottleneck grader.py's semaphore protects). That lives behind ?verbose=1,
    which is admin-gated — see AdminGateTests.
    """

    PUBLIC_KEYS = {"ok", "failed_checks", "checks", "competition", "generated_at"}
    CHECK_NAMES = {"digital_jar", "java", "secret_tests"}

    def get(self, query=""):
        with plugin_app() as plugin:
            response = plugin.client.get(f"/api/v1/digital/health{query}")
        return response, response.get_json()["data"]

    def test_the_public_report_says_which_checks_passed_and_nothing_else(self):
        # Runs whether or not this machine can grade: the shape is the same at
        # 200 and at 503, and the shape is the whole point.
        _, data = self.get()
        self.assertEqual(set(data), self.PUBLIC_KEYS)
        self.assertEqual(set(data["checks"]), self.CHECK_NAMES)
        for name, check in data["checks"].items():
            with self.subTest(check=name):
                self.assertEqual(set(check), {"ok"})
        # Already served in full, to anyone, on /api/v1/digital/competition.
        self.assertIn("phase", data["competition"])

    def test_no_server_path_reaches_the_public_report(self):
        # By value, not by key name: a path moved to a differently-named field
        # would still be a path on an unauthenticated endpoint.
        with plugin_app() as plugin:
            response = plugin.client.get("/api/v1/digital/health")
            paths = {
                "Digital.jar": str(plugin.health.DIGITAL_JAR),
                "secret_tests dir": str(plugin.health.SECRET_TESTS_DIR),
                "repo root": str(plugin.grader.REPO_ROOT),
                "resolved java": shutil.which(plugin.grader.JAVA),
            }
        published = list(strings_in(response.get_json()["data"]))
        for label, path in paths.items():
            if not path:
                continue
            with self.subTest(leaked=label):
                self.assertEqual([s for s in published if path in s], [])

    @unittest.skipIf(
        grading_artifacts_missing(),
        f"grading artifacts unavailable: {grading_artifacts_missing()}",
    )
    def test_a_ready_checkout_answers_200(self):
        response, data = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        self.assertTrue(data["ok"])
        self.assertEqual(data["failed_checks"], [])

    @unittest.skipIf(
        grading_artifacts_missing(),
        f"grading artifacts unavailable: {grading_artifacts_missing()}",
    )
    def test_the_admin_report_carries_the_paths_the_public_one_withholds(self):
        # admins_only is a no-op under the stubs, so this reaches the verbose
        # branch directly; AdminGateTests is what checks the gate itself.
        _, data = self.get("?verbose=1")
        self.assertEqual(data["checks"]["digital_jar"]["path"], str(grader.DIGITAL_JAR))
        self.assertEqual(data["checks"]["secret_tests"]["dir"], str(grader.SECRET_TESTS_DIR))
        self.assertEqual(data["checks"]["java"]["resolved"], shutil.which(grader.JAVA))
        # The denominators an operator would read off this page have to be the
        # same numbers the challenges were registered with.
        rows = {row[0]: row[5] for row in register.CHALLENGES}
        self.assertEqual(
            data["checks"]["secret_tests"]["testcase_counts"],
            {str(cid): rows[cid] for cid in range(2, 16)},
        )
        self.assertEqual(
            data["tuning"]["worst_case_request_sec"],
            data["tuning"]["queue_wait_sec"] + data["tuning"]["timeout_sec"],
        )

    def missing_secret_tests(self, query=""):
        """The 503 answer, with SECRET_TESTS_DIR pointed at nothing."""
        with plugin_app() as plugin:
            with tempfile.TemporaryDirectory() as tmp:
                absent = Path(tmp) / "no-secret-tests"
                # Both modules: health reports the directory, grader is where
                # the per-challenge count is actually read from.
                plugin.health.SECRET_TESTS_DIR = absent
                plugin.grader.SECRET_TESTS_DIR = absent
                response = plugin.client.get(f"/api/v1/digital/health{query}")
        return response, response.get_json()["data"]

    def test_secret_tests_gone_missing_answers_503(self):
        response, data = self.missing_secret_tests()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(data["ok"])
        # A poller alerts on the name; that is all a failure owes the public.
        self.assertIn("secret_tests", data["failed_checks"])
        self.assertEqual(set(data), self.PUBLIC_KEYS)
        self.assertEqual(set(data["checks"]["secret_tests"]), {"ok"})

    def test_which_secret_tests_are_missing_is_for_an_admin(self):
        # The diagnosis itself is not withheld, only moved behind the gate —
        # otherwise the reduced public payload would cost an operator the one
        # thing this check exists to tell them.
        response, data = self.missing_secret_tests("?verbose=1")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            data["checks"]["secret_tests"]["missing_ids"], list(range(2, 16))
        )

    def test_the_cheap_probe_reports_no_deep_probe(self):
        with plugin_app() as plugin:
            response = plugin.client.get("/api/v1/digital/health?deep=0")
        self.assertNotIn("deep_probe", response.get_json()["data"])

    def test_an_unknown_deep_probe_challenge_never_reaches_the_jvm(self):
        # ?challenge= is echoed into deep_probe, which answers "unsupported"
        # for anything outside DEEP_PROBE_CHALLENGE_IDS — so no grading slot is
        # taken and no reference circuit is graded by this request. (The
        # admins_only gate on this variant is a no-op under the stubs; it is
        # checked in AdminGateTests below.)
        for query in ("challenge=999", "challenge=not-a-number"):
            with self.subTest(query=query):
                with plugin_app() as plugin:
                    response = plugin.client.get(
                        f"/api/v1/digital/health?deep=1&{query}"
                    )
                data = response.get_json()["data"]
                self.assertEqual(
                    data["deep_probe"]["status"], "unsupported_challenge"
                )
                # An unsupported id is a bad query parameter, not a grading
                # fault, so it must not turn the report red.
                self.assertNotIn("deep_probe", data["failed_checks"])


# One team, two challenges, one of them unsolved — enough to catch the header
# and the data row drifting apart, which is the failure a spreadsheet hides.
EXPORT_PAYLOAD = {
    "users": [
        {
            "id": 4,
            "name": "4조",
            "hidden": False,
            "banned": True,
            "score": 12,
            "solved": 1,
            "rounds": {
                "round1": {"score": 0, "solved": 0},
                "round2": {"score": 12, "solved": 1},
            },
            "challenges": [
                {
                    "id": 9,
                    "name": "반가산기",
                    "value": 6,
                    "round": "round2",
                    "solved": True,
                    "solve_count": 1,
                    "fail_count": 2,
                    "first_solve_at": "2026-08-01T05:00:00+00:00",
                    "last_attempt_at": "2026-08-01T05:00:00+00:00",
                    "solves": [
                        {
                            "at": "2026-08-01T05:00:00+00:00",
                            "ip": "10.0.0.4",
                            "provided": "1-1번_반가산기(Half Adder)만들기.dig",
                        }
                    ],
                    "fails": [],
                },
                {
                    "id": 15,
                    "name": "7-segment 출력기",
                    "value": 5,
                    "round": "round2",
                    "solved": False,
                    "solve_count": 0,
                    "fail_count": 1,
                    "first_solve_at": None,
                    "last_attempt_at": "2026-08-01T05:30:00+00:00",
                    "solves": [],
                    "fails": [
                        {
                            "at": "2026-08-01T05:30:00+00:00",
                            "ip": "10.0.0.4",
                            "provided": "3-2번_7-segment출력기.dig",
                        }
                    ],
                },
            ],
        }
    ]
}


class ExportCsvTests(unittest.TestCase):
    """The flattening under /export?format=csv. The route body around it needs
    a database and is not reached from here."""

    def render(self):
        generated_at = datetime.datetime(
            2026, 8, 1, 6, 30, 0, tzinfo=datetime.timezone.utc
        )
        with plugin_app() as plugin:
            response = plugin.endpoints._export_csv(EXPORT_PAYLOAD, generated_at)
        return response, response.get_data(as_text=True)

    def test_the_file_opens_in_excel_as_utf8(self):
        response, text = self.render()
        # Without the BOM Excel reads the file in the local codepage and every
        # Korean team name becomes mojibake.
        self.assertTrue(text.startswith("﻿"))
        self.assertIn("charset=utf-8", response.content_type)
        self.assertIn(
            'filename="econ-judge-export-20260801T063000Z.csv"',
            response.headers["Content-Disposition"],
        )

    def test_one_row_per_team_and_challenge_with_the_header_width(self):
        _, text = self.render()
        rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
        header, *data_rows = rows
        self.assertEqual(len(data_rows), 2)
        for row in data_rows:
            with self.subTest(challenge=row[header.index("challenge_id")]):
                self.assertEqual(len(row), len(header))

        by_challenge = {row[header.index("challenge_id")]: row for row in data_rows}
        solved = by_challenge["9"]
        unsolved = by_challenge["15"]

        def field(row, name):
            return row[header.index(name)]

        self.assertEqual(field(solved, "team_name"), "4조")
        self.assertEqual(field(solved, "first_solve_ip"), "10.0.0.4")
        self.assertEqual(field(solved, "banned"), "1")
        # A team with no solve on a challenge must not inherit another row's IP.
        self.assertEqual(field(unsolved, "first_solve_ip"), "")
        self.assertEqual(field(unsolved, "first_solve_at"), "")
        self.assertEqual(field(unsolved, "last_attempt_at"), "2026-08-01T05:30:00+00:00")
        # Team totals repeat on every row so the file pivots without a join.
        for name in ("total_score", "round1_score", "round2_solved"):
            with self.subTest(column=name):
                self.assertEqual(field(solved, name), field(unsolved, name))

    def test_the_header_names_a_column_for_every_round(self):
        _, text = self.render()
        header = next(csv.reader(io.StringIO(text.lstrip("﻿"))))
        with plugin_app() as plugin:
            round_keys = list(plugin.endpoints.ROUND_INFO)
        for key in round_keys:
            with self.subTest(round=key):
                self.assertIn(f"{key}_score", header)
                self.assertIn(f"{key}_solved", header)


class AdminGateTests(unittest.TestCase):
    """`admins_only` is stubbed to a no-op, so the gate is read from the source.

    /export returns real team names AND the submission IPs behind them; the
    verbose health report returns server paths and the grading budget; the deep
    probe returns both of those plus raw Digital output. All three would be a
    scrape target if the decorator were dropped.
    """

    def decorator_names(self, function_name):
        source = (ROOT / "econ_judge" / "endpoints.py").read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == function_name
            ):
                return {
                    decorator.id
                    for decorator in node.decorator_list
                    if isinstance(decorator, ast.Name)
                }
        raise AssertionError(f"endpoints.py no longer defines {function_name}")

    def test_the_views_that_expose_team_or_server_data_are_admin_only(self):
        for name in (
            "digital_export",
            "_verbose_health",
            "_deep_health",
            "digital_projector",
        ):
            with self.subTest(view=name):
                self.assertIn("admins_only", self.decorator_names(name))

    def test_the_structural_health_check_stays_public(self):
        # A host health check polls this without credentials; gating it would
        # make the probe useless. What keeps that safe is not this assertion
        # but the payload it answers with — see HealthRouteTests.
        self.assertEqual(self.decorator_names("digital_health"), set())


if __name__ == "__main__":
    unittest.main()
