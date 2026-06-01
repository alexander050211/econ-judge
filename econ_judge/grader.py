import os
import re
import shutil
import subprocess
import threading
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


def _int_env(name: str, default: int) -> int:
    """Read an integer env var, falling back to `default` on a missing or
    malformed value. These run at import, so an unguarded int() on a dashboard
    typo (e.g. "45s") would raise at plugin-load and crash-loop the worker —
    degrade gracefully instead. Mirrors endpoints._freeze_state's guard."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        print(f"[grader] ignoring invalid {name}={raw!r}, using {default}")
        return default


TIMEOUT_SEC = _int_env("ECON_JUDGE_TIMEOUT", 45)

# ── Grading concurrency guard ────────────────────────────────────────────
# Each grade spawns a Digital JVM. `-Xmx256m` caps only the Java heap; total
# per-JVM RSS (heap + metaspace + thread stacks + JIT) is ~350-450MB, so two
# concurrent grades would exceed the 512MB free-tier limit and OOM-kill the
# worker. We serialize JVM execution with a semaphore (default 1 slot).
#
# The web server runs as a SINGLE gunicorn worker with the `gevent` worker
# class (see bin/entrypoint.sh). gunicorn monkey-patches stdlib `threading`
# BEFORE importing the app, so this `threading.BoundedSemaphore` is actually a
# cooperative gevent lock: a grade waiting for a slot YIELDS the event loop, so
# the rest of the site (scoreboard polls, page loads) stays responsive while
# grades queue one at a time. (Without gevent — e.g. canonical_self_test.py
# importing this module directly — it's an ordinary semaphore, still correct;
# the single-threaded self-test never contends.)
#
# ⚠️ PER-PROCESS cap: this semaphore lives in one worker process, so the OOM
# guarantee holds only with WEB_WORKERS=1 (entrypoint.sh default). With N
# workers the effective JVM concurrency is N * GRADE_CONCURRENCY — keep
# WEB_WORKERS=1 on the 512MB tier, or lower GRADE_CONCURRENCY accordingly.
#
# ECON_JUDGE_CONCURRENCY: grading slots (raise on a larger plan). Default 1.
# ECON_JUDGE_QUEUE_WAIT: max seconds to wait for a slot before returning a
#   retryable "busy" error. Keep QUEUE_WAIT + ECON_JUDGE_TIMEOUT comfortably
#   under gunicorn's --timeout (60s) — a queued grade's wait is ADDITIVE to its
#   subprocess budget against that ceiling (8 + 45 = 53s leaves headroom for
#   request/file/seeding/response overhead on the 0.5 vCPU tier).
GRADE_CONCURRENCY = max(1, _int_env("ECON_JUDGE_CONCURRENCY", 1))
QUEUE_WAIT_SEC = _int_env("ECON_JUDGE_QUEUE_WAIT", 8)
_grade_sem = threading.BoundedSemaphore(GRADE_CONCURRENCY)

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


# Digital .dig files are plain XML (<?xml?> then <circuit>); a legitimate file
# never carries a DOCTYPE or ENTITY declaration. Their presence signals an XXE
# or billion-laughs entity-expansion attempt against the JVM's XML reader, so
# reject the upload before it ever reaches Java.
_XML_DANGER = re.compile(rb"<!DOCTYPE|<!ENTITY", re.IGNORECASE)


def _scan_dangerous_xml(submission_path: str):
    """Return a rejection reason string if the upload looks like an XXE /
    entity-expansion attempt, else None."""
    try:
        data = Path(submission_path).read_bytes()
    except OSError:
        return None
    # Legit Digital .dig files are UTF-8 XML and contain no NUL bytes (XML 1.0
    # forbids them, and UTF-8 never encodes one except U+0000). A NUL byte means
    # a UTF-16/UTF-32 encoding, which would let a DOCTYPE/ENTITY payload slip
    # past the ASCII byte-scan below while Java's XML parser still auto-detects
    # and processes it. Reject any non-UTF-8 upload outright.
    if b"\x00" in data:
        return "Rejected: .dig file is not UTF-8 (NUL bytes / non-UTF-8 encoding)."
    if _XML_DANGER.search(data):
        return "Rejected: .dig file contains an XML DOCTYPE/ENTITY declaration."
    return None


def grade_submission(challenge_id: int, submission_path: str) -> dict:
    test_file = SECRET_TESTS_DIR / f"{challenge_id}.dig"
    if not test_file.exists():
        return {
            "status": "error",
            "reason": "no_test",
            "passed": 0,
            "total": 0,
            "detail": f"No secret test file configured for challenge {challenge_id}",
        }

    working_dir = Path(submission_path).parent
    missing = _seed_canonical(challenge_id, working_dir)
    if missing:
        return {
            "status": "error",
            "reason": "misconfigured",
            "passed": 0,
            "total": 0,
            "detail": (
                f"Grader misconfigured: canonical sub-circuit(s) missing from "
                f"{CANONICAL_DIR}: {', '.join(missing)}"
            ),
        }

    danger = _scan_dangerous_xml(submission_path)
    if danger:
        return {
            "status": "rejected",
            "reason": "unsafe_xml",
            "passed": 0,
            "total": 0,
            "detail": danger,
        }

    # Only the JVM execution is serialized — the cheap validation above runs
    # freely. Acquire a grading slot, bounded by QUEUE_WAIT_SEC so a backlog
    # returns a retryable "busy" (no Fail recorded; see endpoints status
    # contract) rather than piling up past the request timeout.
    if not _grade_sem.acquire(timeout=QUEUE_WAIT_SEC):
        return {
            "status": "error",
            "reason": "busy",
            "passed": 0,
            "total": 0,
            "detail": f"Grader busy — all {GRADE_CONCURRENCY} slot(s) in use for {QUEUE_WAIT_SEC}s",
        }
    try:
        proc = subprocess.run(
            [
                JAVA,
                "-Xmx256m",
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
            "status": "error",
            "reason": "timeout",
            "passed": 0,
            "total": 0,
            "detail": f"Grader timed out after {TIMEOUT_SEC}s",
        }
    except FileNotFoundError:
        return {
            "status": "error",
            "reason": "java_missing",
            "passed": 0,
            "total": 0,
            "detail": f"Java executable '{JAVA}' not found",
        }
    finally:
        _grade_sem.release()

    stdout = _decode_subprocess(proc.stdout)
    stderr = _decode_subprocess(proc.stderr)
    passed = len(re.findall(r":\s*passed", stdout))
    failed = len(re.findall(r":\s*failed", stdout))
    total = passed + failed

    if total == 0:
        # Digital produced no parseable test results — the circuit could not be
        # evaluated (corrupt / unsupported .dig, or a JVM/classpath fault). This
        # is NOT a wrong answer. Keep the raw output server-side only; it can
        # carry absolute paths and Java stack traces, so it must never reach
        # the mentee.
        return {
            "status": "error",
            "reason": "grader_error",
            "passed": 0,
            "total": 0,
            "detail": (stderr.strip() or stdout.strip())[:1500],
        }

    # Real grading happened. Surface only the per-test pass/fail summary
    # (stdout); never stderr, which can leak server paths and stack traces.
    return {
        "status": "graded",
        "passed": passed,
        "total": total,
        "detail": stdout.strip()[:1500],
    }


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
