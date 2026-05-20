import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DIGITAL_JAR = Path(os.environ.get("ECON_JUDGE_DIGITAL_JAR", REPO_ROOT / "Digital.jar"))
SECRET_TESTS_DIR = Path(
    os.environ.get("ECON_JUDGE_TESTS_DIR", REPO_ROOT / "secret_tests")
)
JAVA = os.environ.get("ECON_JUDGE_JAVA", "java")
TIMEOUT_SEC = int(os.environ.get("ECON_JUDGE_TIMEOUT", "15"))


def grade_submission(challenge_id: int, submission_path: str) -> dict:
    test_file = SECRET_TESTS_DIR / f"{challenge_id}.dig"
    if not test_file.exists():
        return {
            "passed": 0,
            "total": 0,
            "detail": f"No secret test file configured for challenge {challenge_id}",
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
