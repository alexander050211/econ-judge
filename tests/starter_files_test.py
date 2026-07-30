"""Confirm participant pages reference final-HWP files without publishing them."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_problemset():
    spec = importlib.util.spec_from_file_location(
        "problemset_for_starters", REPO_ROOT / "econ_judge" / "problemset.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


class StarterFileTests(unittest.TestCase):
    def test_hwp_manifest_covers_every_circuit_challenge(self):
        problemset = _load_problemset()
        self.assertEqual(set(problemset.HWP_STARTER_FILES), set(range(2, 16)))
        self.assertTrue(all(name.endswith(".dig") for name in problemset.HWP_STARTER_FILES.values()))

    def test_problem_template_uses_round_folder_submission_without_mojibake(self):
        source = (REPO_ROOT / "econ_judge" / "templates" / "problems" / "view.html").read_text(encoding="utf-8")
        self.assertNotIn("???", source)
        self.assertIn("webkitdirectory", source)
        self.assertIn("data-answer-filename", source)
    def test_problem_template_does_not_publish_hwp_working_files(self):
        source = (REPO_ROOT / "econ_judge" / "templates" / "problems" / "view.html").read_text(encoding="utf-8")
        self.assertIn("hwp_starter_filename", source)
        self.assertNotIn("assets/starters/{{ starter_filename }}", source)


if __name__ == "__main__":
    unittest.main()
