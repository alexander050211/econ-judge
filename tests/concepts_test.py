"""Tie every concepts.py entry to the challenge that id actually holds.

Commit b9d5332 renumbered the problem set — 7 became the leap-year detector
and 10 the full adder — and nothing bound CHALLENGE_CONCEPTS to the new ids,
so an entry could go on describing the problem its id used to hold. The
checkable signal is the pin names the hints quote: generate_secret_tests.SPECS
is the ground truth for the pins each challenge is graded on, so a hint that
names `C_out` cannot belong to the leap-year detector.

Ids 1-6 quote no pin at all, so nothing here can place them; for those only
presence and shape are checked.
"""

from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


concepts = load("summer_concepts", ROOT / "econ_judge" / "concepts.py")
generator = load("concepts_generator", ROOT / "tests" / "generate_secret_tests.py")
register = load("concepts_register", ROOT / "tests" / "register_challenges.py")

PINS = {
    challenge_id: frozenset(spec["in_pins"]) | frozenset(spec["out_pins"])
    for challenge_id, spec in generator.SPECS.items()
}

# Latin runs in the hints. Korean never matches, and gate vocabulary ("NAND",
# "Sum", "BCD") is discarded below because it names no pin of any challenge.
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def concept_text(items) -> str:
    return " ".join(f"{item['label']} {item['hint']}" for item in items)


def names_pin(token: str, pins) -> bool:
    """Whether `token` is one of `pins`, or the value that they spell out.

    The multi-bit hints say "X" for the number carried by X1 and X0, so a bare
    name counts as naming its own indexed pins.
    """
    return token in pins or any(
        pin.startswith(token) and pin[len(token):].isdigit() for pin in pins
    )


def pin_tokens(items) -> set:
    """The words in one entry that name a pin of some challenge."""
    return {
        token
        for token in IDENTIFIER.findall(concept_text(items))
        if any(names_pin(token, pins) for pins in PINS.values())
    }


def misplaced(concept_map) -> dict:
    """ids whose hints name a pin their own challenge does not have."""
    found = {}
    for challenge_id, items in concept_map.items():
        pins = PINS.get(challenge_id)
        if pins is None:  # id 1 is graded by the truth-table endpoint
            continue
        wrong = {token for token in pin_tokens(items) if not names_pin(token, pins)}
        if wrong:
            found[challenge_id] = wrong
    return found


class ChallengeConceptTests(unittest.TestCase):
    def test_every_registered_challenge_has_a_usable_entry(self):
        self.assertEqual(
            set(concepts.CHALLENGE_CONCEPTS),
            {row[0] for row in register.CHALLENGES},
        )
        for challenge_id, items in concepts.CHALLENGE_CONCEPTS.items():
            with self.subTest(challenge_id=challenge_id):
                self.assertTrue(items)
                for item in items:
                    self.assertTrue(item["label"].strip())
                    self.assertTrue(item["hint"].strip())
        self.assertEqual(concepts.concept_info(9), concepts.CHALLENGE_CONCEPTS[9])
        self.assertEqual(concepts.concept_info(999), concepts.DEFAULT_CONCEPTS)

    def test_no_entry_names_a_pin_of_a_different_challenge(self):
        self.assertEqual(misplaced(concepts.CHALLENGE_CONCEPTS), {})

    def test_the_renumbered_ids_each_name_a_pin_of_their_own(self):
        # 7-11 are the ids b9d5332 moved. Without a pin of their own in the
        # text, an entry could drift onto one of them and the check above
        # would have nothing to notice.
        for challenge_id in (7, 8, 9, 10, 11):
            with self.subTest(challenge_id=challenge_id):
                items = concepts.CHALLENGE_CONCEPTS[challenge_id]
                self.assertTrue(
                    {
                        token
                        for token in pin_tokens(items)
                        if names_pin(token, PINS[challenge_id])
                    }
                )

    def test_the_check_catches_the_drift_it_guards_against(self):
        swapped = dict(concepts.CHALLENGE_CONCEPTS)
        swapped[7], swapped[10] = swapped[10], swapped[7]
        self.assertEqual(set(misplaced(swapped)), {7, 10})


if __name__ == "__main__":
    unittest.main()
