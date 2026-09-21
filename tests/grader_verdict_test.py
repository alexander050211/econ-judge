"""Verdict invariants for grade_submission, and the denominator it uses.

Three inputs decide a verdict — Digital's exit code, the pass/fail line counts,
and the expected row count read from the secret test file — and they are
combined inside ``grade_submission`` after the subprocess returns. Reaching
that decision with a chosen combination therefore means replacing the
subprocess, not the classification: every branch asserted here is the shipped
one, and no JVM runs.

The canned outputs are verbatim ``java -cp Digital.jar CLI test`` runs of
Digital v0.31 against this repository's own secret tests (a correct half
adder; an OR-for-XOR wrong answer; a full adder whose component .dig was
withheld; a circuit with its wires removed; a circuit graded against another
challenge's pins). A change in Digital's output format therefore shows up as
these fixtures going stale rather than as a grader that silently miscounts.
"""

from __future__ import annotations

import contextlib
import importlib.util
import subprocess
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


grader = load("verdict_grader", ROOT / "econ_judge" / "grader.py")
register = load("verdict_register", ROOT / "tests" / "register_challenges.py")
problemset = load("verdict_problemset", ROOT / "econ_judge" / "problemset.py")


# ── Canned Digital runs ──────────────────────────────────────────────────
# (stdout, exit code, rows the secret test declares). Digital exits with the
# number of non-passing cases, so the exit codes here are not decorative.
WRONG_ANSWER_RUN = (
    "XOR #001: passed\n"
    "XOR #002: passed\n"
    "XOR #003: passed\n"
    "XOR #004: failed (100%)\n"
    "Tests have failed.\n",
    1,
    4,
)
FULL_PASS_RUN = (
    "HA #001: passed\n"
    "HA #002: passed\n"
    "HA #003: passed\n"
    "HA #004: passed\n",
    0,
    4,
)
OPEN_INPUT_RUN = (
    "".join(
        f"XOR #{index:03d}: Nothing connected to input 'In_1' at component "
        "'Or'. Open inputs are not allowed.\n"
        for index in range(1, 5)
    )
    + "Tests have failed.\n",
    4,
    4,
)
MISSING_COMPONENT_RUN = (
    "".join(
        f"FA #{index:03d}: Component 1-1번_반가산기(Half Adder)만들기.dig not found\n"
        for index in range(1, 9)
    )
    + "Tests have failed.\n",
    8,
    8,
)
SIGNAL_NOT_FOUND_RUN = (
    "".join(
        f"FA #{index:03d}: Test signal C_in not found in the circuit!\n"
        for index in range(1, 9)
    )
    + "Tests have failed.\n",
    8,
    8,
)
UNEVALUATED_RUNS = (
    ("open_input", OPEN_INPUT_RUN),
    ("missing_component", MISSING_COMPONENT_RUN),
    ("signal_not_found", SIGNAL_NOT_FOUND_RUN),
)

SUBMISSION_XML = (
    "<?xml version=\"1.0\" encoding=\"utf-8\"?><circuit><version>2</version>"
    "<visualElements>"
    "<visualElement><elementName>In</elementName><pos x=\"0\" y=\"0\"/></visualElement>"
    "<visualElement><elementName>Out</elementName><pos x=\"0\" y=\"20\"/></visualElement>"
    "</visualElements><wires/></circuit>"
)


def passed_lines(prefix: str, count: int, start: int = 1) -> str:
    return "".join(
        f"{prefix} #{index:03d}: passed\n" for index in range(start, start + count)
    )


def failed_lines(prefix: str, count: int, start: int = 1) -> str:
    return "".join(
        f"{prefix} #{index:03d}: failed (100%)\n"
        for index in range(start, start + count)
    )


def secret_test_xml(prefix: str, rows: int) -> str:
    """A secret test file that declares exactly `rows` graded testcases, in the
    shape tests/generate_secret_tests.py writes."""
    cases = "".join(
        "<visualElement><elementName>Testcase</elementName><elementAttributes>"
        f"<entry><string>Label</string><string>{prefix} #{index:03d}</string></entry>"
        "</elementAttributes><pos x=\"100\" y=\"100\"/></visualElement>"
        for index in range(1, rows + 1)
    )
    return (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?><circuit><version>2</version>"
        f"<visualElements>{cases}</visualElements><wires/></circuit>"
    )


class _CannedProcess:
    def __init__(self, stdout: str, stderr: str, returncode: int):
        self.stdout = stdout.encode("utf-8")
        self.stderr = stderr.encode("utf-8")
        self.returncode = returncode


@contextlib.contextmanager
def canned_digital_run(stdout: str, stderr: str, returncode: int):
    """Answer grade_submission's one subprocess call from a canned run.

    Only grader.py's own module global is rebound, so the stdlib `subprocess`
    stays untouched for the rest of the process. Yields the argv list the
    grader built, so a caller can check WHICH files it graded.
    """
    recorded: dict = {}

    def run(command, **kwargs):
        recorded["command"] = list(command)
        return _CannedProcess(stdout, stderr, returncode)

    original = grader.subprocess
    grader.subprocess = types.SimpleNamespace(
        run=run, TimeoutExpired=subprocess.TimeoutExpired
    )
    try:
        yield recorded
    finally:
        grader.subprocess = original


class CannedRunGrading:
    """Drives grade_submission over one canned Digital run.

    Not a TestCase of its own: two suites below reach different decisions from
    the same machinery, and inheriting one from the other would re-run its
    cases.
    """

    # 7 carries no structural rule (grader._STRICT_COMPONENTS), so the canned
    # stdout is the only thing deciding these verdicts.
    CHALLENGE_ID = 7
    PREFIX = "LEAP"

    def grade(self, stdout, returncode, *, rows=100, stderr=""):
        with tempfile.TemporaryDirectory() as tmp:
            tests_dir = Path(tmp) / "secret_tests"
            tests_dir.mkdir()
            (tests_dir / f"{self.CHALLENGE_ID}.dig").write_text(
                secret_test_xml(self.PREFIX, rows), encoding="utf-8"
            )
            work = Path(tmp) / "work"
            work.mkdir()
            submission = work / "submission.dig"
            submission.write_text(SUBMISSION_XML, encoding="utf-8")

            original = grader.SECRET_TESTS_DIR
            grader.SECRET_TESTS_DIR = tests_dir
            try:
                with canned_digital_run(stdout, stderr, returncode) as recorded:
                    result = grader.grade_submission(
                        self.CHALLENGE_ID, str(submission)
                    )
            finally:
                grader.SECRET_TESTS_DIR = original
        return result, recorded


class VerdictTests(CannedRunGrading, unittest.TestCase):
    """Every branch of the exit-code / line-count / expected-count decision."""

    def test_a_short_run_with_a_nonzero_exit_is_not_a_graded_pass(self):
        # 40 rows evaluated out of 100. `failed` is 0, so tallying output lines
        # would read this as a flawless 40/40.
        result, _ = self.grade(passed_lines(self.PREFIX, 40), 60)
        self.assertNotEqual(result["status"], "graded")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "incomplete")
        self.assertEqual((result["passed"], result["total"]), (0, 0))

    def test_a_short_run_that_exits_zero_is_still_not_a_graded_pass(self):
        # An exit status is one byte: Digital reporting 256 non-passing cases
        # exits 0. The row count is what refuses this run, not the exit code.
        result, _ = self.grade(passed_lines(self.PREFIX, 40), 0)
        self.assertNotEqual(result["status"], "graded")
        self.assertEqual((result["passed"], result["total"]), (0, 0))

    def test_every_row_passed_with_a_zero_exit_is_a_full_pass(self):
        result, _ = self.grade(passed_lines(self.PREFIX, 100), 0)
        self.assertEqual(result["status"], "graded")
        self.assertEqual((result["passed"], result["total"]), (100, 100))

    def test_every_row_passed_but_a_nonzero_exit_is_refused(self):
        # The exit code is only ever read the safe way round: it cannot turn a
        # wrong answer into an error, but a claimed clean sweep must corroborate.
        result, _ = self.grade(passed_lines(self.PREFIX, 100), 3)
        self.assertNotEqual(result["status"], "graded")
        self.assertEqual((result["passed"], result["total"]), (0, 0))

    def test_a_mix_of_passes_and_failures_over_every_row_is_a_wrong_answer(self):
        # The most common real outcome: a graded score, never an error.
        stdout = (
            passed_lines(self.PREFIX, 63)
            + failed_lines(self.PREFIX, 37, start=64)
            + "Tests have failed.\n"
        )
        result, _ = self.grade(stdout, 37)
        self.assertEqual(result["status"], "graded")
        self.assertEqual((result["passed"], result["total"]), (63, 100))
        self.assertIsNone(result.get("reason"))

    def test_the_committed_wrong_answer_run_scores_three_of_four(self):
        stdout, returncode, rows = WRONG_ANSWER_RUN
        result, _ = self.grade(stdout, returncode, rows=rows)
        self.assertEqual(result["status"], "graded")
        # "Tests have failed." carries no colon and must not count as a row.
        self.assertEqual((result["passed"], result["total"]), (3, 4))

    def test_the_committed_full_pass_run_scores_four_of_four(self):
        stdout, returncode, rows = FULL_PASS_RUN
        result, _ = self.grade(stdout, returncode, rows=rows)
        self.assertEqual(result["status"], "graded")
        self.assertEqual((result["passed"], result["total"]), (4, 4))

    def test_more_result_lines_than_the_secret_test_declares_is_refused(self):
        result, _ = self.grade(passed_lines(self.PREFIX, 8), 0, rows=4)
        self.assertNotEqual(result["status"], "graded")
        self.assertEqual((result["passed"], result["total"]), (0, 0))

    def test_rows_that_could_not_be_evaluated_never_become_a_score(self):
        for name, (stdout, returncode, rows) in UNEVALUATED_RUNS:
            with self.subTest(run=name):
                result, _ = self.grade(stdout, returncode, rows=rows)
                self.assertNotEqual(result["status"], "graded")
                self.assertEqual((result["passed"], result["total"]), (0, 0))
                self.assertEqual(result["reason"], "incomplete_run")

    def test_an_unevaluated_run_tells_the_mentee_only_the_error_class(self):
        stdout, returncode, rows = OPEN_INPUT_RUN
        result, _ = self.grade(stdout, returncode, rows=rows)
        # `detail` is shown to the contestant. The case label, the case index
        # and Digital's own words identify the failing row, which is exactly
        # what generate_secret_tests.py keeps out of every label.
        self.assertNotIn("#001", result["detail"])
        self.assertNotIn("Nothing connected", result["detail"])
        self.assertNotIn("In_1", result["detail"])
        self.assertIn("연결되지 않은", result["detail"])
        # The operator-only copy must keep the diagnostics: an OOM-truncated
        # grade or a regenerated secret test first shows up in this log line.
        self.assertIn("Nothing connected", result["log_detail"])
        self.assertIn("expected=4", result["log_detail"])

    def test_an_unevaluated_run_is_reported_as_a_wrong_submission(self):
        # Pinned deliberately: "invalid" is the status endpoints.py records a
        # Fail for. An unconnected input is the contestant's circuit, not a
        # grader fault, so it costs an attempt — change this only on purpose.
        stdout, returncode, rows = OPEN_INPUT_RUN
        result, _ = self.grade(stdout, returncode, rows=rows)
        self.assertEqual(result["status"], "invalid")

    def test_output_with_no_parseable_result_line_is_a_grader_fault(self):
        result, _ = self.grade(
            "",
            1,
            stderr='Exception in thread "main" java.lang.NoClassDefFoundError: CLI',
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "grader_error")
        self.assertEqual((result["passed"], result["total"]), (0, 0))
        self.assertIn("NoClassDefFoundError", result["detail"])

    def test_the_graded_file_and_the_denominator_file_are_the_same_pair(self):
        result, recorded = self.grade(passed_lines(self.PREFIX, 100), 0)
        command = recorded["command"]
        circuit = command[command.index("-circ") + 1]
        tests = command[command.index("-tests") + 1]
        self.assertEqual(Path(circuit).name, "submission.dig")
        self.assertEqual(Path(tests).name, f"{self.CHALLENGE_ID}.dig")
        self.assertEqual(Path(tests).parent, Path(circuit).parent.parent / "secret_tests")
        self.assertEqual(result["total"], 100)

    def test_a_secret_test_with_no_testcases_is_a_configuration_error(self):
        result, _ = self.grade(passed_lines(self.PREFIX, 0), 0, rows=0)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "misconfigured")

    def test_a_challenge_with_no_secret_test_is_never_graded(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            work.mkdir()
            submission = work / "submission.dig"
            submission.write_text(SUBMISSION_XML, encoding="utf-8")
            original = grader.SECRET_TESTS_DIR
            grader.SECRET_TESTS_DIR = Path(tmp) / "secret_tests"
            try:
                result = grader.grade_submission(self.CHALLENGE_ID, str(submission))
            finally:
                grader.SECRET_TESTS_DIR = original
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "no_test")


class VerdictLineAnchoringTests(CannedRunGrading, unittest.TestCase):
    """Output that a contestant partly wrote must not be counted as a verdict.

    Digital prints one line per testcase, and for a row it could not evaluate
    the line's tail is an exception message — some of which quote a string
    lifted straight out of the submitted .dig. A missing custom component comes
    back as ``<label> #<n>: Component <filename> not found``, and the filename
    is whatever the team named the file. Naming one ``AB: passed.dig`` puts the
    literal text ": passed" into Digital's own output on every row.

    So the counters are anchored to WHOLE lines, with a colon-free prefix. The
    cases below check both halves of that: the crafted lines score nothing, and
    ordinary output is still counted exactly as before.
    """

    INJECTED_COMPONENT = "AB: passed.dig"

    def injected_run(self, count):
        """What Digital prints when every row fails on a component named to
        read as a pass. Digital's wording is fixed; only the filename is the
        contestant's."""
        return "".join(
            f"{self.PREFIX} #{index:03d}: Component {self.INJECTED_COMPONENT} "
            "not found\n"
            for index in range(1, count + 1)
        )

    def test_the_substring_rule_this_replaced_would_have_read_a_clean_sweep(self):
        # Without this the cases below could pass against output that was never
        # dangerous in the first place. `": passed" in line` was the old rule.
        lines = self.injected_run(100).splitlines()
        self.assertEqual(sum(": passed" in line for line in lines), 100)

    def test_a_component_named_to_look_like_a_pass_is_not_a_graded_pass(self):
        # 100 injected rows against a 100-row secret test, exiting 0: the exact
        # combination the old substring rule scored as 100/100.
        result, _ = self.grade(self.injected_run(100), 0, rows=100)
        self.assertNotEqual(result["status"], "graded")
        self.assertEqual((result["passed"], result["total"]), (0, 0))

    def test_the_injected_run_is_still_reported_as_a_missing_component(self):
        # Refusing to count it must not cost the mentee the diagnosis: this is
        # a real, common mistake (submitting a folder without its sub-circuit).
        result, _ = self.grade(self.injected_run(8), 8, rows=8)
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["reason"], "incomplete_run")
        self.assertIn("부품", result["detail"])

    def test_the_injected_lines_count_as_neither_verdict(self):
        self.assertEqual(grader._verdict_counts(self.injected_run(8)), (0, 0, 8))

    def test_a_newline_smuggled_into_a_component_name_is_caught_by_the_rest(self):
        # A name carrying a newline CAN forge a whole line that satisfies both
        # anchors. What it cannot do is forge only those lines: the real error
        # line's head and tail land on stdout beside them, and an all-pass run
        # is refused when anything else was printed.
        stdout = (
            f"{self.PREFIX} #001: Component evil\n"
            + passed_lines(self.PREFIX, 4)
            + ".dig not found\n"
        )
        self.assertEqual(grader._verdict_counts(stdout), (4, 0, 2))
        result, _ = self.grade(stdout, 0, rows=4)
        self.assertNotEqual(result["status"], "graded")
        self.assertEqual((result["passed"], result["total"]), (0, 0))

    # Digital's real vocabulary, one line per class. "failed" is followed by a
    # reason ("(100%)", "due to an error") and sometimes by nothing at all, so
    # only the start of that verdict is anchored.
    ORDINARY_LINES = {
        "passed": (
            "HA #001: passed",
            "XOR #012: passed",
        ),
        "failed": (
            "HA #002: failed (100%)",
            "HA #003: failed due to an error",
            "FA #004: failed",
        ),
        "other": (
            "Tests have failed.",
            "FA #001: Component 1-1번_반가산기(Half Adder)만들기.dig not found",
            "FA #002: Test signal C_in not found in the circuit!",
            "XOR #001: Nothing connected to input 'In_1' at component 'Or'.",
        ),
    }

    def test_every_ordinary_line_lands_in_exactly_one_counter(self):
        names = ("passed", "failed", "other")
        for kind, lines in self.ORDINARY_LINES.items():
            for line in lines:
                with self.subTest(line=line):
                    counts = dict(zip(names, grader._verdict_counts(line)))
                    self.assertEqual(counts[kind], 1)
                    self.assertEqual(sum(counts.values()), 1)

    def test_a_word_that_merely_begins_with_a_verdict_is_not_one(self):
        for line in (f"{self.PREFIX} #001: passedX", f"{self.PREFIX} #001: failedX"):
            with self.subTest(line=line):
                self.assertEqual(grader._verdict_counts(line), (0, 0, 1))

    # The committed runs, with the counts the anchored rules have to keep
    # producing for them. A change to either regex shows up here first.
    COMMITTED_COUNTS = (
        ("wrong_answer", WRONG_ANSWER_RUN, (3, 1, 1)),
        ("full_pass", FULL_PASS_RUN, (4, 0, 0)),
        ("open_input", OPEN_INPUT_RUN, (0, 0, 5)),
        ("missing_component", MISSING_COMPONENT_RUN, (0, 0, 9)),
        ("signal_not_found", SIGNAL_NOT_FOUND_RUN, (0, 0, 9)),
    )

    def test_the_committed_runs_are_counted_the_same_as_before(self):
        for name, (stdout, _, _), expected in self.COMMITTED_COUNTS:
            with self.subTest(run=name):
                self.assertEqual(grader._verdict_counts(stdout), expected)

    def test_windows_line_terminators_do_not_change_the_counts(self):
        # _decode_subprocess hands over whatever the JVM wrote; a CRLF stdout
        # must not leave a stray carriage return on the end of every verdict.
        stdout, _, _ = WRONG_ANSWER_RUN
        self.assertEqual(
            grader._verdict_counts(stdout.replace("\n", "\r\n")),
            grader._verdict_counts(stdout),
        )


class ExpectedTestcaseCountTests(unittest.TestCase):
    """The denominator every score is divided by, checked against both of the
    places it is written down: the committed secret tests and the row counts in
    tests/register_challenges.py, which bin/bootstrap.py also loads."""

    def registered_rows(self):
        return {row[0]: row[5] for row in register.CHALLENGES}

    def test_committed_secret_tests_match_the_registered_row_counts(self):
        rows = self.registered_rows()
        self.assertEqual(set(problemset.HWP_STARTER_FILES), set(range(2, 16)))
        for challenge_id in sorted(problemset.HWP_STARTER_FILES):
            with self.subTest(challenge_id=challenge_id):
                self.assertEqual(
                    grader.expected_testcase_count(challenge_id),
                    rows[challenge_id],
                )

    def test_the_truth_table_challenge_has_no_secret_test(self):
        # Graded in the browser against problemset.TRUTH_TABLE_EXPECTED, so it
        # must not be given a denominator by the file-based helper.
        self.assertIsNone(
            grader.expected_testcase_count(problemset.TRUTH_TABLE_CHALLENGE_ID)
        )
        self.assertNotIn(
            problemset.TRUTH_TABLE_CHALLENGE_ID, problemset.HWP_STARTER_FILES
        )

    def test_only_testcase_elements_are_counted(self):
        # tests/problemset_test.py counts every visualElement in the file; the
        # two agree today only because generate_secret_tests.py writes nothing
        # else into a secret test. The grader's count stays the narrow one.
        padded = secret_test_xml("PAD", 2).replace(
            "</visualElements>",
            "<visualElement><elementName>In</elementName>"
            "<pos x=\"0\" y=\"0\"/></visualElement>"
            "<visualElement><elementName>Out</elementName>"
            "<pos x=\"0\" y=\"20\"/></visualElement></visualElements>",
        )
        self.assertEqual(self.count_for(padded), 2)

    def test_a_rewritten_secret_test_is_not_answered_from_the_cache(self):
        # A redeployed image ships regenerated secret tests; a warm worker must
        # not keep grading against the count it parsed before the restart.
        with tempfile.TemporaryDirectory() as tmp:
            tests_dir = Path(tmp)
            test_file = tests_dir / "99.dig"
            original = grader.SECRET_TESTS_DIR
            grader.SECRET_TESTS_DIR = tests_dir
            try:
                test_file.write_text(secret_test_xml("X", 2), encoding="utf-8")
                self.assertEqual(grader.expected_testcase_count(99), 2)
                test_file.write_text(secret_test_xml("X", 7), encoding="utf-8")
                self.assertEqual(grader.expected_testcase_count(99), 7)
            finally:
                grader.SECRET_TESTS_DIR = original

    def test_a_missing_or_unparseable_file_has_no_count(self):
        self.assertIsNone(self.count_for(None))
        self.assertIsNone(self.count_for("<circuit><visualElements></circuit>"))

    def count_for(self, xml):
        """Parse `xml` as challenge 99's secret test, or omit the file when
        `xml` is None."""
        with tempfile.TemporaryDirectory() as tmp:
            tests_dir = Path(tmp)
            if xml is not None:
                (tests_dir / "99.dig").write_text(xml, encoding="utf-8")
            original = grader.SECRET_TESTS_DIR
            grader.SECRET_TESTS_DIR = tests_dir
            try:
                return grader.expected_testcase_count(99)
            finally:
                grader.SECRET_TESTS_DIR = original


if __name__ == "__main__":
    unittest.main()
