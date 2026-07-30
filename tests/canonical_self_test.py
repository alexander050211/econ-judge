"""Verify active summer canonical subcircuits against their secret tests."""

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
TESTS = [
    ("07_half_adder.dig", 9, 4),
    ("08_full_adder.dig", 10, 8),
    ("12_at_least_one.dig", 12, 4),
]


def main() -> int:
    if not TESTS:
        print("SKIP: summer canonical files have not been added yet")
        return 0
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
