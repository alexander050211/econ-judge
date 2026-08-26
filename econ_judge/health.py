"""Readiness probes for the grading path.

Java, Digital.jar and secret_tests/ are three independently built artifacts,
and nothing exercises them together until a contestant submits a circuit
during a timed round. These checks answer "can this container actually
grade?" ahead of that moment: a structural half that touches no JVM and no
database, so a host health check can poll it, and an opt-in deep half that
grades one committed reference circuit end to end.

Nothing here imports CTFd — the checks are about the grader's own artifacts,
so this module stays importable wherever grader.py is.
"""

from __future__ import annotations

import datetime
import os
import shutil
import tempfile
import time
from pathlib import Path

from .competition import competition_status
from .grader import (
    DIGITAL_JAR,
    GRADE_CONCURRENCY,
    JAVA,
    QUEUE_WAIT_SEC,
    REPO_ROOT,
    SECRET_TESTS_DIR,
    TIMEOUT_SEC,
    expected_testcase_count,
    grade_submission,
)
from .problemset import HWP_STARTER_FILES

# Exactly the challenges that are answered by uploading a .dig, which is the
# same thing as "needs a file in SECRET_TESTS_DIR". The truth-table challenge
# (problemset.TRUTH_TABLE_CHALLENGE_ID) is answered in the browser and graded
# in endpoints.py against TRUTH_TABLE_EXPECTED, so it has no secret test and
# must not be reported as missing one.
GRADED_CHALLENGE_IDS = tuple(sorted(HWP_STARTER_FILES))

# Fixtures for the deep probe. Challenges 5 and 15 are the two 2-testcase
# problems, and both reference circuits are self-contained — no sibling
# component .dig — so the probe stages a single file, the way a one-file
# submission does.
DEEP_PROBE_CHALLENGE_IDS = (5, 15)

# solutions/ is NOT copied into the Docker image (see the Dockerfile's COPY
# list), so on a deployed container this directory does not exist and the deep
# probe reports "fixture_unavailable" rather than failing. The env var is the
# hook for shipping a fixture without changing code.
SOLUTIONS_DIR = Path(
    os.environ.get(
        "ECON_JUDGE_SOLUTIONS_DIR", REPO_ROOT / "solutions" / "2026-summer"
    )
)


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digital_jar_check() -> dict:
    """Digital.jar is present and non-empty.

    The size is reported because the usual failure is not absence but a
    truncated download: the Dockerfile fetches the release zip at build time,
    and a partial unzip leaves a file that exists and cannot run.
    """
    path = Path(DIGITAL_JAR)
    try:
        size = path.stat().st_size
    except OSError:
        size = None
    ok = bool(size)
    return {
        "ok": ok,
        "path": str(path),
        "size_bytes": size,
        "detail": None if ok else f"Digital.jar missing or empty at {path}",
    }


def java_check() -> dict:
    """The java binary resolves.

    Deliberately only a PATH lookup: `java -version` would spend the very
    resource grader.py's semaphore exists to ration — a ~400MB JVM — on every
    health poll. Proving java actually runs is the deep probe's job.
    """
    resolved = shutil.which(JAVA)
    return {
        "ok": resolved is not None,
        "command": JAVA,
        "resolved": resolved,
        "detail": None if resolved else f"java executable '{JAVA}' not found on PATH",
    }


def secret_tests_check() -> dict:
    """Every submittable challenge has a secret test with at least one row.

    A challenge whose file is missing, unreadable or empty cannot be graded at
    all — grade_submission returns a "no_test"/"misconfigured" error and the
    mentee is told the problem is not ready. The per-challenge counts are the
    denominators every score is computed against, so they are reported in full.
    """
    counts: dict[str, int | None] = {}
    missing: list[int] = []
    unreadable: list[int] = []
    empty: list[int] = []
    for challenge_id in GRADED_CHALLENGE_IDS:
        test_file = SECRET_TESTS_DIR / f"{challenge_id}.dig"
        count = expected_testcase_count(challenge_id)
        counts[str(challenge_id)] = count
        if not test_file.exists():
            missing.append(challenge_id)
        elif count is None:
            unreadable.append(challenge_id)
        elif count < 1:
            empty.append(challenge_id)
    problems = []
    if missing:
        problems.append(f"missing: {missing}")
    if unreadable:
        problems.append(f"unreadable: {unreadable}")
    if empty:
        problems.append(f"no Testcase elements: {empty}")
    return {
        "ok": not problems,
        "dir": str(SECRET_TESTS_DIR),
        "expected_challenge_ids": list(GRADED_CHALLENGE_IDS),
        "testcase_counts": counts,
        "missing_ids": missing,
        "unreadable_ids": unreadable,
        "empty_ids": empty,
        "detail": None if not problems else "; ".join(problems),
    }


def tuning() -> dict:
    """The effective ECON_JUDGE_* values — after grader.py's env parsing, not
    as typed in the dashboard. A malformed number is silently replaced by the
    default there, so the dashboard is not evidence of what is running.

    Reported only on structural_report(verbose=True): server paths, and the
    grading budget an attacker would otherwise have to measure."""
    return {
        "digital_jar": str(DIGITAL_JAR),
        "secret_tests_dir": str(SECRET_TESTS_DIR),
        "java": JAVA,
        "timeout_sec": TIMEOUT_SEC,
        "grade_concurrency": GRADE_CONCURRENCY,
        "queue_wait_sec": QUEUE_WAIT_SEC,
        # A queued grade's wait is additive to its subprocess budget against
        # gunicorn's 60s request timeout (see render.yaml), so this is the
        # worst case a single request can spend inside the grader.
        "worst_case_request_sec": QUEUE_WAIT_SEC + TIMEOUT_SEC,
    }


def structural_report(verbose: bool = False) -> dict:
    """The cheap half: artifacts and schedule, no JVM and no database.

    Only the three artifact checks gate `ok`. The schedule is reported but
    never gates it: a bad ECON_JUDGE_COMPETITION_START stops submissions, yet
    failing a host health check over it would restart-loop the service and
    take the whole site down as well — `competition.phase == "misconfigured"`
    is the field to alert on instead.

    `verbose` is for ADMIN CALLERS ONLY. The route this feeds is public and
    unauthenticated (endpoints.digital_health), so the default report names
    each check and whether it passed and nothing more: the full check dicts
    carry absolute server paths, the resolved java binary and the
    secret-tests directory, and tuning() carries the grading budget —
    GRADE_CONCURRENCY together with the worst-case seconds one request can
    spend in the grader is a map of exactly the bottleneck that semaphore
    exists to protect. That is the same reason deep_probe's raw Digital
    output is admin-gated. `competition` is in both halves: it is already
    served in full to anyone on /api/v1/digital/competition.
    """
    checks = {
        "digital_jar": digital_jar_check(),
        "java": java_check(),
        "secret_tests": secret_tests_check(),
    }
    failed = [name for name, check in checks.items() if not check["ok"]]
    report = {
        "ok": not failed,
        "failed_checks": failed,
        "checks": (
            checks
            if verbose
            else {name: {"ok": check["ok"]} for name, check in checks.items()}
        ),
        "competition": competition_status(),
    }
    if verbose:
        report["tuning"] = tuning()
    report["generated_at"] = _utc_now_iso()
    return report


def deep_probe(challenge_id) -> dict:
    """Grade one committed reference circuit and report the wall-clock cost.

    This goes through grade_submission rather than invoking Java itself, so it
    acquires the same grading semaphore a contestant's submission does, with
    the same QUEUE_WAIT_SEC bound: an operator refreshing a health page queues
    behind a real grade and gets a "busy" result instead of taking the single
    permit away from it.

    Every failure path returns a result dict — an unsupported id, a missing
    fixture, an unreadable file. Nothing here raises.
    """
    if challenge_id not in DEEP_PROBE_CHALLENGE_IDS:
        return {
            "ok": False,
            "status": "unsupported_challenge",
            "challenge_id": challenge_id,
            "detail": (
                "deep probe runs challenge "
                f"{' or '.join(str(i) for i in DEEP_PROBE_CHALLENGE_IDS)} only"
            ),
        }

    fixture = SOLUTIONS_DIR / HWP_STARTER_FILES[challenge_id]
    if not fixture.is_file():
        return {
            "ok": False,
            "status": "fixture_unavailable",
            "challenge_id": challenge_id,
            "fixture": str(fixture),
            "detail": (
                "reference circuit not available in this image; solutions/ is "
                "not copied into the Docker image. Point "
                "ECON_JUDGE_SOLUTIONS_DIR at a directory that ships one."
            ),
        }

    expected = expected_testcase_count(challenge_id)
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory() as working_dir:
            staged = Path(working_dir) / fixture.name
            shutil.copy(fixture, staged)
            result = grade_submission(challenge_id, str(staged))
    except OSError as exc:
        return {
            "ok": False,
            "status": "error",
            "challenge_id": challenge_id,
            "fixture": str(fixture),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "detail": f"{type(exc).__name__}: {exc}",
        }
    # Wall clock, deliberately measured around the whole call: it includes any
    # time spent queued for a grading slot, which is exactly what a
    # contestant's submission would have experienced at this moment.
    elapsed_ms = int((time.monotonic() - started) * 1000)

    grader_status = result.get("status")
    reason = result.get("reason")
    passed = int(result.get("passed") or 0)
    total = int(result.get("total") or 0)
    # A pass demands the same three-way agreement the real Solve path demands
    # (endpoints.digital_attempt): graded, every row passed, and the row count
    # equal to what the secret test file declares.
    ok = bool(
        grader_status == "graded" and expected and total == expected and passed == total
    )
    if reason == "busy":
        status = "busy"
    elif ok:
        status = "passed"
    elif grader_status == "graded":
        status = "failed"
    else:
        status = "error"
    return {
        "ok": ok,
        "status": status,
        "challenge_id": challenge_id,
        "fixture": str(fixture),
        "expected_testcases": expected,
        "passed": passed,
        "total": total,
        "elapsed_ms": elapsed_ms,
        "grader_status": grader_status,
        "grader_reason": reason,
        # Raw Digital output: carries testcase labels and server paths, which
        # is why every caller of this function is admin-gated.
        "detail": (result.get("detail") or "")[:500],
    }
