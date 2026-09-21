"""Keep tests/deploy_smoke.py's SAMPLES table honest without a live server.

deploy_smoke.py only runs against a deployment, so its constants went stale
unnoticed: the paths still pointed at tests/samples/5jo-26winter after commit
39ee2ae moved the directory to tests/5jo-26winter, and the script died on
FileNotFoundError before its first request. These checks read the table out of
the script and confirm offline that each sample is where it is looked for and
that its pins are the ones the challenge it is posted to is graded on.
"""

from __future__ import annotations

import ast
import importlib.util
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEPLOY_SMOKE = ROOT / "tests" / "deploy_smoke.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generator = load("smoke_generator", ROOT / "tests" / "generate_secret_tests.py")
problemset = load("smoke_problemset", ROOT / "econ_judge" / "problemset.py")


def constant(name: str):
    """One module-level assignment of deploy_smoke.py, unevaluated.

    The script is read rather than imported: importing it needs `requests` and
    runs its .env loader, which would push the camp credentials in .env into
    the environment of every other test in the process.
    """
    tree = ast.parse(DEPLOY_SMOKE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return node.value
    raise AssertionError(f"deploy_smoke.py no longer defines {name}")


SAMPLES = ast.literal_eval(constant("SAMPLES"))
# SAMPLES_ROOT is an os.path.join of REPO_ROOT and the directory parts.
SAMPLES_ROOT = ROOT.joinpath(
    *(
        node.value
        for node in ast.walk(constant("SAMPLES_ROOT"))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
)


def pin_labels(path: Path) -> set:
    """The In/Out labels of a circuit — the names a Testcase column binds to."""
    labels = set()
    for element in ET.parse(path).iter("visualElement"):
        if (element.findtext("elementName") or "").strip() not in ("In", "Out"):
            continue
        for entry in element.iter("entry"):
            strings = entry.findall("string")
            if len(strings) == 2 and strings[0].text == "Label":
                labels.add(strings[1].text)
    return labels


class SampleCircuitTests(unittest.TestCase):
    def test_every_sample_is_where_the_script_looks_for_it(self):
        self.assertTrue(SAMPLES_ROOT.is_dir(), SAMPLES_ROOT)
        for challenge_id, relative_path in sorted(SAMPLES.items()):
            with self.subTest(challenge_id=challenge_id):
                self.assertTrue((SAMPLES_ROOT / relative_path).is_file(), relative_path)

    def test_every_sample_has_the_pins_of_the_challenge_it_is_posted_to(self):
        for challenge_id, relative_path in sorted(SAMPLES.items()):
            with self.subTest(challenge_id=challenge_id):
                spec = generator.SPECS[challenge_id]
                self.assertEqual(
                    pin_labels(SAMPLES_ROOT / relative_path),
                    set(spec["in_pins"]) | set(spec["out_pins"]),
                )

    def test_the_script_posts_each_sample_under_the_answer_filename(self):
        # The endpoint reads request.files.getlist("files") and matches one
        # part against this challenge's answer filename; a single "file" part
        # carrying a bare basename is rejected before grading.
        source = DEPLOY_SMOKE.read_text(encoding="utf-8")
        self.assertIn("problemset.HWP_STARTER_FILES[cid]", source)
        self.assertIn('files=[("files"', source)
        for challenge_id in sorted(SAMPLES):
            with self.subTest(challenge_id=challenge_id):
                self.assertIn(challenge_id, problemset.HWP_STARTER_FILES)


if __name__ == "__main__":
    unittest.main()
