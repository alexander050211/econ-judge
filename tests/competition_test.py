"""Tests for the 70 / 10 / 80 minute competition-round schedule."""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "competition", ROOT / "econ_judge" / "competition.py"
)
competition = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = competition
spec.loader.exec_module(competition)


class CompetitionPhaseTests(unittest.TestCase):
    def test_disabled_schedule_leaves_the_full_set_open(self):
        with patch.dict(os.environ, {}, clear=True):
            phase = competition.current_phase()
        self.assertEqual(phase.name, "open")
        self.assertEqual(phase.visible_challenge_ids, competition.ALL_CHALLENGE_IDS)
        self.assertTrue(phase.submissions_open)

    def test_round_boundaries_and_problem_groups(self):
        start = datetime(2026, 8, 1, 9, 0, tzinfo=timezone(timedelta(hours=9)))
        with patch.dict(
            os.environ,
            {"ECON_JUDGE_COMPETITION_START": start.isoformat()},
            clear=True,
        ):
            self.assertEqual(competition.current_phase(start - timedelta(seconds=1)).name, "before")
            round_1 = competition.current_phase(start)
            self.assertEqual(round_1.name, "round1")
            self.assertTrue(round_1.submissions_open)
            self.assertEqual(round_1.visible_challenge_ids, competition.ROUND_1_CHALLENGE_IDS)

            pause = competition.current_phase(start + timedelta(minutes=70))
            self.assertEqual(pause.name, "break")
            self.assertFalse(pause.submissions_open)
            self.assertEqual(pause.visible_challenge_ids, competition.ROUND_1_CHALLENGE_IDS)

            round_2 = competition.current_phase(start + timedelta(minutes=80))
            self.assertEqual(round_2.name, "round2")
            self.assertTrue(round_2.submissions_open)
            self.assertEqual(round_2.visible_challenge_ids, competition.ROUND_2_CHALLENGE_IDS)

            finished = competition.current_phase(start + timedelta(minutes=160))
            self.assertEqual(finished.name, "finished")
            self.assertFalse(finished.submissions_open)
            self.assertEqual(finished.visible_challenge_ids, competition.ALL_CHALLENGE_IDS)

    def test_bare_start_time_is_rejected(self):
        with patch.dict(
            os.environ,
            {"ECON_JUDGE_COMPETITION_START": "2026-08-01T09:00:00"},
            clear=True,
        ):
            phase = competition.current_phase()
            self.assertEqual(phase.name, "misconfigured")
            self.assertFalse(phase.submissions_open)


if __name__ == "__main__":
    unittest.main()
