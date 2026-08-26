"""Load the plugin the way CTFd does, with only CTFd itself stubbed out.

The repository does not vendor CTFd, so nothing here could import
``econ_judge`` until now — a missing ``import shutil`` in grader.py and a call
to a helper that had been renamed out of endpoints.py both survived review
because no test ever executed those files together. These tests stub the small
CTFd surface the plugin imports, then run ``load(app)`` end to end and read the
modules back with ``ast``.

What they do NOT cover: anything that talks to the database. The stubs make no
attempt to emulate SQLAlchemy query results, so a route body's SQL is only
checked for names that resolve, not for what it would return.
"""

from __future__ import annotations

import ast
import builtins
import contextlib
import importlib
import io
import sys
import tempfile
import types
import unicodedata
import unittest
from pathlib import Path, PurePosixPath

from flask import Flask


REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "econ_judge"


def _stub_modules() -> dict:
    """The CTFd (and SQLAlchemy) names the plugin binds at import time.

    SQLAlchemy is stubbed as well even where it is installed: it is not in
    requirements-dev.txt, so CI has to be able to import endpoints.py without
    it, and only route bodies ever touch ``func``.
    """
    ctfd = types.ModuleType("CTFd")

    models = types.ModuleType("CTFd.models")
    for name in ("Challenges", "Fails", "Solves", "Users"):
        setattr(models, name, type(name, (), {}))
    models.db = types.SimpleNamespace(session=None)

    plugins = types.ModuleType("CTFd.plugins")
    plugins.bypass_csrf_protection = lambda function: function
    plugins.override_template = lambda name, content: None
    plugins.register_plugin_assets_directory = lambda app, **kwargs: None
    challenges = types.ModuleType("CTFd.plugins.challenges")
    challenges.BaseChallenge = type("BaseChallenge", (), {})
    challenges.CHALLENGE_CLASSES = {}

    utils = types.ModuleType("CTFd.utils")
    utils.get_config = lambda key: None
    config = types.ModuleType("CTFd.utils.config")
    pages = types.ModuleType("CTFd.utils.config.pages")
    pages.build_markdown = lambda text, sanitize=False: f"<p>{text}</p>"
    decorators = types.ModuleType("CTFd.utils.decorators")
    decorators.admins_only = lambda function: function
    decorators.authed_only = lambda function: function
    user = types.ModuleType("CTFd.utils.user")
    user.get_current_user = lambda: None
    user.get_current_team = lambda: None
    user.get_ip = lambda request: "127.0.0.1"

    sqlalchemy = types.ModuleType("sqlalchemy")
    sqlalchemy.func = types.SimpleNamespace()

    return {
        "CTFd": ctfd,
        "CTFd.models": models,
        "CTFd.plugins": plugins,
        "CTFd.plugins.challenges": challenges,
        "CTFd.utils": utils,
        "CTFd.utils.config": config,
        "CTFd.utils.config.pages": pages,
        "CTFd.utils.decorators": decorators,
        "CTFd.utils.user": user,
        "sqlalchemy": sqlalchemy,
    }


@contextlib.contextmanager
def ctfd_stubs():
    """Make ``import econ_judge...`` work, and undo it afterwards.

    The real package is imported, not a fake one, so relative imports and the
    template/asset paths inside it are the ones that ship.
    """
    modules = _stub_modules()
    previous = {name: sys.modules.get(name) for name in modules}
    already_loaded = set(sys.modules)
    sys.modules.update(modules)
    on_path = str(REPO_ROOT) in sys.path
    if not on_path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        yield
    finally:
        if not on_path:
            sys.path.remove(str(REPO_ROOT))
        for name in set(sys.modules) - already_loaded:
            if name == "econ_judge" or name.startswith("econ_judge."):
                sys.modules.pop(name, None)
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def plugin_modules() -> list:
    return sorted(path.stem for path in PLUGIN_DIR.glob("*.py"))


def import_name(stem: str) -> str:
    return "econ_judge" if stem == "__init__" else f"econ_judge.{stem}"


def undefined_names(source: str) -> set:
    """Names a module reads but binds nowhere, and that are not builtins.

    Scopes are deliberately ignored: every binding anywhere in the file counts
    as visible everywhere in it. That over-approximation is what keeps the
    check free of false positives while still catching the two cases that
    matter — a module that uses a library it never imported, and a call to a
    helper that no longer exists.
    """
    tree = ast.parse(source)
    bound = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id not in bound
    }


class PluginImportTests(unittest.TestCase):
    def test_every_plugin_module_imports_with_ctfd_stubbed(self):
        stems = plugin_modules()
        self.assertIn("endpoints", stems)
        with ctfd_stubs():
            for stem in stems:
                with self.subTest(module=stem):
                    importlib.import_module(import_name(stem))

    # Every @app.route in endpoints.py plus the problem-page blueprint. A route
    # dropped by an edit elsewhere is otherwise silent: nothing 404s until
    # someone opens the page, and two of these are operator-only URLs nobody
    # loads until they are needed — /health is what render.yaml's
    # healthCheckPath points at, and /export is the teardown checklist's
    # fastest route to the contest record before the free tier wipes it.
    ROUTES = (
        "/problems/<int:challenge_id>",
        "/api/v1/digital/competition",
        "/api/v1/digital/challenges/<int:challenge_id>/attempt",
        "/api/v1/digital/challenges/<int:challenge_id>/truth-table-attempt",
        "/api/v1/digital/my-score",
        "/api/v1/digital/projector",
        "/api/v1/digital/health",
        "/api/v1/digital/export",
    )

    def registered_rules(self):
        with ctfd_stubs():
            plugin = importlib.import_module("econ_judge")
            app = Flask(__name__)
            plugin.load(app)
            return {rule.rule for rule in app.url_map.iter_rules()}

    def test_load_registers_the_participant_and_api_routes(self):
        rules = self.registered_rules()
        for rule in self.ROUTES:
            with self.subTest(rule=rule):
                self.assertIn(rule, rules)

    def test_no_api_route_is_registered_that_this_list_does_not_name(self):
        # The other half: a new /api/v1/digital/* route has to be added above,
        # which is where its gate gets thought about (see
        # tests/health_export_test.py's AdminGateTests).
        registered = {
            rule
            for rule in self.registered_rules()
            if rule.startswith("/api/v1/digital/")
        }
        self.assertEqual(registered, {r for r in self.ROUTES if r.startswith("/api")})


class UndefinedNameTests(unittest.TestCase):
    def test_no_plugin_module_reads_a_name_it_never_binds(self):
        for stem in plugin_modules():
            with self.subTest(module=stem):
                source = (PLUGIN_DIR / f"{stem}.py").read_text(encoding="utf-8")
                self.assertEqual(undefined_names(source), set())

    def test_the_check_reports_a_missing_import_and_a_missing_helper(self):
        # Without this the test above would still pass if `undefined_names`
        # ever stopped finding anything.
        self.assertEqual(
            undefined_names("def seed(path):\n    shutil.copy(path, path)\n"),
            {"shutil"},
        )
        self.assertEqual(
            undefined_names("def view():\n    return _user_score_and_solved(1)\n"),
            {"_user_score_and_solved"},
        )


class UploadContractTests(unittest.TestCase):
    """The shape tests/deploy_smoke.py has to post, pinned at the endpoint."""

    def stage(self, challenge_id, data):
        with ctfd_stubs():
            endpoints = importlib.import_module("econ_judge.endpoints")
            app = Flask(__name__)
            with tempfile.TemporaryDirectory() as tmp:
                with app.test_request_context("/", method="POST", data=data):
                    return endpoints._stage_digital_bundle(challenge_id, tmp)

    def stage_into(self, challenge_id, data, working_dir):
        """As `stage`, but into a directory the caller owns, so what actually
        landed on disk can be read back."""
        with ctfd_stubs():
            endpoints = importlib.import_module("econ_judge.endpoints")
            app = Flask(__name__)
            with app.test_request_context("/", method="POST", data=data):
                return endpoints._stage_digital_bundle(challenge_id, working_dir)

    def starter_files(self):
        with ctfd_stubs():
            problemset = importlib.import_module("econ_judge.problemset")
            return dict(problemset.HWP_STARTER_FILES)

    def test_manifest_path_under_files_is_accepted_for_every_challenge(self):
        for challenge_id, starter in sorted(self.starter_files().items()):
            with self.subTest(challenge_id=challenge_id):
                path, dependencies, provided, error = self.stage(
                    challenge_id,
                    {"files": [(io.BytesIO(b"<circuit/>"), starter)]},
                )
                self.assertIsNone(error)
                self.assertEqual(provided, PurePosixPath(starter).name)
                self.assertEqual(Path(path).name, PurePosixPath(starter).name)
                self.assertEqual(dependencies, ())

    def test_single_file_field_with_a_bare_basename_is_rejected(self):
        # The shape deploy_smoke.py posted before the round-folder contract:
        # one part named "file", carrying no folder prefix.
        starter = self.starter_files()[3]
        _, _, _, error = self.stage(
            3, {"file": (io.BytesIO(b"<circuit/>"), PurePosixPath(starter).name)}
        )
        self.assertIsNotNone(error)

        _, _, _, error = self.stage(
            3, {"files": [(io.BytesIO(b"<circuit/>"), PurePosixPath(starter).name)]}
        )
        self.assertIsNotNone(error)

    def test_folder_without_this_challenges_answer_file_is_rejected(self):
        starter = self.starter_files()[9]
        folder = PurePosixPath(starter).parent
        _, _, _, error = self.stage(
            9, {"files": [(io.BytesIO(b"<circuit/>"), f"{folder}/다른파일.dig")]}
        )
        self.assertIsNotNone(error)

    def reject_reason(self, challenge_id, upload_name):
        """(staging error, names left in the grading directory) for one part."""
        with tempfile.TemporaryDirectory() as tmp:
            _, _, _, error = self.stage_into(
                challenge_id,
                {"files": [(io.BytesIO(b"<circuit/>"), upload_name)]},
                tmp,
            )
            return error, sorted(entry.name for entry in Path(tmp).iterdir())

    def test_a_name_that_is_not_one_path_component_is_rejected(self):
        # Only `relative_path.name` is ever joined onto the grading directory,
        # and `.name` is a POSIX-only split: on Windows os.path.join(tmp,
        # "D:evil.dig") returns "D:evil.dig", which is not inside tmp at all.
        folder = PurePosixPath(self.starter_files()[9]).parent
        # A name the guard lets through is staged, and only then turned down
        # for not being this challenge's answer file. Comparing against that
        # message is what tells "refused as a filename" apart from "refused as
        # a folder" — the distinction the guard exists to make.
        past_the_guard, staged = self.reject_reason(9, f"{folder}/other.dig")
        self.assertIsNotNone(past_the_guard)
        self.assertEqual(staged, ["other.dig"])

        reasons = set()
        for name in ("D:evil.dig", ".."):
            with self.subTest(filename=name):
                reason, staged = self.reject_reason(9, f"{folder}/{name}")
                self.assertIsNotNone(reason)
                self.assertNotEqual(reason, past_the_guard)
                self.assertEqual(staged, [])
                reasons.add(reason)
        self.assertEqual(len(reasons), 1)

    def test_a_decomposed_hangul_filename_still_finds_the_answer_file(self):
        # macOS sends Hangul filenames NFD-decomposed in multipart parts while
        # HWP_STARTER_FILES is composed. Without the fold to NFC the endpoint
        # tells a team its own answer file is missing from the folder it just
        # selected — and nothing else in this suite would notice.
        starter = self.starter_files()[9]
        composed = PurePosixPath(starter).name
        decomposed = unicodedata.normalize("NFD", starter)
        self.assertFalse(unicodedata.is_normalized("NFC", decomposed))
        # Escaped rather than literal: the syllable 번 of the starter
        # name decomposes to these three jamo, and an editor that re-composed
        # a literal here would quietly turn this assertion into a no-op.
        self.assertIn("\u1107\u1165\u11ab", decomposed)
        self.assertEqual(unicodedata.normalize("NFC", decomposed), starter)

        with tempfile.TemporaryDirectory() as tmp:
            path, dependencies, provided, error = self.stage_into(
                9, {"files": [(io.BytesIO(b"<circuit/>"), decomposed)]}, tmp
            )
            self.assertIsNone(error)
            self.assertEqual(provided, composed)
            self.assertEqual(Path(path).name, composed)
            self.assertEqual(dependencies, ())
            staged = {entry.name for entry in Path(tmp).iterdir()}
        # A .dig authored on that machine names its sibling components in the
        # same decomposed form, and Digital resolves a component by exact
        # filename, so both spellings have to exist in the grading directory.
        self.assertEqual(staged, {composed, PurePosixPath(decomposed).name})

    def test_a_decomposed_answer_file_keeps_its_siblings_as_dependencies(self):
        starters = self.starter_files()
        answer, sibling = starters[10], starters[9]
        with tempfile.TemporaryDirectory() as tmp:
            path, dependencies, _, error = self.stage_into(
                10,
                {
                    "files": [
                        (
                            io.BytesIO(b"<circuit/>"),
                            unicodedata.normalize("NFD", answer),
                        ),
                        (io.BytesIO(b"<circuit/>"), sibling),
                    ]
                },
                tmp,
            )
            self.assertIsNone(error)
            self.assertEqual(Path(path).name, PurePosixPath(answer).name)
            self.assertEqual(
                [Path(dependency).name for dependency in dependencies],
                [PurePosixPath(sibling).name],
            )

    def test_one_filename_in_both_spellings_is_rejected_as_a_duplicate(self):
        # Folded, these are the same name: accepting both would leave which
        # upload wins up to the order the browser happened to send them in.
        starter = self.starter_files()[9]
        with tempfile.TemporaryDirectory() as tmp:
            _, _, _, error = self.stage_into(
                9,
                {
                    "files": [
                        (io.BytesIO(b"<circuit/>"), starter),
                        (
                            io.BytesIO(b"<circuit/>"),
                            unicodedata.normalize("NFD", starter),
                        ),
                    ]
                },
                tmp,
            )
        self.assertIsNotNone(error)


if __name__ == "__main__":
    unittest.main()
