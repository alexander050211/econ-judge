"""Focused tests for the participant-facing problem page route.

The repository does not vendor CTFd, so these tests stub only the CTFd modules
that problems.py imports. Flask still registers and dispatches the real
blueprint, which catches route, status, context, and template-loader mistakes.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

from flask import Flask, abort


REPO_ROOT = Path(__file__).resolve().parent.parent


class FakeQuery:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter_by(self, **filters):
        return FakeQuery(
            row
            for row in self.rows
            if all(getattr(row, key, None) == value for key, value in filters.items())
        )

    def all(self):
        return list(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None

    def first_or_404(self):
        if not self.rows:
            abort(404)
        return self.rows[0]

    def count(self):
        return len(self.rows)


class Challenges:
    query = FakeQuery([])


class Solves:
    query = FakeQuery([])


class Fails:
    query = FakeQuery([])


CURRENT_USER = SimpleNamespace(id=7, type="user")


def load_problem_module():
    ctfd = types.ModuleType("CTFd")
    econ_judge = types.ModuleType("econ_judge")
    econ_judge.__path__ = [str(REPO_ROOT / "econ_judge")]
    models = types.ModuleType("CTFd.models")
    models.Challenges = Challenges
    models.Solves = Solves
    models.Fails = Fails

    utils = types.ModuleType("CTFd.utils")
    config = types.ModuleType("CTFd.utils.config")
    pages = types.ModuleType("CTFd.utils.config.pages")
    pages.build_markdown = lambda text, sanitize=False: f"<p>{text}</p>"
    decorators = types.ModuleType("CTFd.utils.decorators")
    decorators.authed_only = lambda function: function
    user = types.ModuleType("CTFd.utils.user")
    user.get_current_user = lambda: CURRENT_USER

    modules = {
        "econ_judge": econ_judge,
        "CTFd": ctfd,
        "CTFd.models": models,
        "CTFd.utils": utils,
        "CTFd.utils.config": config,
        "CTFd.utils.config.pages": pages,
        "CTFd.utils.decorators": decorators,
        "CTFd.utils.user": user,
    }
    previous = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        spec = importlib.util.spec_from_file_location(
            "econ_judge.problems",
            REPO_ROOT / "econ_judge" / "problems.py",
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(spec.name, None)
        return module
    finally:
        for name, old_module in previous.items():
            if old_module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old_module


def challenge(challenge_id, category, *, state="visible", challenge_type="digital"):
    return SimpleNamespace(
        id=challenge_id,
        category=category,
        state=state,
        type=challenge_type,
        name=f"Problem {challenge_id}",
        description="Description",
        value=3,
    )


class ProblemPageTests(unittest.TestCase):
    def setUp(self):
        global CURRENT_USER
        CURRENT_USER = SimpleNamespace(id=7, type="user")
        self.rows = [
            challenge(1, "연습"),
            challenge(5, "연습"),
            challenge(2, "미션"),
            challenge(3, "프로젝트"),
            challenge(4, "프로젝트"),
        ]
        Challenges.query = FakeQuery(self.rows)
        Solves.query = FakeQuery([])
        Fails.query = FakeQuery([])
        self.module = load_problem_module()

    def make_app(self):
        app = Flask(__name__)
        app.testing = True
        self.module.register_problem_pages(app)
        return app

    def test_neighbors_follow_category_order_then_id(self):
        self.assertEqual(self.module._problem_neighbors(5), (1, 2))
        self.assertEqual(self.module._problem_neighbors(1), (None, 5))
        self.assertEqual(self.module._problem_neighbors(4), (3, None))

    def test_route_registers_template_loader_and_context(self):
        Solves.query = FakeQuery([SimpleNamespace(user_id=7, challenge_id=5)])
        Fails.query = FakeQuery(
            [
                SimpleNamespace(user_id=7, challenge_id=5),
                SimpleNamespace(user_id=7, challenge_id=5),
            ]
        )
        captured = {}

        def fake_render(template_name, **context):
            captured["template"] = template_name
            captured["context"] = context
            return "rendered"

        self.module.render_template = fake_render
        app = self.make_app()

        with app.test_client() as client:
            response = client.get("/problems/5")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["template"], "problems/view.html")
        self.assertTrue(captured["context"]["solved"])
        self.assertEqual(captured["context"]["record_count"], 3)
        self.assertEqual(captured["context"]["previous_id"], 1)
        self.assertEqual(captured["context"]["next_id"], 2)
        self.assertFalse(captured["context"]["truth_table_mode"])
        self.assertFalse(captured["context"]["attempt_locked"])
        self.assertEqual(
            captured["context"]["starter_filename"],
            "05_nand_not.dig",
        )
        self.assertIn("problems/view.html", app.jinja_env.list_templates())

    def test_truth_table_problem_is_locked_after_first_record(self):
        Fails.query = FakeQuery([SimpleNamespace(user_id=7, challenge_id=1)])
        captured = {}

        def fake_render(template_name, **context):
            captured.update(context)
            return "rendered"

        self.module.render_template = fake_render
        app = self.make_app()

        with app.test_client() as client:
            response = client.get("/problems/1")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(captured["truth_table_mode"])
        self.assertTrue(captured["attempt_locked"])
        self.assertIsNone(captured["starter_filename"])

    def test_hidden_and_non_digital_challenges_are_not_participant_pages(self):
        Challenges.query = FakeQuery(
            [
                challenge(20, "연습", state="hidden"),
                challenge(21, "연습", challenge_type="standard"),
            ]
        )
        app = self.make_app()

        with app.test_client() as client:
            self.assertEqual(client.get("/problems/20").status_code, 404)
            self.assertEqual(client.get("/problems/21").status_code, 404)
            self.assertEqual(client.get("/problems/999").status_code, 404)

    def test_submission_forwards_ctfd_csrf_nonce(self):
        source = (REPO_ROOT / "econ_judge" / "assets" / "view.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("window.init.csrfNonce", source)
        self.assertIn('fd.append("nonce", nonce)', source)
        self.assertIn('headers: nonce ? { "CSRF-Token": nonce } : {}', source)

    def test_detailed_grading_feedback_is_dormant(self):
        source = (REPO_ROOT / "econ_judge" / "assets" / "view.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("const DETAILED_GRADING_FEEDBACK = false;", source)
        self.assertIn("if (!DETAILED_GRADING_FEEDBACK) return false;", source)
        self.assertIn("const hints = DETAILED_GRADING_FEEDBACK", source)


if __name__ == "__main__":
    unittest.main()
