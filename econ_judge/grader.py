import os
import re
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DIGITAL_JAR = Path(os.environ.get("ECON_JUDGE_DIGITAL_JAR", REPO_ROOT / "Digital.jar"))
SECRET_TESTS_DIR = Path(
    os.environ.get("ECON_JUDGE_TESTS_DIR", REPO_ROOT / "secret_tests")
)
CANONICAL_DIR = Path(
    os.environ.get("ECON_JUDGE_CANONICAL_DIR", REPO_ROOT / "canonical")
)
JAVA = os.environ.get("ECON_JUDGE_JAVA", "java")
TIMEOUT_SEC = int(os.environ.get("ECON_JUDGE_TIMEOUT", "45"))

# Per-challenge canonical sub-circuit filenames seeded next to the submission
# so Digital can resolve sub-circuit imports without the mentee needing to
# upload sibling files. Filenames must match what mentees' .dig files reference.
CANONICAL_SUBCIRCUITS = {
    2: ["A1_반가산기(HalfAdder)만들기.dig"],
    3: ["A1_반가산기(HalfAdder)만들기.dig", "A2_전가산기(FullAdder)만들기.dig"],
    15: ["A1_2비트비교기.dig"],
    16: [
        "A1_반가산기(HalfAdder)만들기.dig",
        "A2_전가산기(FullAdder)만들기.dig",
        "A3_3비트덧셈연산기만들기.dig",
        "B_보수계산기만들기.dig",
        "C_3의나눗셈기만들기.dig",
    ],
    # chal 18 (P2 full wiring) imports all 4 P2 sub-circuits. Canonical P2 C
    # is intentionally listed even though the file isn't present yet — the
    # grader's missing-canonical error path will surface a clear admin
    # message until C_7segment출력기.dig (with Out pins a..g, per the camp
    # skeleton design) is authored and dropped into canonical/.
    18: [
        "A1_2비트비교기.dig",
        "A2_2,3비트비교기.dig",
        "B_대피소배정하기.dig",
        "C_7segment출력기.dig",
    ],
}


def _seed_canonical(challenge_id: int, working_dir: Path) -> list[str]:
    """Copy canonical sub-circuits for this challenge into the submission's
    working directory. Returns the list of filenames missing from CANONICAL_DIR
    (empty if all expected files were copied)."""
    missing: list[str] = []
    for filename in CANONICAL_SUBCIRCUITS.get(challenge_id, []):
        src = CANONICAL_DIR / filename
        if not src.exists():
            missing.append(filename)
            continue
        shutil.copy(src, working_dir / filename)
    return missing


def grade_submission(challenge_id: int, submission_path: str) -> dict:
    test_file = SECRET_TESTS_DIR / f"{challenge_id}.dig"
    if not test_file.exists():
        return {
            "passed": 0,
            "total": 0,
            "detail": f"No secret test file configured for challenge {challenge_id}",
        }

    working_dir = Path(submission_path).parent
    missing = _seed_canonical(challenge_id, working_dir)
    if missing:
        return {
            "passed": 0,
            "total": 0,
            "detail": (
                f"Grader misconfigured: canonical sub-circuit(s) missing from "
                f"{CANONICAL_DIR}: {', '.join(missing)}"
            ),
        }

    try:
        proc = subprocess.run(
            [
                JAVA,
                "-Dfile.encoding=UTF-8",
                "-cp",
                str(DIGITAL_JAR),
                "CLI",
                "test",
                "-circ",
                submission_path,
                "-tests",
                str(test_file),
            ],
            capture_output=True,
            timeout=TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return {
            "passed": 0,
            "total": 0,
            "detail": f"Grader timed out after {TIMEOUT_SEC}s",
        }
    except FileNotFoundError:
        return {
            "passed": 0,
            "total": 0,
            "detail": f"Java executable '{JAVA}' not found",
        }

    stdout = _decode_subprocess(proc.stdout)
    stderr = _decode_subprocess(proc.stderr)
    passed = len(re.findall(r":\s*passed", stdout))
    failed = len(re.findall(r":\s*failed", stdout))
    total = passed + failed
    detail = (stdout.strip() or stderr.strip())[:1500]
    return {"passed": passed, "total": total, "detail": detail}


def _decode_subprocess(raw: bytes) -> str:
    """Digital on Windows prints in the active console codepage (cp949 for Korean
    locales), which can include Korean filenames in error messages. Try UTF-8
    first since `-Dfile.encoding=UTF-8` makes some messages UTF-8, then fall
    back to cp949, then latin-1 as a guaranteed-decode last resort."""
    if not raw:
        return ""
    for codec in ("utf-8", "cp949", "latin-1"):
        try:
            return raw.decode(codec)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")
