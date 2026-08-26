import csv
import datetime
import io
import json
import ntpath
import os
import tempfile
import threading
import time
import unicodedata
from pathlib import Path, PurePosixPath

from flask import Response, abort, jsonify, request
from sqlalchemy import func

from CTFd.models import Challenges, Fails, Solves, Users, db
from CTFd.plugins import bypass_csrf_protection
from CTFd.utils import get_config
from CTFd.utils.decorators import admins_only, authed_only
from CTFd.utils.user import get_current_team, get_current_user, get_ip

from .concepts import concept_info
from .competition import ALL_CHALLENGE_IDS, ROUND_INFO, competition_status, current_phase
from .grader import expected_testcase_count, grade_submission
from .health import DEEP_PROBE_CHALLENGE_IDS, deep_probe, structural_report
from .problemset import (
    HWP_STARTER_FILES,
    TRUTH_TABLE_CHALLENGE_ID,
    TRUTH_TABLE_EXPECTED,
    normalize_truth_table_answers,
)

MAX_UPLOAD_BYTES = 256 * 1024
MAX_BUNDLE_FILES = 32
MAX_BUNDLE_BYTES = 1024 * 1024


def _uploaded_circuit_files():
    """Return the files selected from one contestant round folder."""
    return [item for item in request.files.getlist("files") if item.filename]


def _stage_digital_bundle(challenge_id: int, working_dir: str):
    """Stage one round-starter folder and return its answer plus dependencies.

    Digital resolves custom components by filename beside the main circuit. The
    browser therefore sends every `.dig` in the selected starter folder; this
    helper flattens that one folder into the isolated grading directory.
    """
    uploads = _uploaded_circuit_files()
    if not uploads:
        return None, (), "", "제출할 라운드 starters 폴더를 선택해주세요."
    if len(uploads) > MAX_BUNDLE_FILES:
        return None, (), "", "제출 폴더의 .dig 파일이 너무 많습니다."

    starter_path = PurePosixPath(HWP_STARTER_FILES.get(challenge_id, ""))
    # HWP_STARTER_FILES is NFC-normalized Hangul, but a macOS browser sends the
    # NFD-decomposed form in multipart filenames. Both sides are folded to NFC
    # before every filename comparison below, otherwise a folder that really
    # does hold the answer file is reported as missing it.
    expected = unicodedata.normalize("NFC", starter_path.name)
    if not expected:
        return None, (), "", "이 문제의 제출 파일 구성을 찾을 수 없습니다."

    staged: dict[str, str] = {}
    total_size = 0
    for upload in uploads:
        relative_path = PurePosixPath((upload.filename or "").replace("\\", "/"))
        if (
            relative_path.is_absolute()
            or len(relative_path.parts) != 2
        ):
            return None, (), "", (
                "\ud3f4\ub354 \ud558\ub098\ub9cc \uc120\ud0dd\ud574\uc8fc\uc138\uc694. "
                "\ud558\uc704 \ud3f4\ub354\ub294 \uc0ac\uc6a9\ud560 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4."
            )
        filename = relative_path.name
        # Path containment lives HERE and nowhere else: only `relative_path.name`
        # — one path component — is ever joined onto working_dir. The two-segment
        # check above is a folder-shape rule for the UI, NOT the security
        # boundary, so it cannot stand in for this guard. `.name` is a POSIX-only
        # split, and on Windows os.path.join(tmpdir, "D:evil.dig") returns
        # "D:evil.dig" and escapes the directory, so a drive-qualified or
        # separator-bearing name is rejected outright. (werkzeug's
        # secure_filename is not an option: it strips Hangul, which would erase
        # every legitimate submission filename.)
        if (
            not filename
            or filename in (".", "..")
            or filename != ntpath.basename(filename)
        ):
            return None, (), "", "제출 파일 이름을 사용할 수 없습니다. 파일 이름을 확인해주세요."
        key = unicodedata.normalize("NFC", filename)
        if not key.lower().endswith(".dig"):
            return None, (), "", "starters 폴더의 .dig 파일만 제출할 수 있습니다."
        if key in staged:
            return None, (), "", "같은 이름의 .dig 파일은 함께 제출할 수 없습니다."

        upload.seek(0, os.SEEK_END)
        size = upload.tell()
        upload.seek(0)
        if size == 0:
            return None, (), "", f"{filename} 파일이 비어 있습니다."
        if size > MAX_UPLOAD_BYTES:
            return None, (), "", f"{filename} 파일이 너무 큽니다."
        total_size += size
        if total_size > MAX_BUNDLE_BYTES:
            return None, (), "", "제출 폴더의 파일 용량이 너무 큽니다."

        # Staged under the NFC name: that is the form every Korean filename in
        # this repo already uses and the only one this stack is known to grade.
        destination = os.path.join(working_dir, key)
        upload.save(destination)
        staged[key] = destination
        if filename != key:
            # A .dig authored on a machine that decomposes Hangul names its
            # sibling components in that same form, and Digital resolves a
            # component by exact filename. Keep a second copy under the name the
            # browser sent so the reference resolves either way; it is byte
            # identical to the copy above, so it adds nothing to validate.
            upload.seek(0)
            upload.save(os.path.join(working_dir, filename))

    submission_path = staged.get(expected)
    if submission_path is None:
        return None, (), "", f"선택한 폴더에 이 문제의 답안 파일({expected})이 없습니다."
    dependencies = tuple(
        path for filename, path in staged.items() if filename != expected
    )
    return submission_path, dependencies, expected, None


def _reject(message: str):
    return jsonify(
        {"success": True, "data": {"status": "incorrect", "message": message}}
    )


def _submission_window_reject(challenge_id: int):
    """Do not grade or record a Fail outside the active competition window."""
    phase = current_phase()
    if phase.submissions_open and challenge_id in phase.visible_challenge_ids:
        return None
    message = phase.message or "지금은 이 문제를 제출할 수 없습니다."
    return jsonify(
        {
            "success": True,
            "data": {"status": "unavailable", "message": message},
        }
    )


# Mentee-facing messages for grader system/config errors. These are NOT wrong
# answers — they are logged server-side and never record a Fail. The raw grader
# detail is deliberately kept out of these strings (it can carry server paths
# and Java stack traces).
_ERROR_MESSAGES = {
    "no_test": "이 문제는 아직 채점 준비가 되지 않았습니다. 운영진에게 알려주세요.",
    "misconfigured": "채점기 설정 오류입니다. 운영진에게 알려주세요.",
    "timeout": "채점 시간이 초과되었습니다 (회로가 너무 크거나 멈춰 있을 수 있어요). 회로를 점검한 뒤 다시 제출해주세요.",
    "java_missing": "채점기를 일시적으로 사용할 수 없습니다. 잠시 후 다시 시도해주세요.",
    "grader_error": "회로를 평가할 수 없습니다. Digital에서 정상적으로 열리는지, 지원되는 부품만 사용했는지 확인해주세요.",
    "incomplete": "채점이 끝까지 진행되지 않았습니다. 잠시 후 다시 제출해주세요. 같은 현상이 반복되면 운영진에게 알려주세요.",
    "unsafe_xml": "안전하지 않은 XML 구문이 포함되어 파일이 거부되었습니다.",
    "busy": "지금 채점이 몰려 있습니다. 잠시 후 다시 제출해주세요.",
}


# ── One-attempt guard ─────────────────────────────────────────────────
# The truth-table problem is graded once per user, enforced by a
# check-then-insert. Two concurrent POSTs from the same team could both pass
# the check and each insert a row, so that critical section runs under this
# lock.
#
# PER-PROCESS: like grader.py's semaphore, this lock lives in one worker
# process, so the one-attempt guarantee holds only with WEB_WORKERS=1
# (bin/entrypoint.sh default). Under gunicorn's gevent worker `threading` is
# monkey-patched, so a waiting request yields the event loop instead of
# blocking it. With N workers this would need a DB-level unique constraint.
#
# NOTHING CLIENT-PACED MAY RUN UNDER IT. The request body is read before the
# `with`, never inside it: request.get_json() pulls from a streaming
# wsgi.input, and this route carries @bypass_csrf_protection so nothing has
# touched the body first. A client that stalls between headers and body parks
# its greenlet mid-read — which yields the event loop but KEEPS this lock —
# and gunicorn's --timeout is a worker heartbeat for async workers, not a
# per-request deadline, so nothing cuts the socket. One sleeping laptop would
# otherwise lock this challenge for every other team until TCP keepalive
# gives up, roughly two hours later.
_truth_table_lock = threading.Lock()


# ── Projector category mapping ────────────────────────────────────────
# Project-phase challenge ids — fixed by the camp problem set. Order = column
# order in the projector matrix; `short` is the compact HWP part label and
# `name` must stay in sync with CHALLENGES in tests/register_challenges.py
# (the same list bootstrap.py loads).
_PROJECT_PHASE_COLS = [
    {"id": 12, "short": "A-1", "name": "1 이상 판정"},
    {"id": 13, "short": "A-2", "name": "홍수 경보 판단"},
    {"id": 14, "short": "B",   "name": "홍수 위험 지역 판단"},
    {"id": 15, "short": "C",   "name": "7-segment 출력기"},
]


def _freeze_state():
    """Read CTFd's `freeze` config (Unix timestamp string). Return
    (frozen_bool, freeze_ts_or_none, date_filter_list). date_filter is a
    list of SQLAlchemy clauses to splat into a Solves query so frozen
    snapshots exclude solves dated after the freeze timestamp."""
    freeze_raw = get_config("freeze")
    try:
        freeze_ts = int(freeze_raw) if freeze_raw else None
    except (TypeError, ValueError):
        freeze_ts = None
    frozen = bool(freeze_ts and time.time() >= freeze_ts)
    date_filter = []
    if frozen and freeze_ts:
        # CTFd writes Solves.date / Fails.date as NAIVE UTC, so every value
        # compared against those columns must be naive too: convert the
        # timestamp tz-aware, then drop the tzinfo.
        cutoff = datetime.datetime.fromtimestamp(
            freeze_ts, datetime.timezone.utc
        ).replace(tzinfo=None)
        date_filter.append(Solves.date < cutoff)
    return frozen, freeze_ts, date_filter


def _round_score(user_id, challenge_ids, date_filter):
    """Return a user's score and solved count for a fixed contest round.

    Challenge visibility is deliberately not a filter: Round 1 remains part of
    a team's total after the Round 2 transition hides its problems.
    """
    row = (
        db.session.query(
            func.coalesce(func.sum(Challenges.value), 0).label("score"),
            func.count(Solves.id).label("solved"),
        )
        .join(Solves, Solves.challenge_id == Challenges.id)
        .filter(
            Solves.user_id == user_id,
            Challenges.id.in_(challenge_ids),
            *date_filter,
        )
        .one()
    )
    return {"score": int(row.score or 0), "solved": int(row.solved or 0)}


def _user_round_scores(user_id, date_filter):
    return {
        key: _round_score(user_id, info["challenge_ids"], date_filter)
        for key, info in ROUND_INFO.items()
    }


def _score_total(round_scores):
    return sum(score["score"] for score in round_scores.values())


def _solved_total(round_scores):
    return sum(score["solved"] for score in round_scores.values())


def _challenge_totals():
    """Return the full online-contest totals, not only the visible round."""
    total_points = int(
        db.session.query(func.coalesce(func.sum(Challenges.value), 0))
        .filter(Challenges.id.in_(ALL_CHALLENGE_IDS))
        .scalar()
        or 0
    )
    total_challenges = int(
        db.session.query(func.count(Challenges.id))
        .filter(Challenges.id.in_(ALL_CHALLENGE_IDS))
        .scalar()
        or 0
    )
    return total_points, total_challenges


def _round_payload(round_scores):
    return {
        key: {
            "label": info["label"],
            "points": info["points"],
            "challenge_count": len(info["challenge_ids"]),
            **round_scores.get(key, {"score": 0, "solved": 0}),
        }
        for key, info in ROUND_INFO.items()
    }


# Query-parameter spellings accepted as "yes", mirroring how
# competition.rehearsal_mode reads its environment variable.
_TRUTHY_ARGS = ("1", "true", "yes", "on")


def _truthy_arg(name):
    return request.args.get(name, "").strip().lower() in _TRUTHY_ARGS


def _iso_utc(value):
    """Render a CTFd Solves/Fails timestamp as an explicit-UTC ISO-8601 string.

    Those columns hold NAIVE UTC (see _freeze_state), so the offset has to be
    attached here: an export read back months later must not leave the reader
    guessing which zone the numbers are in.
    """
    if not isinstance(value, datetime.datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value.isoformat()


def _health_response(report):
    """200 only when every structural check passed, 503 otherwise — with
    `failed_checks` naming the failures so a poller can alert on the name."""
    return jsonify({"success": report["ok"], "data": report}), (
        200 if report["ok"] else 503
    )


def _deep_probe_challenge_id():
    """Read ?challenge= for the deep probe. An unparseable value becomes 0,
    which is no challenge's id, so health.deep_probe reports it as unsupported
    rather than silently grading a different problem."""
    raw = request.args.get("challenge", "").strip()
    if not raw:
        return DEEP_PROBE_CHALLENGE_IDS[0]
    try:
        return int(raw)
    except ValueError:
        return 0


def _freeze_iso(freeze_ts):
    """Render CTFd's `freeze` config — a Unix timestamp — as an explicit-UTC
    ISO-8601 string, so the CSV carries one time format throughout."""
    if freeze_ts is None:
        return None
    try:
        return datetime.datetime.fromtimestamp(
            int(freeze_ts), datetime.timezone.utc
        ).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _export_csv(payload, generated_at):
    """Flatten the export to one row per (team, challenge).

    Each team's totals repeat on every one of its rows so the file pivots in a
    spreadsheet without a second sheet. A BOM is written because the tool a
    mentor will actually open this in is Excel, which reads a BOM-less UTF-8
    CSV in the local codepage and turns every Korean team name into mojibake.

    `frozen`/`frozen_at` repeat alongside those totals, and they carry weight
    the JSON reader gets for free at top level: the team totals ARE
    freeze-filtered while the per-challenge solved/solve_count/first_solve_at
    columns beside them are NOT, because the attempt log is the archival
    record. Summing a team's `solved` column can therefore legitimately exceed
    its `total_solved`, and without these two columns nothing in the file
    tells a mentor that apart from a bug.
    """
    round_keys = list(ROUND_INFO)
    header = [
        "user_id",
        "team_name",
        "hidden",
        "banned",
        "total_score",
        "total_solved",
        "frozen",
        "frozen_at",
    ]
    for key in round_keys:
        header += [f"{key}_score", f"{key}_solved"]
    header += [
        "challenge_id",
        "challenge_name",
        "round",
        "value",
        "solved",
        "solve_count",
        "fail_count",
        "first_solve_at",
        "last_attempt_at",
        "first_solve_ip",
    ]

    def _text(value):
        """Neutralise a leading formula character in a free-text cell.

        The BOM above exists so Excel opens this file, and Excel evaluates any
        cell beginning with = + - or @ as a formula. Team and challenge names
        are the only operator-or-participant-authored columns here, so they
        get a leading apostrophe — Excel's own literal-text escape — rather
        than being trusted. Numeric and ISO-timestamp columns are generated by
        this module and are left alone.
        """
        text = "" if value is None else str(value)
        return "'" + text if text[:1] in ("=", "+", "-", "@") else text

    # One freeze state for the whole file, read once: it is a property of the
    # export, not of a team.
    freeze_columns = [
        int(bool(payload.get("frozen"))),
        _freeze_iso(payload.get("frozen_at")) or "",
    ]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for user in payload["users"]:
        rounds = user["rounds"]
        totals = [
            user["id"],
            _text(user["name"]),
            int(user["hidden"]),
            int(user["banned"]),
            user["score"],
            user["solved"],
            *freeze_columns,
        ]
        for key in round_keys:
            round_score = rounds.get(key, {})
            totals += [round_score.get("score", 0), round_score.get("solved", 0)]
        for challenge in user["challenges"]:
            solves = challenge["solves"]
            writer.writerow(
                totals
                + [
                    challenge["id"],
                    _text(challenge["name"]),
                    _text(challenge["round"]),
                    challenge["value"],
                    int(challenge["solved"]),
                    challenge["solve_count"],
                    challenge["fail_count"],
                    challenge["first_solve_at"] or "",
                    challenge["last_attempt_at"] or "",
                    (solves[0]["ip"] or "") if solves else "",
                ]
            )

    filename = f"econ-judge-export-{generated_at.strftime('%Y%m%dT%H%M%SZ')}.csv"
    return Response(
        "\ufeff" + buffer.getvalue(),
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def register_endpoints(app):
    @app.route("/api/v1/digital/competition", methods=["GET"])
    def digital_competition():
        return jsonify({"success": True, "data": competition_status()})

    @app.route(
        "/api/v1/digital/challenges/<int:challenge_id>/truth-table-attempt",
        methods=["POST"],
    )
    @authed_only
    @bypass_csrf_protection
    def truth_table_attempt(challenge_id):
        if challenge_id != TRUTH_TABLE_CHALLENGE_ID:
            abort(404)
        challenge = Challenges.query.filter_by(
            id=TRUTH_TABLE_CHALLENGE_ID
        ).first_or_404()
        if challenge.type != "digital":
            abort(404)

        unavailable = _submission_window_reject(challenge_id)
        if unavailable is not None:
            return unavailable

        user = get_current_user()
        # Outside the lock, and it must stay outside: get_json() reads the body
        # at the client's pace. Both this and normalize_truth_table_answers are
        # pure, so nothing here needs the guard. See _truth_table_lock.
        payload = request.get_json(silent=True) or {}
        answers = normalize_truth_table_answers(payload.get("answers"))

        with _truth_table_lock:
            already_solved = Solves.query.filter_by(
                user_id=user.id, challenge_id=TRUTH_TABLE_CHALLENGE_ID
            ).first()
            already_failed = Fails.query.filter_by(
                user_id=user.id, challenge_id=TRUTH_TABLE_CHALLENGE_ID
            ).first()
            if already_solved or already_failed:
                return jsonify(
                    {
                        "success": True,
                        "data": {
                            "status": "locked",
                            "message": "이 문제는 이미 제출하여 다시 제출할 수 없습니다.",
                        },
                    }
                )

            # "locked" deliberately outranks the 0/1 message, which is why this
            # branch stays under the lock rather than moving up beside the parse
            # above. Once the one attempt is spent no answer can change the
            # outcome, so telling a mentee to fix their rows would send them
            # back to re-submit something that can never be recorded.
            if answers is None:
                return _reject("모든 행에 0 또는 1을 선택해주세요.")

            team = get_current_team()
            record_fields = {
                "user_id": user.id,
                "team_id": team.id if team else None,
                "challenge_id": TRUTH_TABLE_CHALLENGE_ID,
                "ip": get_ip(request),
                "provided": json.dumps(answers, separators=(",", ":")),
            }
            if answers == TRUTH_TABLE_EXPECTED:
                db.session.add(Solves(**record_fields))
                status = "correct"
                message = "정답입니다. 진리표의 모든 행이 일치합니다."
            else:
                db.session.add(Fails(**record_fields))
                status = "incorrect"
                message = "제출이 기록되었습니다. 이 문제는 다시 제출할 수 없습니다."
            db.session.commit()
        return jsonify(
            {"success": True, "data": {"status": status, "message": message}}
        )

    @app.route(
        "/api/v1/digital/challenges/<int:challenge_id>/attempt",
        methods=["POST"],
    )
    @authed_only
    @bypass_csrf_protection
    def digital_attempt(challenge_id):
        challenge = Challenges.query.filter_by(id=challenge_id).first_or_404()
        if challenge.type != "digital":
            abort(404)

        unavailable = _submission_window_reject(challenge_id)
        if unavailable is not None:
            return unavailable

        with tempfile.TemporaryDirectory() as tmp:
            upload_path, dependencies, provided_filename, error = _stage_digital_bundle(
                challenge_id, tmp
            )
            if error:
                return _reject(error)
            result = grade_submission(challenge_id, upload_path, dependencies)

        user = get_current_user()
        team = get_current_team()
        ip = get_ip(request)

        # "invalid" is a wrong answer, not a system error: either the grader's
        # own structure check (fan-in, NAND count, Seven-Seg wiring) turned the
        # circuit down, or Digital could not evaluate every row because of the
        # circuit / the uploaded folder. Either way `detail` is a hand-written
        # Korean message — never raw Digital output, and never anything that
        # identifies WHICH testcase row was involved — so it is shown to the
        # mentee and the attempt is recorded as a Fail. Rejected input
        # ("rejected") and grader faults ("error") take the branch below
        # instead: no Fail, a generic message, and the real detail logged
        # server-side only.
        if result.get("status") == "invalid":
            if result.get("log_detail"):
                # An incomplete run carries operator-only diagnostics (exit
                # code, line counts, raw Digital output). It must never be
                # silently dropped: it is also how an OOM-truncated grade or a
                # regenerated secret test file first shows up in the logs.
                app.logger.warning(
                    "digital grade incomplete: chal=%s user=%s reason=%s detail=%s",
                    challenge_id,
                    getattr(user, "id", "?"),
                    result.get("reason"),
                    result["log_detail"][:500],
                )
            db.session.add(
                Fails(
                    user_id=user.id,
                    team_id=team.id if team else None,
                    challenge_id=challenge_id,
                    ip=ip,
                    provided=provided_filename,
                )
            )
            db.session.commit()
            manifest = concept_info(challenge_id)
            return jsonify(
                {
                    "success": True,
                    "data": {
                        "status": "incorrect",
                        "message": result.get("detail") or "회로 구성 조건을 확인해주세요.",
                        "concepts": [item["label"] for item in manifest],
                        "hints": [item["hint"] for item in manifest],
                    },
                }
            )

        if result.get("status") != "graded":
            app.logger.warning(
                "digital grade non-graded: chal=%s user=%s status=%s reason=%s detail=%s",
                challenge_id,
                getattr(user, "id", "?"),
                result.get("status"),
                result.get("reason"),
                (result.get("detail") or "")[:500],
            )
            return _reject(
                _ERROR_MESSAGES.get(
                    result.get("reason"),
                    "채점 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                )
            )

        # Concept manifest drives the testbench UI. It is purely educational —
        # the ordered list of aspects a challenge checks — and carries NO
        # per-case pass/fail signal, so it is safe to send on every response.
        # `concepts` = aspect labels (bench animation); `hints` = the parallel
        # directional "확인해 볼 점" checklist shown on a wrong/partial answer.
        manifest = concept_info(challenge_id)
        concepts = [c["label"] for c in manifest]
        hints = [c["hint"] for c in manifest]
        passed = int(result["passed"])
        total = int(result["total"])
        expected = expected_testcase_count(challenge_id)

        # INTEGRITY: a Solve requires a "passed" line for every testcase the
        # secret test file declares. `total` is that expected count — grader.py
        # takes it from the file, never from tallying output lines — and it is
        # re-checked against the file here, so a run that ended early, or whose
        # missing rows printed an error line instead of "passed"/"failed",
        # cannot reach this branch.
        if expected and total == expected and passed == total:
            already = Solves.query.filter_by(
                user_id=user.id, challenge_id=challenge_id
            ).first()
            if already is None:
                solve = Solves(
                    user_id=user.id,
                    team_id=team.id if team else None,
                    challenge_id=challenge_id,
                    ip=ip,
                    provided=provided_filename,
                )
                db.session.add(solve)
                db.session.commit()
            return jsonify(
                {
                    "success": True,
                    "data": {
                        "status": "correct",
                        "message": f"All {total} testcases passed.",
                        "passed": passed,
                        "total": total,
                        "concepts": concepts,
                    },
                }
            )

        wrong = Fails(
            user_id=user.id,
            team_id=team.id if team else None,
            challenge_id=challenge_id,
            ip=ip,
            provided=provided_filename,
        )
        db.session.add(wrong)
        db.session.commit()

        # Conceptual-only feedback: aggregate count + a directional checklist
        # (one hint per concept). The raw per-case grader detail (which case
        # index failed) is deliberately NOT sent — it would let a student
        # pinpoint the failing input combo — and is intentionally discarded
        # here (only the non-graded error branch above logs detail server-side;
        # a normal wrong answer logs nothing). Since Digital grades whole rows,
        # the checklist covers every aspect — it cannot single out the failing
        # one, by design.
        return jsonify(
            {
                "success": True,
                "data": {
                    "status": "incorrect",
                    "message": f"{passed}/{total} testcases passed.",
                    "passed": passed,
                    "total": total,
                    "concepts": concepts,
                    "hints": hints,
                },
            }
        )

    @app.route("/api/v1/digital/my-score", methods=["GET"])
    @authed_only
    def digital_my_score():
        """Anti-toxicity scoreboard surrogate. Returns only the current user's
        score+solve count and the (anonymized) leader's score+solve count —
        no ranked list, no leader team name. CTFd's stock
        /api/v1/scoreboard/* is gated behind score_visibility=admins for the
        same reason, so the /my-score page cannot use those endpoints. This
        is the dedicated surrogate.

        Each "조" is modeled as a CTFd user (not a Team), matching how the
        bootstrap demo seed and camp registration work.
        """
        user = get_current_user()

        frozen, freeze_ts, date_filter = _freeze_state()

        team_round_scores = _user_round_scores(user.id, date_filter)
        team_score = _score_total(team_round_scores)
        team_solved = _solved_total(team_round_scores)

        # Leader: top non-hidden, non-banned, non-admin user by total
        # visible-challenge value. Response anonymizes — score + solved only,
        # no name.
        leader_row = (
            db.session.query(
                Users.id,
                func.coalesce(func.sum(Challenges.value), 0).label("score"),
            )
            .join(Solves, Solves.user_id == Users.id)
            .join(Challenges, Challenges.id == Solves.challenge_id)
            .filter(
                Users.hidden.is_(False),
                Users.banned.is_(False),
                Users.type == "user",
                Challenges.id.in_(ALL_CHALLENGE_IDS),
                *date_filter,
            )
            .group_by(Users.id)
            .order_by(func.sum(Challenges.value).desc())
            .first()
        )

        leader = None
        if leader_row and int(leader_row.score) > 0:
            leader_round_scores = _user_round_scores(leader_row.id, date_filter)
            leader = {
                "score": _score_total(leader_round_scores),
                "solved": _solved_total(leader_round_scores),
            }

        total_points, total_challenges = _challenge_totals()

        return jsonify(
            {
                "success": True,
                "data": {
                    "team": {
                        "name": user.name,
                        "score": team_score,
                        "solved": team_solved,
                        "rounds": _round_payload(team_round_scores),
                    },
                    "leader": leader,
                    "frozen": frozen,
                    "frozen_at": freeze_ts,
                    "total_points": total_points,
                    "total_challenges": total_challenges,
                    "rounds": _round_payload({}),
                    "competition": competition_status(),
                },
            }
        )

    @app.route("/api/v1/digital/projector", methods=["GET"])
    @admins_only
    def digital_projector():
        """BK Hall public-screen feed. Two phase-aware payloads:

        - Practice phase (default, or before freeze): anonymized leader
          score + solved count, plus "collective momentum" stats for the
          last 30 minutes. No team-vs-team framing.
        - Project phase (after freeze): submission matrix with REAL team
          names per row and project-phase challenges per column. Cells
          carry a boolean — submitted (any Solves or Fails) or not.
          NEVER pass/fail/score on the projector during this phase; that
          is the anti-toxicity contract from meeting 3.

        Admin-gated because the project-phase team→submission map is more
        granular than what mentees should be able to scrape via the API.
        The matching /projector Page is open to any logged-in viewer; the
        sensitivity lives here at the data layer.
        """
        frozen, freeze_ts, date_filter = _freeze_state()
        phase = "project" if frozen else "practice"

        total_points, total_challenges = _challenge_totals()

        payload = {
            "phase": phase,
            "frozen_at": freeze_ts,
            "total_points": total_points,
            "total_challenges": total_challenges,
        }

        if phase == "practice":
            # Anonymized leader for the hero score.
            leader_row = (
                db.session.query(
                    Users.id,
                    func.coalesce(func.sum(Challenges.value), 0).label("score"),
                )
                .join(Solves, Solves.user_id == Users.id)
                .join(Challenges, Challenges.id == Solves.challenge_id)
                .filter(
                    Users.hidden.is_(False),
                    Users.banned.is_(False),
                    Users.type == "user",
                    Challenges.id.in_(ALL_CHALLENGE_IDS),
                    *date_filter,
                )
                .group_by(Users.id)
                .order_by(func.sum(Challenges.value).desc())
                .first()
            )
            leader = None
            if leader_row and int(leader_row.score) > 0:
                leader_round_scores = _user_round_scores(leader_row.id, date_filter)
                leader = {
                    "score": _score_total(leader_round_scores),
                    "solved": _solved_total(leader_round_scores),
                }
            payload["leader"] = leader

            # Momentum: last 30 minutes of solves + attempts. JOIN Users so
            # admin test-submissions don't inflate the stats (admins may
            # still poke the system during the camp; only count mentee
            # teams = non-hidden, non-banned, type="user").
            # Naive UTC to match Solves.date / Fails.date (see _freeze_state).
            now_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            window_start = now_utc - datetime.timedelta(minutes=30)

            def _mentee_user_filter():
                return [
                    Users.hidden.is_(False),
                    Users.banned.is_(False),
                    Users.type == "user",
                ]

            new_solves = int(
                db.session.query(func.count(Solves.id))
                .join(Users, Users.id == Solves.user_id)
                .filter(
                    Solves.date >= window_start,
                    Solves.challenge_id.in_(ALL_CHALLENGE_IDS),
                    *_mentee_user_filter(),
                )
                .scalar()
                or 0
            )
            new_fails = int(
                db.session.query(func.count(Fails.id))
                .join(Users, Users.id == Fails.user_id)
                .filter(
                    Fails.date >= window_start,
                    Fails.challenge_id.in_(ALL_CHALLENGE_IDS),
                    *_mentee_user_filter(),
                )
                .scalar()
                or 0
            )

            active_solver_ids = {
                row.user_id
                for row in db.session.query(Solves.user_id)
                .join(Users, Users.id == Solves.user_id)
                .filter(
                    Solves.date >= window_start,
                    Solves.challenge_id.in_(ALL_CHALLENGE_IDS),
                    *_mentee_user_filter(),
                )
                .distinct()
                .all()
            }
            active_fail_ids = {
                row.user_id
                for row in db.session.query(Fails.user_id)
                .join(Users, Users.id == Fails.user_id)
                .filter(
                    Fails.date >= window_start,
                    Fails.challenge_id.in_(ALL_CHALLENGE_IDS),
                    *_mentee_user_filter(),
                )
                .distinct()
                .all()
            }
            active_team_ids = active_solver_ids | active_fail_ids

            total_teams = int(
                db.session.query(func.count(Users.id))
                .filter(
                    Users.hidden.is_(False),
                    Users.banned.is_(False),
                    Users.type == "user",
                )
                .scalar()
                or 0
            )

            payload["momentum"] = {
                "new_solves": new_solves,
                "submits": new_solves + new_fails,
                "active_teams": len(active_team_ids),
                "total_teams": total_teams,
            }
            return jsonify({"success": True, "data": payload})

        # phase == "project": submission matrix. Real team names per user's
        # explicit decision on 2026-05-27 (anti-toxicity policy bounded by
        # cell content being submission boolean only — no scores/verdicts).
        cids = [c["id"] for c in _PROJECT_PHASE_COLS]

        teams = (
            db.session.query(Users.id, Users.name)
            .filter(
                Users.hidden.is_(False),
                Users.banned.is_(False),
                Users.type == "user",
            )
            .order_by(Users.id.asc())
            .all()
        )

        # Build the submitted set as (user_id, challenge_id). Honors freeze:
        # submissions after freeze don't count.
        submit_filters = [
            Solves.challenge_id.in_(cids),
        ]
        if frozen and freeze_ts:
            # Naive UTC to match Solves.date (see _freeze_state).
            cutoff = datetime.datetime.fromtimestamp(
                freeze_ts, datetime.timezone.utc
            ).replace(tzinfo=None)
            submit_filters.append(Solves.date < cutoff)

        submitted = set()
        for row in (
            db.session.query(Solves.user_id, Solves.challenge_id)
            .filter(*submit_filters)
            .distinct()
            .all()
        ):
            submitted.add((row.user_id, row.challenge_id))

        fail_filters = [Fails.challenge_id.in_(cids)]
        if frozen and freeze_ts:
            # Naive UTC to match Fails.date (see _freeze_state).
            cutoff = datetime.datetime.fromtimestamp(
                freeze_ts, datetime.timezone.utc
            ).replace(tzinfo=None)
            fail_filters.append(Fails.date < cutoff)

        for row in (
            db.session.query(Fails.user_id, Fails.challenge_id)
            .filter(*fail_filters)
            .distinct()
            .all()
        ):
            submitted.add((row.user_id, row.challenge_id))

        payload["cols"] = _PROJECT_PHASE_COLS
        payload["teams"] = [
            {
                "name": t.name,
                "submits": [(t.id, cid) in submitted for cid in cids],
            }
            for t in teams
        ]
        return jsonify({"success": True, "data": payload})

    @app.route("/api/v1/digital/health", methods=["GET"])
    def digital_health():
        """Readiness probe for the grading path, and the value render.yaml's
        healthCheckPath carries. CTFd's index — the previous health target —
        returns 200 as soon as gunicorn binds and proves nothing about
        grading; the first exercise of the grader is otherwise a contestant's
        submission during a timed round.

        Public and deliberately cheap — no JVM, no database, no session — so a
        host can poll it on a short interval. Public also means minimal: it
        names the three independently built artifacts a grade needs
        (Digital.jar, a java binary on PATH, one secret test per submittable
        challenge) and whether each one passed, plus the parsed phase and
        schedule. 200 only when every structural check passes, 503 otherwise
        with `failed_checks` naming them.

        What a failure needs for diagnosis — the absolute paths, the resolved
        java binary, the secret-tests directory with its per-challenge row
        counts, and the effective ECON_JUDGE_* tuning — describes the server
        and the grading budget rather than readiness, so `?verbose=1` carries
        it and is admin-gated. `?deep=1` is admin-gated too and additionally
        grades a reference circuit. See _verbose_health and _deep_health.
        """
        if _truthy_arg("deep"):
            return _deep_health()
        if _truthy_arg("verbose"):
            return _verbose_health()
        return _health_response(structural_report())

    # Not a route of its own: /api/v1/digital/health?verbose=1 delegates here,
    # so a non-admin asking for it gets CTFd's ordinary admins_only redirect.
    # Gated because the full report carries absolute server paths, the resolved
    # java binary, and the grading budget — GRADE_CONCURRENCY next to the
    # worst-case seconds one request can spend in the grader is a map of the
    # exact bottleneck grader.py's semaphore exists to protect. Kept separate
    # from the deep probe so an operator can read all of it mid-round without
    # spending a grading slot to do so.
    @admins_only
    def _verbose_health():
        return _health_response(structural_report(verbose=True))

    # Not a route of its own: /api/v1/digital/health?deep=1 delegates here, so
    # a non-admin asking for it gets CTFd's ordinary admins_only redirect,
    # exactly as on /api/v1/digital/projector. Gated because the probe spends a
    # real grading slot and its report carries server paths and raw Digital
    # output. The wall-clock figure it returns is the operator's only live read
    # on how slow a grade is on today's container.
    @admins_only
    def _deep_health():
        report = structural_report(verbose=True)
        probe = deep_probe(_deep_probe_challenge_id())
        report["deep_probe"] = probe
        # Only a grade that actually ran and came back wrong counts against
        # the response. "busy" means a contestant holds the grading permit and
        # this probe queued behind them — the system working as designed;
        # "fixture_unavailable" means the image ships no reference circuit,
        # which says nothing about the grader; "unsupported_challenge" is a bad
        # query parameter. Failing the response on the remaining two is safe
        # precisely because this variant is admin-only: no host health check
        # can reach it and restart-loop the service over one bad grade.
        if probe["status"] in ("failed", "error"):
            report["failed_checks"] = [*report["failed_checks"], "deep_probe"]
            report["ok"] = False
        return _health_response(report)

    @app.route("/api/v1/digital/export", methods=["GET"])
    @admins_only
    def digital_export():
        """One-URL archive of the whole contest record.

        On the free tier SQLite sits on ephemeral disk, so a redeploy, restart
        or idle spin-down destroys every Solve and Fail — which is why
        render.yaml's teardown checklist opens with "EXPORT THE RESULTS before
        anything restarts". That step names CTFd's own admin backup and a dump
        of the database behind DATABASE_URL; this endpoint is the third route
        it points at, the fastest of the three — one GET a mentor can open
        from a phone the moment a round ends — and the only one that arrives
        already shaped the way this contest counts. `?format=csv` returns one
        row per (team, challenge); the default is JSON.

        Admin-gated for the same reason /api/v1/digital/projector is — the
        rows carry real team names — and here also the submission IPs, which
        is strictly more than a mentee should be able to scrape from the API.

        Scores come from the same helpers /my-score renders, freeze filter
        included, so the export cannot disagree with the page a team was just
        shown. The attempt log is deliberately NOT freeze-filtered: it is the
        archival record, so a post-freeze solve appears there while sitting
        outside `score`, and `frozen_at` marks where that line falls.
        """
        frozen, freeze_ts, date_filter = _freeze_state()
        generated_at = datetime.datetime.now(datetime.timezone.utc)

        # Admins are excluded (type != "user"), matching how every other view
        # here counts a contest participant. Hidden and banned mentee rows are
        # kept and flagged rather than dropped: an archival export must not
        # silently lose a team that someone hid mid-camp.
        users = (
            db.session.query(Users.id, Users.name, Users.hidden, Users.banned)
            .filter(Users.type == "user")
            .order_by(Users.id.asc())
            .all()
        )
        challenges = {
            row.id: row
            for row in db.session.query(
                Challenges.id, Challenges.name, Challenges.value
            )
            .filter(Challenges.id.in_(ALL_CHALLENGE_IDS))
            .all()
        }
        round_of = {
            challenge_id: key
            for key, info in ROUND_INFO.items()
            for challenge_id in info["challenge_ids"]
        }

        # Two queries for the entire attempt log, bucketed in Python. A
        # per-user-per-challenge query would be users * challenges * 2 round
        # trips against a SQLite file that a mentor is waiting on over mobile
        # data at the exact moment the round ends.
        attempts = {}

        def _collect(kind, rows):
            for row in rows:
                bucket = attempts.setdefault(
                    (row.user_id, row.challenge_id),
                    {"solves": [], "fails": [], "dates": []},
                )
                bucket[kind].append(
                    {
                        "at": _iso_utc(row.date),
                        "ip": row.ip,
                        "provided": row.provided,
                    }
                )
                # Kept as datetimes, and shared by both kinds, so
                # `last_attempt_at` is a real max over solves AND fails rather
                # than a comparison of formatted strings.
                if isinstance(row.date, datetime.datetime):
                    bucket["dates"].append(row.date)

        _collect(
            "solves",
            db.session.query(
                Solves.user_id,
                Solves.challenge_id,
                Solves.date,
                Solves.ip,
                Solves.provided,
            )
            .filter(Solves.challenge_id.in_(ALL_CHALLENGE_IDS))
            .order_by(Solves.date.asc())
            .all(),
        )
        _collect(
            "fails",
            db.session.query(
                Fails.user_id,
                Fails.challenge_id,
                Fails.date,
                Fails.ip,
                Fails.provided,
            )
            .filter(Fails.challenge_id.in_(ALL_CHALLENGE_IDS))
            .order_by(Fails.date.asc())
            .all(),
        )

        records = []
        for user in users:
            round_scores = _user_round_scores(user.id, date_filter)
            per_challenge = []
            for challenge_id in sorted(challenges):
                challenge = challenges[challenge_id]
                bucket = attempts.get(
                    (user.id, challenge_id),
                    {"solves": [], "fails": [], "dates": []},
                )
                solves = bucket["solves"]
                per_challenge.append(
                    {
                        "id": challenge_id,
                        "name": challenge.name,
                        "value": int(challenge.value or 0),
                        "round": round_of.get(challenge_id),
                        "solved": bool(solves),
                        "solve_count": len(solves),
                        "fail_count": len(bucket["fails"]),
                        "first_solve_at": solves[0]["at"] if solves else None,
                        "last_attempt_at": (
                            _iso_utc(max(bucket["dates"])) if bucket["dates"] else None
                        ),
                        "solves": solves,
                        "fails": bucket["fails"],
                    }
                )
            records.append(
                {
                    "id": user.id,
                    "name": user.name or "",
                    "hidden": bool(user.hidden),
                    "banned": bool(user.banned),
                    "score": _score_total(round_scores),
                    "solved": _solved_total(round_scores),
                    "rounds": _round_payload(round_scores),
                    "challenges": per_challenge,
                }
            )

        total_points, total_challenges = _challenge_totals()
        payload = {
            "generated_at": generated_at.isoformat(),
            "frozen": frozen,
            "frozen_at": freeze_ts,
            "competition": competition_status(),
            "problem_set": {
                "challenge_ids": sorted(challenges),
                "starter_files": {
                    str(challenge_id): starter
                    for challenge_id, starter in sorted(HWP_STARTER_FILES.items())
                },
                "rounds": _round_payload({}),
                "total_points": total_points,
                "total_challenges": total_challenges,
            },
            "users": records,
        }

        if request.args.get("format", "").strip().lower() == "csv":
            return _export_csv(payload, generated_at)
        return jsonify({"success": True, "data": payload})
