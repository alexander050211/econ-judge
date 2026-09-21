import os
import re
import shutil
import subprocess
import threading
import xml.etree.ElementTree as ET
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

# Do not seed reference circuits into contestant submissions. A component must
# be included in the contestant's selected round folder, so components cannot
# silently cross a round boundary.
CANONICAL_SUBCIRCUITS: dict[int, list[str]] = {}


def _seed_canonical(challenge_id: int, working_dir: Path) -> list[str]:
    """Copy canonical sub-circuits for this challenge into the submission's
    working directory. Returns the list of filenames missing from CANONICAL_DIR
    (empty if all expected files were copied)."""
    missing: list[str] = []
    for filename in CANONICAL_SUBCIRCUITS.get(challenge_id, []):
        src = CANONICAL_DIR / filename
        destination = working_dir / filename
        # A submitted folder may intentionally contain a team-built component
        # with the same filename. It must win over our legacy fallback copy.
        if destination.exists():
            continue
        if not src.exists():
            missing.append(filename)
            continue
        shutil.copy(src, destination)
    return missing


# ── Expected testcase count ──────────────────────────────────────────────
# secret_tests/<id>.dig is the authority on how many rows a challenge is graded
# over: tests/generate_secret_tests.py writes exactly one visualElement whose
# elementName is "Testcase" per graded row. The denominator must come from here
# and never from tallying Digital's output lines — a run that errored on some
# rows, or that died halfway, prints fewer lines and would otherwise look
# short-but-perfect.
#
# Cached on (mtime_ns, size), not on the path alone: a redeployed image ships
# regenerated secret tests, and a warm worker must not answer from a stale
# count. A race between two graders costs at most a redundant parse.
_EXPECTED_COUNT_CACHE: dict[str, tuple[tuple[int, int], int]] = {}


def expected_testcase_count(challenge_id: int):
    """Return how many testcases challenge `challenge_id` is graded over, or
    ``None`` if its secret test file is missing or unreadable."""
    test_file = SECRET_TESTS_DIR / f"{challenge_id}.dig"
    try:
        stamp = test_file.stat()
    except OSError:
        return None
    key = str(test_file)
    version = (stamp.st_mtime_ns, stamp.st_size)
    cached = _EXPECTED_COUNT_CACHE.get(key)
    if cached is not None and cached[0] == version:
        return cached[1]
    try:
        root = ET.parse(test_file).getroot()
    except (ET.ParseError, OSError):
        return None
    count = sum(
        1
        for element in root.findall(".//visualElement")
        if element.findtext("elementName", default="") == "Testcase"
    )
    _EXPECTED_COUNT_CACHE[key] = (version, count)
    return count


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


_FAN_IN_GATES = {"And", "Or", "NAnd", "NOr", "XOr", "XNOr"}
_STRICT_COMPONENTS = {
    3: ({"In", "Out", "And", "Text"}, None),
    5: ({"In", "Out", "NAnd", "Text"}, 1),
    6: ({"In", "Out", "NAnd", "Text"}, 3),
}
_SEVEN_SEGMENT_LABELS = ("a", "b", "c", "d", "e", "f", "g")
_SEVEN_SEGMENT_PIN_OFFSETS = {
    "a": (0, 0),
    "b": (20, 0),
    "c": (40, 0),
    "d": (60, 0),
    "e": (0, 140),
    "f": (20, 140),
    "g": (40, 140),
}


def _input_count(element) -> int:
    for entry in element.findall("./elementAttributes/entry"):
        key = entry.find("string")
        if key is None or key.text != "Inputs":
            continue
        value = entry.find("int")
        if value is not None and value.text:
            try:
                return int(value.text)
            except ValueError:
                return 2
    return 2


def _attribute(element, name: str):
    for entry in element.findall("./elementAttributes/entry"):
        values = list(entry)
        if not values or values[0].tag != "string" or values[0].text != name:
            continue
        return values[1].text if len(values) > 1 else ""
    return None


def _position(node):
    if node is None:
        return None
    try:
        return int(node.get("x")), int(node.get("y"))
    except (TypeError, ValueError):
        return None


def _wire_graph(root):
    graph: dict[tuple[int, int], set[tuple[int, int]]] = {}
    for wire in root.findall("./wires/wire"):
        first = _position(wire.find("p1"))
        second = _position(wire.find("p2"))
        if first is None or second is None:
            continue
        graph.setdefault(first, set()).add(second)
        graph.setdefault(second, set()).add(first)
    return graph


def _same_net(graph, first, second) -> bool:
    if first == second:
        return True
    pending = [first]
    visited = {first}
    while pending:
        point = pending.pop()
        for neighbor in graph.get(point, ()):
            if neighbor == second:
                return True
            if neighbor not in visited:
                visited.add(neighbor)
                pending.append(neighbor)
    return False


def _validate_seven_segment(root, elements):
    displays = [
        element for element in elements
        if element.findtext("elementName", default="") == "Seven-Seg"
    ]
    if len(displays) != 1:
        return "Seven-Seg 부품을 정확히 1개 사용해야 합니다."

    display_position = _position(displays[0].find("pos"))
    if display_position is None:
        return "Seven-Seg 부품의 위치 정보를 읽을 수 없습니다."

    outputs: dict[str, list[tuple[int, int]]] = {
        label: [] for label in _SEVEN_SEGMENT_LABELS
    }
    for element in elements:
        if element.findtext("elementName", default="") != "Out":
            continue
        label = _attribute(element, "Label")
        if label not in outputs:
            continue
        position = _position(element.find("pos"))
        if position is not None:
            outputs[label].append(position)

    invalid_labels = [label for label, positions in outputs.items() if len(positions) != 1]
    if invalid_labels:
        return "a, b, c, d, e, f, g 출력 단자를 각각 정확히 1개 배치해야 합니다."

    graph = _wire_graph(root)
    display_x, display_y = display_position
    for label in _SEVEN_SEGMENT_LABELS:
        offset_x, offset_y = _SEVEN_SEGMENT_PIN_OFFSETS[label]
        display_pin = (display_x + offset_x, display_y + offset_y)
        if not _same_net(graph, display_pin, outputs[label][0]):
            return f"출력 {label}을 Seven-Seg 부품의 {label} 입력에 연결해야 합니다."
    return None


def _validate_structure(
    challenge_id: int,
    submission_path: str,
    dependency_paths: tuple[str, ...] = (),
):
    """Return a participant-safe structural-rule error, or ``None``."""
    try:
        root = ET.parse(submission_path).getroot()
    except (ET.ParseError, OSError):
        # Digital will return the ordinary malformed-circuit error later.
        return None

    elements = root.findall(".//visualElement")
    names = []
    for circuit_path in (submission_path, *dependency_paths):
        if circuit_path == submission_path:
            # Parsed above; an unparseable submission already deferred to Digital.
            circuit_elements = elements
        else:
            try:
                circuit_root = ET.parse(circuit_path).getroot()
            except (ET.ParseError, OSError):
                return "함께 제출한 부품 파일을 Digital에서 읽을 수 없습니다."
            circuit_elements = circuit_root.findall(".//visualElement")
        for element in circuit_elements:
            name_node = element.find("elementName")
            name = name_node.text if name_node is not None else ""
            if circuit_path == submission_path:
                names.append(name)
            if name in _FAN_IN_GATES and _input_count(element) > 2:
                return "입력이 2개보다 많은 논리 게이트는 사용할 수 없습니다."

    if challenge_id == 15:
        return _validate_seven_segment(root, elements)

    rule = _STRICT_COMPONENTS.get(challenge_id)
    if rule is None:
        return None

    allowed, exact_nand_count = rule
    disallowed = sorted({name for name in names if name and name not in allowed})
    if disallowed:
        if challenge_id in (5, 6):
            return "이 문제에서는 NAND 게이트 외의 논리 부품을 사용할 수 없습니다."
        return "이 문제에서는 2입력 AND 게이트만 사용할 수 있습니다."

    if exact_nand_count is not None and names.count("NAnd") != exact_nand_count:
        return f"NAND 게이트를 정확히 {exact_nand_count}개 사용해야 합니다."
    return None


# Digital prints one line per testcase, and a THIRD class of line that matches
# neither ": passed" nor ": failed" — the row could not be evaluated at all.
# Such lines are invisible to both counters, so a run carrying them reports
# fewer graded rows than the test file declares. These patterns recognise the
# CLASS of that line and nothing more: the matched text, the case label and the
# case index must never reach a contestant, since pinpointing the failing row
# is exactly what tests/generate_secret_tests.py keeps out of every label.
_INCOMPLETE_CLASSES = (
    (
        re.compile(r"Component .* not found"),
        "회로에서 사용한 부품 파일을 찾을 수 없습니다. 필요한 부품 .dig 파일이 "
        "같은 라운드 폴더 안에 있는지 확인한 뒤 폴더를 다시 선택해 제출해주세요.",
    ),
    (
        re.compile(
            r"Nothing connected to input"
            r"|Open inputs are not allowed"
            r"|No output connected to a wire"
        ),
        "연결되지 않은 입력 또는 출력 단자가 있습니다. 모든 단자가 회로에 "
        "연결되어 있는지 확인해주세요.",
    ),
    (
        re.compile(r"Test signal .* not found"),
        "문제에서 정한 입력·출력 단자 이름을 회로에서 찾을 수 없습니다. "
        "단자 이름(Label)을 문제지와 똑같이 맞춰주세요.",
    ),
)


def _incomplete_class_message(stdout: str):
    """Return a participant-safe Korean message for a run that could not grade
    every row, derived from the error-message CLASS alone, or ``None`` when the
    output matches no known class."""
    for pattern, message in _INCOMPLETE_CLASSES:
        if pattern.search(stdout):
            return message
    return None


# ── Verdict lines ────────────────────────────────────────────────────────
# Digital fills one output line per testcase: "<case label> #<n>: passed",
# "<case label> #<n>: failed …", or — for a row it could not evaluate at all —
# the exception text in that same slot. So everything after that first colon
# may be an exception message, and some of those messages quote a string taken
# straight out of the SUBMITTED circuit: a missing custom component is reported
# as "Component <filename> not found", with the filename coming from the
# uploaded .dig. The tail of an error line is therefore CONTESTANT-CONTROLLED
# text, and a substring search for ": passed" would count it. Two rules keep a
# crafted name from passing for a verdict:
#   * a verdict must be a WHOLE line, so an error line that merely contains
#     ": passed" is not one (the missing-component message always continues
#     with " not found"); and
#   * the part before the colon must itself be colon-free, so text appended
#     after Digital's own "<label> #<n>: " prefix can never complete one.
# Contestant text carrying a NEWLINE can still forge a line satisfying both,
# which is what `unmatched` is for — see grade_submission's all-pass checks.
_PASSED_LINE = re.compile(r"^[^:\n]*:[ \t]*passed[ \t\r]*$")
# "failed" is followed by Digital's reason ("failed (100%)", "failed due to an
# error"), so only the start of the verdict is anchored; the lookahead just
# keeps "failedX" from counting.
_FAILED_LINE = re.compile(r"^[^:\n]*:[ \t]*failed(?![^\s])")


def _verdict_counts(stdout: str) -> tuple[int, int, int]:
    """Count (passed, failed, unmatched) lines in Digital's stdout.

    `unmatched` is every other non-blank line: per-case error text and its
    continuation lines, the "Tests have failed." trailer, and anything a
    contestant-controlled string smuggled onto a line of its own."""
    passed = failed = unmatched = 0
    for line in stdout.splitlines():
        if not line.strip():
            continue
        if _PASSED_LINE.match(line):
            passed += 1
        elif _FAILED_LINE.match(line):
            failed += 1
        else:
            unmatched += 1
    return passed, failed, unmatched


def grade_submission(
    challenge_id: int,
    submission_path: str,
    dependency_paths: tuple[str, ...] = (),
) -> dict:
    test_file = SECRET_TESTS_DIR / f"{challenge_id}.dig"
    if not test_file.exists():
        return {
            "status": "error",
            "reason": "no_test",
            "passed": 0,
            "total": 0,
            "detail": f"No secret test file configured for challenge {challenge_id}",
        }

    expected = expected_testcase_count(challenge_id)
    if not expected:
        # ``None`` and 0 are different faults needing different repairs — a
        # file that would not parse (truncated, corrupt) versus one that parsed
        # and declares no rows (a generator spec with an empty row list). Name
        # the one that happened, the way health.secret_tests_check separates
        # its "unreadable" and "no Testcase elements" lists.
        return {
            "status": "error",
            "reason": "misconfigured",
            "passed": 0,
            "total": 0,
            "detail": (
                f"Secret test file {test_file} is unreadable "
                f"(could not be read or parsed as XML)"
                if expected is None
                else f"Secret test file {test_file} declares no Testcase elements"
            ),
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

    for circuit_path in (submission_path, *dependency_paths):
        danger = _scan_dangerous_xml(circuit_path)
        if danger:
            return {
                "status": "rejected",
                "reason": "unsafe_xml",
                "passed": 0,
                "total": 0,
                "detail": danger,
            }

    structure_error = _validate_structure(
        challenge_id, submission_path, dependency_paths
    )
    if structure_error:
        return {
            "status": "invalid",
            "reason": "structure",
            "passed": 0,
            "total": 0,
            "detail": structure_error,
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
    passed, failed, unmatched = _verdict_counts(stdout)
    reported = passed + failed

    # Four inputs decide the verdict: the two verdict-line counts, the count of
    # lines that are neither, the exit code, and the expected count. A run is a
    # graded verdict ONLY when it printed one pass/fail line per expected
    # testcase — anything shorter (rows that hit a per-case error line, or a
    # JVM killed mid-run on the 512MB tier) must not become a score, because
    # `failed` stays 0 and the run then looks perfect.
    #
    # An all-pass run is held to two further checks, since it is the only
    # verdict that awards a Solve. It must have printed NOTHING but its pass
    # lines: a genuine all-pass run is exactly one line per case with no
    # trailer, while a forged pass line (contestant text smuggling a newline
    # into an error message; see _PASSED_LINE) always leaves the row's real
    # output on stdout beside it. And it must have exited 0 — corroboration
    # only, never a gate on a wrong answer: Digital exits with the count of
    # non-passing cases, which a POSIX exit status truncates to one byte, so a
    # 256-row all-fail run exits 0 and this check alone would wave it through.
    if (
        reported != expected
        or (passed == expected and (unmatched or proc.returncode != 0))
    ):
        # Raw output can carry absolute paths, Java stack traces and the label
        # of the offending case, so it stays server-side in every branch here.
        detail = (
            f"incomplete run: exit={proc.returncode} passed={passed} "
            f"failed={failed} other={unmatched} expected={expected}\n"
            f"{stderr.strip() or stdout.strip()}"
        )[:1500]
        message = _incomplete_class_message(stdout)
        if message:
            # The submitted circuit (or the uploaded folder) is why the run
            # could not finish, so this is a wrong submission rather than a
            # grader fault. `detail` is the hand-written, class-only Korean
            # message that the mentee sees; `log_detail` is operator-only.
            return {
                "status": "invalid",
                "reason": "incomplete_run",
                "passed": 0,
                "total": 0,
                "detail": message,
                "log_detail": detail,
            }
        return {
            "status": "error",
            # No recognised per-case error line: either Digital produced no
            # parseable result at all (corrupt / unsupported .dig, or a
            # JVM/classpath fault) or it stopped partway through — a truncated
            # run, e.g. an OOM-kill. Neither is a wrong answer.
            "reason": "grader_error" if reported == 0 else "incomplete",
            "passed": 0,
            "total": 0,
            "detail": detail,
        }

    # Real grading happened over every expected row. `total` is the expected
    # count taken from the secret test file — never a tally of output lines —
    # so `passed == total` cannot be satisfied by a short run. Surface only the
    # per-test pass/fail summary (stdout); never stderr, which can leak server
    # paths and stack traces.
    return {
        "status": "graded",
        "passed": passed,
        "total": expected,
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
