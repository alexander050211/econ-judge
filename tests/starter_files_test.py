"""Validate the public Digital starter files and their page integration."""

from __future__ import annotations

import importlib.util
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
STARTER_DIR = REPO_ROOT / "econ_judge" / "assets" / "starters"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


generator = _load_module(
    "generate_starters",
    REPO_ROOT / "tests" / "generate_starters.py",
)
problemset = _load_module(
    "problemset_for_starters",
    REPO_ROOT / "econ_judge" / "problemset.py",
)


def _label(visual) -> str | None:
    entries = visual.findall("./elementAttributes/entry")
    for entry in entries:
        strings = [node.text for node in entry.findall("string")]
        if len(strings) == 2 and strings[0] == "Label":
            return strings[1]
    return None


class StarterFileTests(unittest.TestCase):
    def test_manifest_covers_every_circuit_challenge(self):
        self.assertEqual(set(generator.STARTERS), set(range(2, 16)))
        self.assertEqual(
            problemset.STARTER_FILES,
            {
                challenge_id: starter.filename
                for challenge_id, starter in generator.STARTERS.items()
            },
        )

    def test_files_have_exact_public_components_and_no_wires(self):
        self.assertEqual(
            {path.name for path in STARTER_DIR.glob("*.dig")},
            {starter.filename for starter in generator.STARTERS.values()},
        )

        for challenge_id, starter in generator.STARTERS.items():
            with self.subTest(challenge_id=challenge_id):
                root = ET.parse(STARTER_DIR / starter.filename).getroot()
                visuals = root.findall("./visualElements/visualElement")
                names = [visual.findtext("elementName") for visual in visuals]
                inputs = [
                    _label(visual)
                    for visual in visuals
                    if visual.findtext("elementName") == "In"
                ]
                outputs = [
                    _label(visual)
                    for visual in visuals
                    if visual.findtext("elementName") == "Out"
                ]

                self.assertEqual(inputs, [item[0] for item in starter.inputs])
                self.assertEqual(outputs, [item[0] for item in starter.outputs])
                self.assertEqual(
                    names.count("Seven-Seg"),
                    int(starter.seven_segment is not None),
                )
                self.assertTrue(set(names) <= {"In", "Out", "Seven-Seg"})
                self.assertEqual(root.findall("./wires/wire"), [])

    def test_problem_template_links_the_starter_asset(self):
        source = (
            REPO_ROOT / "econ_judge" / "templates" / "problems" / "view.html"
        ).read_text(encoding="utf-8")
        self.assertIn("assets/starters/{{ starter_filename }}", source)
        self.assertIn('download="{{ starter_filename }}"', source)


if __name__ == "__main__":
    unittest.main()
