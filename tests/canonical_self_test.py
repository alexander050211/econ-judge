"""Verify every committed reference circuit against its secret test.

This is the check that proves a problem set and its testcases agree before a
camp: it runs the real grader, the real Digital.jar and the committed secret
tests over every reference circuit in the repository, and it is the only test
here that needs a JVM. Kept as a script rather than a unittest case so
`python -m unittest discover -s tests -p "*_test.py"` stays offline.

Two sweeps, both ending in grader.grade_submission:

* `canonical/` — the shared sub-circuits, staged under the HWP filename the
  challenge that reuses them expects.
* `solutions/2026-summer/` — one reference answer per submittable challenge.
  Each round folder is copied whole into the grading directory, dependencies
  and all, which is exactly the shape endpoints._stage_digital_bundle produces
  from a contestant's uploaded folder.

Run: .venv/Scripts/python.exe tests/canonical_self_test.py
"""

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "grader", str(REPO_ROOT / "econ_judge" / "grader.py")
)
grader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(grader)

_problemset_spec = importlib.util.spec_from_file_location(
    "problemset", str(REPO_ROOT / "econ_judge" / "problemset.py")
)
problemset = importlib.util.module_from_spec(_problemset_spec)
_problemset_spec.loader.exec_module(problemset)

SOLUTIONS_DIR = REPO_ROOT / "solutions" / "2026-summer"

# (canonical filename, challenge_id, expected pass count, components).
# `components` maps the challenge whose HWP starter filename a sub-circuit is
# named after to the canonical file that implements it. Digital resolves a
# custom component by its filename next to the main circuit and
# grader.CANONICAL_SUBCIRCUITS is empty, so each one is staged under that
# filename and declared as a dependency — the same way an uploaded round
# folder reaches the grader in production.
TESTS = [
    ("07_half_adder.dig", 9, 4, {}),
    ("08_full_adder.dig", 10, 8, {9: "07_half_adder.dig"}),
    ("12_at_least_one.dig", 12, 4, {}),
]


def java_unavailable():
    """Why this cannot run here, or None. Java and Digital.jar are the two
    artifacts the grader shells out to; without them every grade would come
    back `java_missing` and read as a broken problem set."""
    if shutil.which(grader.JAVA) is None:
        return f"java executable '{grader.JAVA}' not found on PATH"
    if not Path(grader.DIGITAL_JAR).is_file():
        return f"Digital.jar not found at {grader.DIGITAL_JAR}"
    return None


def grade_canonical(canonical_filename, challenge_id, components):
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "submission.dig"
        shutil.copy(REPO_ROOT / "canonical" / canonical_filename, dst)
        dependencies = []
        for component_cid, component_filename in components.items():
            component = Path(tmp) / PurePosixPath(
                problemset.HWP_STARTER_FILES[component_cid]
            ).name
            shutil.copy(REPO_ROOT / "canonical" / component_filename, component)
            dependencies.append(str(component))
        return grader.grade_submission(challenge_id, str(dst), tuple(dependencies))


def grade_solution(challenge_id, relative_path):
    """Grade one reference answer with its whole round folder staged beside it.

    The folder is flattened into the grading directory rather than copied as a
    tree: Digital resolves a custom component by filename in the circuit's own
    directory, and that flattening is what the upload endpoint does too.
    """
    source = SOLUTIONS_DIR.joinpath(*PurePosixPath(relative_path).parts)
    if not source.is_file():
        return None, f"reference circuit missing: {relative_path}"
    with tempfile.TemporaryDirectory() as tmp:
        staged = {}
        for sibling in sorted(source.parent.glob("*.dig")):
            destination = Path(tmp) / sibling.name
            shutil.copy(sibling, destination)
            staged[sibling.name] = destination
        dependencies = tuple(
            str(path) for name, path in staged.items() if name != source.name
        )
        result = grader.grade_submission(
            challenge_id, str(staged[source.name]), dependencies
        )
    return result, None


def check(label, expected, result, error, fails):
    """Record one graded reference circuit. `expected` is the row count the
    secret test declares, so a reference answer and a regenerated testcase file
    cannot drift apart without this failing."""
    if error is not None:
        fails.append((label, error))
        return
    passed, total = result["passed"], result["total"]
    if expected and passed == expected and total == expected:
        print(f"  OK  {label}: {passed}/{total}")
        return
    fails.append(
        (
            label,
            f"got {passed}/{total}, expected {expected}/{expected}; "
            f"status={result.get('status')} reason={result.get('reason')}; "
            f"detail: {result.get('detail', '')[:160]}",
        )
    )


def main() -> int:
    reason = java_unavailable()
    if reason:
        print(f"SKIP: {reason}")
        return 0

    fails = []
    total_checks = 0

    print("canonical/ sub-circuits")
    for canonical_filename, cid, expected, components in TESTS:
        total_checks += 1
        label = f"chal {cid:>2} ({canonical_filename})"
        missing = [
            name
            for name in (canonical_filename, *components.values())
            if not (REPO_ROOT / "canonical" / name).exists()
        ]
        if missing:
            fails.append((label, f"canonical file missing: {', '.join(missing)}"))
            continue
        check(
            label,
            expected,
            grade_canonical(canonical_filename, cid, components),
            None,
            fails,
        )

    print()
    print(f"{SOLUTIONS_DIR.relative_to(REPO_ROOT).as_posix()}/ reference answers")
    for cid, relative_path in sorted(problemset.HWP_STARTER_FILES.items()):
        total_checks += 1
        label = f"chal {cid:>2} ({PurePosixPath(relative_path).name})"
        expected = grader.expected_testcase_count(cid)
        if not expected:
            fails.append((label, f"secret_tests/{cid}.dig declares no testcases"))
            continue
        result, error = grade_solution(cid, relative_path)
        check(label, expected, result, error, fails)

    print()
    if fails:
        print(f"FAIL: {len(fails)}/{total_checks} reference circuits disagree with their secret tests")
        for name, msg in fails:
            print(f"  {name}: {msg}")
        return 1
    print(f"PASS: {total_checks}/{total_checks} reference circuits consistent with their secret tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
