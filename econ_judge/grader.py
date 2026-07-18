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

# Optional canonical sub-circuits seeded next to the submission so Digital can
# resolve imports when a participant chooses to reuse an earlier circuit.
# Gate-only submissions are equally valid; no structure rule requires these
# components. Nested dependencies are included because 08_full_adder.dig itself
# imports 07_half_adder.dig.
CANONICAL_SUBCIRCUITS = {
    8: ["07_half_adder.dig"],
    9: ["07_half_adder.dig", "08_full_adder.dig"],
    11: ["07_half_adder.dig", "08_full_adder.dig"],
    13: ["12_at_least_one.dig"],
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


_FAN_IN_GATES = {"And", "Or", "NAnd", "NOr", "XOr", "XNOr"}
_STRICT_COMPONENTS = {
    3: ({"In", "Out", "And", "Text"}, None),
    5: ({"In", "Out", "NAnd", "Text"}, 1),
    6: ({"In", "Out", "NAnd", "Text"}, 3),
}
_SEVEN_SEGMENT_LABELS = ("a", "b", "c", "d", "e", "f", "g", "dp")
_SEVEN_SEGMENT_PIN_OFFSETS = {
    "a": (0, 0),
    "b": (20, 0),
    "c": (40, 0),
    "d": (60, 0),
    "e": (0, 140),
    "f": (20, 140),
    "g": (40, 140),
    "dp": (60, 140),
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
        return "a, b, c, d, e, f, g, dp 출력 단자를 각각 정확히 1개 배치해야 합니다."

    graph = _wire_graph(root)
    display_x, display_y = display_position
    for label in _SEVEN_SEGMENT_LABELS:
        offset_x, offset_y = _SEVEN_SEGMENT_PIN_OFFSETS[label]
        display_pin = (display_x + offset_x, display_y + offset_y)
        if not _same_net(graph, display_pin, outputs[label][0]):
            return f"출력 {label}을 Seven-Seg 부품의 {label} 입력에 연결해야 합니다."
    return None


def _validate_structure(challenge_id: int, submission_path: str):
    """Return a participant-safe structural-rule error, or ``None``."""
    try:
        root = ET.parse(submission_path).getroot()
    except (ET.ParseError, OSError):
        # Digital will return the ordinary malformed-circuit error later.
        return None

    elements = root.findall(".//visualElement")
    names = []
    for element in elements:
        name_node = element.find("elementName")
        name = name_node.text if name_node is not None else ""
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

    structure_error = _validate_structure(challenge_id, submission_path)
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
