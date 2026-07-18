"""Consistency checks for the 2026 summer problem-set configuration."""

from __future__ import annotations

import importlib.util
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


register = load("summer_register", ROOT / "tests" / "register_challenges.py")
generator = load("summer_generator", ROOT / "tests" / "generate_secret_tests.py")
problemset = load("summer_problemset", ROOT / "econ_judge" / "problemset.py")


class ProblemSetTests(unittest.TestCase):
    def test_ids_points_categories_and_rows(self):
        self.assertEqual([row[0] for row in register.CHALLENGES], list(range(1, 16)))
        self.assertEqual(sum(row[3] for row in register.CHALLENGES), 82)
        self.assertEqual(
            {category for _, _, category, *_ in register.CHALLENGES},
            {"연습", "미션", "프로젝트"},
        )

        expected_rows = {row[0]: row[5] for row in register.CHALLENGES}
        self.assertEqual(expected_rows[1], 8)
        for challenge_id, spec in generator.SPECS.items():
            test_file = ROOT / "secret_tests" / f"{challenge_id}.dig"
            self.assertTrue(test_file.exists(), test_file)
            count = len(ET.parse(test_file).findall(".//visualElement"))
            self.assertEqual(count, expected_rows[challenge_id])

    def test_only_circuit_challenges_have_digital_test_files(self):
        files = {int(path.stem) for path in (ROOT / "secret_tests").glob("*.dig")}
        self.assertEqual(files, set(range(2, 16)))

    def test_previous_circuit_reuse_is_optional(self):
        descriptions = {row[0]: row[4] for row in register.CHALLENGES}
        for challenge_id in (8, 9, 11, 13):
            with self.subTest(challenge_id=challenge_id):
                self.assertIn("이전 회로의 사용은 선택입니다.", descriptions[challenge_id])

    def test_key_truth_functions(self):
        self.assertEqual(problemset.TRUTH_TABLE_EXPECTED, (0, 0, 0, 1, 0, 1, 0, 1))
        self.assertEqual(
            problemset.normalize_truth_table_answers(list(problemset.TRUTH_TABLE_EXPECTED)),
            problemset.TRUTH_TABLE_EXPECTED,
        )
        self.assertIsNone(problemset.normalize_truth_table_answers([False] * 8))
        self.assertEqual(generator.abc_identity([1, 1, 1]), [1, 1, 1])
        self.assertEqual(generator.abc_identity([1, 0, 0]), [1, 0, 0])
        self.assertEqual(generator.flood_warning([0, 1, 0, 0, 1, 1]), [1])
        self.assertEqual(generator.flood_risk([1, 1, 1, 1, 0, 0]), [1])
        self.assertEqual(generator.flood_risk([1, 0, 1, 1, 0, 1]), [1])
        self.assertEqual(generator.flood_risk([1, 0, 1, 0, 1, 1]), [0])
        self.assertEqual(generator.seven_segment_yn([1]), [0, 1, 1, 1, 0, 1, 1, 0])
        self.assertEqual(generator.seven_segment_yn([0]), [1, 1, 1, 0, 1, 1, 0, 0])


if __name__ == "__main__":
    unittest.main()
