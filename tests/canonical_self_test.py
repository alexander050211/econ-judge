"""Verify every canonical .dig in canonical/ still passes the secret test for
the challenge it's the reference for. Runs the grader directly — no CTFd
needed — so this is fast (~5 seconds) and catches drift in canonical files
or secret tests before deploy.

Insurance against: someone replacing a canonical .dig with a different
version, regenerating secret tests with a buggy compute function, or
pin-name conventions silently changing."""

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "grader", str(REPO_ROOT / "econ_judge" / "grader.py")
)
grader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(grader)

# (canonical filename, challenge_id, expected pass count)
# Each canonical file is graded as if it were a submission to the challenge
# whose secret test it implements. Composition canonicals (A2 imports A1, A3
# imports A1+A2, P2 A2 imports P2 A1) work via the grader's CANONICAL_SUBCIRCUITS
# seeding map — siblings are placed in the tempdir automatically.
TESTS = [
    ("A1_반가산기(HalfAdder)만들기.dig", 1, 4),
    ("A2_전가산기(FullAdder)만들기.dig", 2, 8),
    ("A3_3비트덧셈연산기만들기.dig", 3, 64),
    ("B_보수계산기만들기.dig", 12, 16),
    ("C_3의나눗셈기만들기.dig", 13, 8),
    ("A1_2비트비교기.dig", 4, 16),
    ("A2_2,3비트비교기.dig", 15, 32),
    ("B_대피소배정하기.dig", 14, 24),
]


def main() -> int:
    fails = []
    for canonical_filename, cid, expected in TESTS:
        canonical_path = REPO_ROOT / "canonical" / canonical_filename
        if not canonical_path.exists():
            fails.append((canonical_filename, "canonical file missing"))
            continue
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "submission.dig"
            shutil.copy(canonical_path, dst)
            result = grader.grade_submission(cid, str(dst))
        passed, total = result["passed"], result["total"]
        if passed == expected and total == expected:
            print(f"  OK  chal {cid:>2}: {passed}/{total}  ({canonical_filename})")
        else:
            fails.append(
                (canonical_filename, f"chal {cid}: got {passed}/{total}, expected {expected}/{expected}; detail: {result.get('detail', '')[:160]}")
            )

    print()
    if fails:
        print(f"FAIL: {len(fails)}/{len(TESTS)} canonicals don't match their secret tests")
        for name, msg in fails:
            print(f"  {name}: {msg}")
        return 1
    print(f"PASS: {len(TESTS)}/{len(TESTS)} canonicals consistent with their secret tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
