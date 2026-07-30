"""Unit tests for circuit construction constraints that truth tables miss."""

from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "structure_grader", ROOT / "econ_judge" / "grader.py"
)
grader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(grader)


def circuit(*elements):
    body = []
    for name, inputs in elements:
        attributes = ""
        if inputs is not None:
            attributes = (
                "<elementAttributes><entry><string>Inputs</string>"
                f"<int>{inputs}</int></entry></elementAttributes>"
            )
        body.append(
            f"<visualElement><elementName>{name}</elementName>{attributes}"
            "<pos x=\"0\" y=\"0\"/></visualElement>"
        )
    return (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
        "<circuit><visualElements>" + "".join(body) +
        "</visualElements><wires/></circuit>"
    )


def seven_segment_circuit(*, swap_b_c=False, include_display=True):
    pin_positions = {
        "a": (100, 100), "b": (120, 100),
        "c": (140, 100), "d": (160, 100),
        "e": (100, 240), "f": (120, 240),
        "g": (140, 240), "dp": (160, 240),
    }
    wired_positions = {
        "a": (100, 20), "b": (120, 20),
        "c": (140, 20), "d": (160, 20),
        "e": (100, 320), "f": (120, 320),
        "g": (140, 320), "dp": (160, 320),
    }
    output_positions = dict(wired_positions)
    if swap_b_c:
        output_positions["b"], output_positions["c"] = (
            output_positions["c"], output_positions["b"]
        )

    elements = []
    if include_display:
        elements.append(
            "<visualElement><elementName>Seven-Seg</elementName>"
            "<elementAttributes/><pos x=\"100\" y=\"100\"/></visualElement>"
        )
    wires = []
    for label, output_position in output_positions.items():
        x, y = output_position
        elements.append(
            "<visualElement><elementName>Out</elementName><elementAttributes>"
            f"<entry><string>Label</string><string>{label}</string></entry>"
            f"</elementAttributes><pos x=\"{x}\" y=\"{y}\"/></visualElement>"
        )
        pin_x, pin_y = pin_positions[label]
        wired_x, wired_y = wired_positions[label]
        wires.append(
            f"<wire><p1 x=\"{pin_x}\" y=\"{pin_y}\"/>"
            f"<p2 x=\"{wired_x}\" y=\"{wired_y}\"/></wire>"
        )
    return (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?><circuit>"
        f"<visualElements>{''.join(elements)}</visualElements>"
        f"<wires>{''.join(wires)}</wires></circuit>"
    )


class StructureTests(unittest.TestCase):
    def validate(self, challenge_id, xml):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "submission.dig"
            path.write_text(xml, encoding="utf-8")
            return grader._validate_structure(challenge_id, str(path))

    def test_exact_nand_counts(self):
        valid_not = circuit(("In", None), ("Out", None), ("NAnd", 2))
        self.assertIsNone(self.validate(5, valid_not))
        self.assertIn("정확히 1개", self.validate(5, circuit(("In", None), ("Out", None))))

        valid_or = circuit(
            ("In", None), ("In", None), ("Out", None),
            ("NAnd", 2), ("NAnd", 2), ("NAnd", 2),
        )
        self.assertIsNone(self.validate(6, valid_or))

    def test_wrong_component_and_three_input_gate_are_rejected(self):
        self.assertIn(
            "NAND 게이트 외",
            self.validate(5, circuit(("In", None), ("Out", None), ("Not", None))),
        )
        self.assertIn(
            "입력이 2개보다 많은",
            self.validate(9, circuit(("In", None), ("Out", None), ("Or", 3))),
        )

    def test_flood_risk_rejects_three_input_gates(self):
        self.assertIn("2개보다 많은", self.validate(14, circuit(("Or", 3))))

    def test_three_input_and_problem_accepts_only_and_gates(self):
        valid = circuit(
            ("In", None), ("In", None), ("In", None), ("Out", None),
            ("And", 2), ("And", 2),
        )
        self.assertIsNone(self.validate(3, valid))
        self.assertIn(
            "AND 게이트만",
            self.validate(3, circuit(("In", None), ("Out", None), ("Or", 2))),
        )

    def test_reuse_challenges_allow_components_or_plain_gates(self):
        cases = {
            10: "07_half_adder.dig",
            11: "08_full_adder.dig",
            13: "12_at_least_one.dig",
        }
        for challenge_id, component in cases.items():
            with self.subTest(challenge_id=challenge_id, implementation="component"):
                self.assertIsNone(self.validate(
                    challenge_id,
                    circuit(("In", None), ("Out", None), (component, None)),
                ))
            with self.subTest(challenge_id=challenge_id, implementation="gates"):
                self.assertIsNone(self.validate(
                    challenge_id,
                    circuit(
                        ("In", None), ("Out", None),
                        ("And", 2), ("Or", 2), ("XOr", 2),
                    ),
                ))

    def test_seven_segment_requires_matching_output_nets(self):
        self.assertIsNone(self.validate(15, seven_segment_circuit()))
        self.assertIn("출력 b", self.validate(
            15, seven_segment_circuit(swap_b_c=True)
        ))
        self.assertIn("정확히 1개", self.validate(
            15, seven_segment_circuit(include_display=False)
        ))



    def test_custom_component_bundle_is_accepted_and_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            submission = work / "team_full_adder.dig"
            dependency = work / "team_half_adder.dig"
            full_adder = (ROOT / "solutions" / "2026-summer" / "08_full_adder.dig").read_text(
                encoding="utf-8"
            )
            submission.write_text(
                full_adder.replace("07_half_adder.dig", dependency.name),
                encoding="utf-8",
            )
            shutil.copy2(ROOT / "solutions" / "2026-summer" / "07_half_adder.dig", dependency)

            result = grader.grade_submission(10, str(submission), (str(dependency),))
            self.assertEqual(result["status"], "graded")
            self.assertEqual((result["passed"], result["total"]), (8, 8))

            dependency.write_text(circuit(("And", 3)), encoding="utf-8")
            self.assertIn(
                "2개보다 많은",
                grader._validate_structure(10, str(submission), (str(dependency),)),
            )

if __name__ == "__main__":
    unittest.main()
