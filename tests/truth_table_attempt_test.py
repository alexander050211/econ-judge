"""The one-attempt truth-table route: what it answers, and in which order.

Challenge 1 is graded in the browser rather than by Digital, and a team gets
exactly ONE attempt at it — enforced by a check-then-insert that runs under
``endpoints._truth_table_lock``. Two things about that section are easy to
break by accident and impossible to notice from the outside:

* WHAT RUNS UNDER THE LOCK. ``request.get_json()`` reads from a streaming
  wsgi.input, so a client that stalls between headers and body parks its
  greenlet mid-read while still holding the lock — one sleeping laptop would
  freeze this challenge for every other team. The body is therefore parsed
  before the ``with``.
* THE ORDER THE ANSWERS COME BACK IN. Moving the parse out could easily have
  taken the 0/1 validation with it, and then a team whose one attempt is
  already spent would be told to fix their rows instead of that they are
  locked out — sending them back to re-submit something that can never be
  recorded. The validation stays under the lock, behind the locked check, and
  the cases below pin that.

CTFd is not vendored, so the route runs against tests/plugin_surface_test.py's
stubs with the four models it touches replaced by in-memory stand-ins. Only
this one route is exercised; nothing here says anything about the rest of
endpoints.py.
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import importlib.util
import json
import unittest
from pathlib import Path

from flask import Flask, abort


ROOT = Path(__file__).resolve().parent.parent
ENDPOINTS_SOURCE = (ROOT / "econ_judge" / "endpoints.py").read_text(encoding="utf-8")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Only the stub context manager is taken from it, the same way
# tests/health_export_test.py does; loading by path keeps this file runnable
# on its own.
ctfd_stubs = load("truth_table_surface", ROOT / "tests" / "plugin_surface_test.py").ctfd_stubs
problemset = load("truth_table_problemset", ROOT / "econ_judge" / "problemset.py")

CHALLENGE_ID = problemset.TRUTH_TABLE_CHALLENGE_ID
CORRECT = list(problemset.TRUTH_TABLE_EXPECTED)
WRONG = [1 - answer for answer in CORRECT]
# Right length, values outside {0, 1}: what normalize_truth_table_answers
# turns down, and the only way to reach the "select 0 or 1" branch.
MALFORMED = [2] * len(CORRECT)


class FakeQuery:
    """The three methods this route calls, over a list of rows."""

    def __init__(self, rows):
        self.rows = list(rows)

    def filter_by(self, **filters):
        return FakeQuery(
            row
            for row in self.rows
            if all(getattr(row, key, None) == value for key, value in filters.items())
        )

    def first(self):
        return self.rows[0] if self.rows else None

    def first_or_404(self):
        if not self.rows:
            abort(404)
        return self.rows[0]


class _LiveQuery:
    """`Model.query`, re-read on every access so a commit is visible to the
    next request rather than to a list captured at import."""

    def __get__(self, instance, owner):
        return FakeQuery(owner.TABLE)


class Row:
    def __init__(self, **fields):
        self.__dict__.update(fields)


def model(name):
    return type(name, (Row,), {"TABLE": [], "query": _LiveQuery()})


class FakeSession:
    """Enough of db.session that a row only becomes visible on commit —
    the property the one-attempt guard depends on."""

    def __init__(self):
        self.pending = []
        self.commits = 0

    def add(self, row):
        self.pending.append(row)

    def commit(self):
        self.commits += 1
        for row in self.pending:
            type(row).TABLE.append(row)
        self.pending = []


@contextlib.contextmanager
def truth_table_app(*, challenge_type="digital"):
    """econ_judge's routes on a bare Flask app, with challenge 1 present.

    `register_endpoints` rather than `econ_judge.load`, for the reason
    tests/health_export_test.py gives: `load` also installs a before_request
    hook that queries Challenges, which the stubs do not emulate.
    """
    with ctfd_stubs():
        endpoints = importlib.import_module("econ_judge.endpoints")
        competition = importlib.import_module("econ_judge.competition")

        challenges = model("Challenges")
        solves = model("Solves")
        fails = model("Fails")
        session = FakeSession()
        user = Row(id=7, name="1조", type="user")

        challenges.TABLE.append(Row(id=CHALLENGE_ID, type=challenge_type))
        endpoints.Challenges = challenges
        endpoints.Solves = solves
        endpoints.Fails = fails
        endpoints.db.session = session
        endpoints.get_current_user = lambda: user
        endpoints.get_current_team = lambda: None
        # The real schedule is read from ECON_JUDGE_COMPETITION_START, so
        # without this the answers below would depend on when the suite runs.
        endpoints.current_phase = lambda now=None: competition.CompetitionPhase(
            "round1", competition.ROUND_1_CHALLENGE_IDS, True
        )

        app = Flask(__name__)
        endpoints.register_endpoints(app)
        yield SubmitClient(
            app.test_client(), endpoints, competition, solves, fails, session, user
        )


class SubmitClient:
    def __init__(self, client, endpoints, competition, solves, fails, session, user):
        self.client = client
        self.endpoints = endpoints
        self.competition = competition
        self.solves = solves
        self.fails = fails
        self.session = session
        self.user = user

    def close_the_window(self, message):
        self.endpoints.current_phase = (
            lambda now=None: self.competition.CompetitionPhase(
                "break", self.competition.ROUND_1_CHALLENGE_IDS, False, message
            )
        )

    def post(self, answers, challenge_id=CHALLENGE_ID):
        response = self.client.post(
            f"/api/v1/digital/challenges/{challenge_id}/truth-table-attempt",
            data=json.dumps({"answers": answers}),
            content_type="application/json",
        )
        return response, (response.get_json() or {}).get("data", {})

    def spend_the_attempt(self, model_class):
        """Put the row an earlier attempt would have left behind."""
        model_class.TABLE.append(
            Row(user_id=self.user.id, challenge_id=CHALLENGE_ID, provided="[]")
        )

    def recorded(self):
        return len(self.solves.TABLE), len(self.fails.TABLE)


class AttemptOrderingTests(unittest.TestCase):
    """Which of the two refusals a repeat submitter gets."""

    def test_a_first_correct_answer_is_recorded_as_a_solve(self):
        with truth_table_app() as api:
            _, data = api.post(CORRECT)
            self.assertEqual(data["status"], "correct")
            self.assertEqual(api.recorded(), (1, 0))
            self.assertEqual(
                json.loads(api.solves.TABLE[0].provided), CORRECT
            )

    def test_a_first_wrong_answer_is_recorded_as_a_fail(self):
        with truth_table_app() as api:
            _, data = api.post(WRONG)
            self.assertEqual(data["status"], "incorrect")
            self.assertEqual(api.recorded(), (0, 1))

    def test_a_malformed_first_answer_is_sent_back_without_spending_it(self):
        # The 0/1 message exists for exactly this case: nothing is recorded, so
        # the team still has its one attempt.
        with truth_table_app() as api:
            _, data = api.post(MALFORMED)
            self.assertEqual(data["status"], "incorrect")
            self.assertIn("0 또는 1", data["message"])
            self.assertEqual(api.recorded(), (0, 0))
            self.assertEqual(api.session.commits, 0)

    def test_a_repeat_submitter_is_locked_out(self):
        for spent_as in ("solves", "fails"):
            with self.subTest(previous_attempt=spent_as):
                with truth_table_app() as api:
                    api.spend_the_attempt(getattr(api, spent_as))
                    before = api.recorded()
                    _, data = api.post(CORRECT)
                    self.assertEqual(data["status"], "locked")
                    self.assertEqual(api.recorded(), before)

    def test_a_repeat_submitter_sending_malformed_answers_is_told_locked(self):
        # THE ORDERING PIN. Moving the body parse above the lock puts the
        # parsed `answers` in hand before the locked check runs, and answering
        # "select 0 or 1" here would tell a team whose attempt is already gone
        # to go and fix rows that can no longer be submitted. "locked"
        # outranks the validation message; changing that is a product
        # decision, not a refactor.
        for spent_as in ("solves", "fails"):
            with self.subTest(previous_attempt=spent_as):
                with truth_table_app() as api:
                    api.spend_the_attempt(getattr(api, spent_as))
                    _, data = api.post(MALFORMED)
                    self.assertEqual(data["status"], "locked")
                    self.assertNotIn("0 또는 1", data["message"])

    def test_the_second_attempt_after_a_real_first_one_is_locked(self):
        # Same guarantee reached through the route twice rather than through a
        # pre-seeded row, so the commit path is what makes the lock stick.
        with truth_table_app() as api:
            _, first = api.post(WRONG)
            self.assertEqual(first["status"], "incorrect")
            _, second = api.post(CORRECT)
            self.assertEqual(second["status"], "locked")
            self.assertEqual(api.recorded(), (0, 1))

    def test_a_closed_window_refuses_before_the_attempt_is_spent(self):
        # A correct answer submitted during the break must not quietly consume
        # the single attempt the team still has coming.
        with truth_table_app() as api:
            api.close_the_window("휴식 시간에는 제출할 수 없습니다.")
            _, data = api.post(CORRECT)
            self.assertEqual(data["status"], "unavailable")
            self.assertEqual(api.recorded(), (0, 0))

    def test_only_challenge_one_answers_this_route(self):
        with truth_table_app() as api:
            response, _ = api.post(CORRECT, challenge_id=9)
            self.assertEqual(response.status_code, 404)

    def test_a_challenge_that_is_not_a_digital_type_is_not_answerable(self):
        with truth_table_app(challenge_type="standard") as api:
            response, _ = api.post(CORRECT)
            self.assertEqual(response.status_code, 404)


class LockScopeTests(unittest.TestCase):
    """What may run while `_truth_table_lock` is held, read from the source.

    Not reachable from a request: the lock is uncontended in a test client, so
    a body parsed inside it would answer exactly the same as one parsed
    outside. The cost only shows up as a stalled contestant on camp day, which
    is why this is asserted structurally instead.
    """

    def setUp(self):
        self.function = self.route_function("truth_table_attempt")
        self.locked = [
            node
            for node in ast.walk(self.function)
            if isinstance(node, ast.With)
            and any(
                isinstance(item.context_expr, ast.Name)
                and item.context_expr.id == "_truth_table_lock"
                for item in node.items
            )
        ]

    def route_function(self, name):
        for node in ast.walk(ast.parse(ENDPOINTS_SOURCE)):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        raise AssertionError(f"endpoints.py no longer defines {name}()")

    def calls_in(self, tree):
        """Every call in `tree`, spelled the way the source writes it — a bare
        helper as its own name, an attribute call with its whole receiver
        chain ("request.get_json", "db.session.add")."""
        names = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            parts = []
            current = node.func
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
                names.add(".".join(reversed(parts)))
        return names

    def test_the_route_takes_the_lock_exactly_once(self):
        self.assertEqual(len(self.locked), 1)

    def test_nothing_client_paced_runs_under_the_lock(self):
        # get_json() pulls from a streaming wsgi.input; under gevent a client
        # that stops sending mid-body yields the greenlet and keeps the lock,
        # and gunicorn's --timeout does not cut an async worker's socket.
        under_lock = self.calls_in(self.locked[0])
        self.assertNotIn("request.get_json", under_lock)
        self.assertNotIn("normalize_truth_table_answers", under_lock)

    def test_the_body_is_parsed_before_the_lock_is_taken(self):
        # The counterpart to the assertion above: the parse has to still exist
        # somewhere, or that one would pass on a route that no longer reads a
        # body at all.
        whole_route = self.calls_in(self.function)
        self.assertIn("request.get_json", whole_route)
        self.assertIn("normalize_truth_table_answers", whole_route)

    def test_the_check_then_insert_stays_under_the_lock(self):
        # The reason the lock is there: two concurrent POSTs must not both pass
        # the "already attempted?" check and each insert a row.
        under_lock = self.calls_in(self.locked[0])
        for name in (
            "Solves.query.filter_by",
            "Fails.query.filter_by",
            "db.session.add",
            "db.session.commit",
        ):
            with self.subTest(call=name):
                self.assertIn(name, under_lock)


if __name__ == "__main__":
    unittest.main()
