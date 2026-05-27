import datetime
import os
import tempfile
import time

from flask import abort, jsonify, request
from sqlalchemy import func

from CTFd.models import Challenges, Fails, Solves, Users, db
from CTFd.plugins import bypass_csrf_protection
from CTFd.utils import get_config
from CTFd.utils.decorators import admins_only, authed_only
from CTFd.utils.user import get_current_team, get_current_user, get_ip

from .grader import grade_submission

MAX_UPLOAD_BYTES = 256 * 1024


def _reject(message: str):
    return jsonify(
        {"success": True, "data": {"status": "incorrect", "message": message}}
    )


# ── Projector category mapping ────────────────────────────────────────
# Project-phase challenge ids — fixed by the camp problem set, matching
# tests/register_challenges.py. Order = column order in the projector
# matrix. Keep in sync with bootstrap.py CHALLENGES.
_PROJECT_PHASE_COLS = [
    {"id": 3,  "short": "P1·A3",   "name": "3-bit Adder"},
    {"id": 12, "short": "P1·B",    "name": "보수 계산기"},
    {"id": 13, "short": "P1·C",    "name": "÷3 Round"},
    {"id": 16, "short": "P1·FULL", "name": "Full Wire"},
    {"id": 4,  "short": "P2·A1",   "name": "2-bit Comp"},
    {"id": 15, "short": "P2·A2",   "name": "2,3-bit Comp"},
    {"id": 14, "short": "P2·B",    "name": "대피소 배정"},
    {"id": 17, "short": "P2·C",    "name": "7-seg Drv"},
    {"id": 18, "short": "P2·FULL", "name": "Full Wire"},
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
        cutoff = datetime.datetime.utcfromtimestamp(freeze_ts)
        date_filter.append(Solves.date < cutoff)
    return frozen, freeze_ts, date_filter


def _user_score_and_solved(user_id, date_filter):
    """Return (total_score, solved_count) for the given user, scoped to
    visible challenges, honoring the freeze date_filter."""
    row = (
        db.session.query(
            func.coalesce(func.sum(Challenges.value), 0).label("score"),
            func.count(Solves.id).label("solved"),
        )
        .join(Solves, Solves.challenge_id == Challenges.id)
        .filter(
            Solves.user_id == user_id,
            Challenges.state == "visible",
            *date_filter,
        )
        .one()
    )
    return int(row.score or 0), int(row.solved or 0)


def _challenge_totals():
    """Return (total_points, total_challenges) across all visible
    challenges. Both numbers shown on /my-score and /projector."""
    total_points = int(
        db.session.query(func.coalesce(func.sum(Challenges.value), 0))
        .filter(Challenges.state == "visible")
        .scalar()
        or 0
    )
    total_challenges = int(
        db.session.query(func.count(Challenges.id))
        .filter(Challenges.state == "visible")
        .scalar()
        or 0
    )
    return total_points, total_challenges


def register_endpoints(app):
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

        if "file" not in request.files:
            return _reject("No file uploaded.")

        upload = request.files["file"]
        if not upload.filename:
            return _reject("No file selected.")
        if not upload.filename.lower().endswith(".dig"):
            return _reject("Please upload a .dig file (Digital circuit format).")

        upload.seek(0, os.SEEK_END)
        size = upload.tell()
        upload.seek(0)
        if size == 0:
            return _reject("Uploaded file is empty.")
        if size > MAX_UPLOAD_BYTES:
            return _reject(
                f"File too large ({size:,} bytes). Limit is "
                f"{MAX_UPLOAD_BYTES:,} bytes."
            )

        with tempfile.TemporaryDirectory() as tmp:
            upload_path = os.path.join(tmp, "submission.dig")
            upload.save(upload_path)
            result = grade_submission(challenge_id, upload_path)

        user = get_current_user()
        team = get_current_team()
        ip = get_ip(request)

        if result["total"] > 0 and result["passed"] == result["total"]:
            already = Solves.query.filter_by(
                user_id=user.id, challenge_id=challenge_id
            ).first()
            if already is None:
                solve = Solves(
                    user_id=user.id,
                    team_id=team.id if team else None,
                    challenge_id=challenge_id,
                    ip=ip,
                    provided=upload.filename,
                )
                db.session.add(solve)
                db.session.commit()
            return jsonify(
                {
                    "success": True,
                    "data": {
                        "status": "correct",
                        "message": f"All {result['total']} testcases passed.",
                    },
                }
            )

        wrong = Fails(
            user_id=user.id,
            team_id=team.id if team else None,
            challenge_id=challenge_id,
            ip=ip,
            provided=upload.filename,
        )
        db.session.add(wrong)
        db.session.commit()

        msg_lines = [f"{result['passed']}/{result['total']} testcases passed."]
        if result["detail"]:
            msg_lines.append(result["detail"])
        return jsonify(
            {
                "success": True,
                "data": {
                    "status": "incorrect",
                    "message": "\n".join(msg_lines),
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

        team_score, team_solved = _user_score_and_solved(user.id, date_filter)

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
                Challenges.state == "visible",
                *date_filter,
            )
            .group_by(Users.id)
            .order_by(func.sum(Challenges.value).desc())
            .first()
        )

        leader = None
        if leader_row and int(leader_row.score) > 0:
            _, leader_solved = _user_score_and_solved(leader_row.id, date_filter)
            leader = {"score": int(leader_row.score), "solved": leader_solved}

        total_points, total_challenges = _challenge_totals()

        return jsonify(
            {
                "success": True,
                "data": {
                    "team": {
                        "name": user.name,
                        "score": team_score,
                        "solved": team_solved,
                    },
                    "leader": leader,
                    "frozen": frozen,
                    "frozen_at": freeze_ts,
                    "total_points": total_points,
                    "total_challenges": total_challenges,
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
                    Challenges.state == "visible",
                    *date_filter,
                )
                .group_by(Users.id)
                .order_by(func.sum(Challenges.value).desc())
                .first()
            )
            leader = None
            if leader_row and int(leader_row.score) > 0:
                _, leader_solved = _user_score_and_solved(leader_row.id, date_filter)
                leader = {
                    "score": int(leader_row.score),
                    "solved": leader_solved,
                }
            payload["leader"] = leader

            # Momentum: last 30 minutes of solves + attempts. JOIN Users so
            # admin test-submissions don't inflate the stats (admins may
            # still poke the system during the camp; only count mentee
            # teams = non-hidden, non-banned, type="user").
            window_start = datetime.datetime.utcnow() - datetime.timedelta(minutes=30)

            def _mentee_user_filter():
                return [
                    Users.hidden.is_(False),
                    Users.banned.is_(False),
                    Users.type == "user",
                ]

            new_solves = int(
                db.session.query(func.count(Solves.id))
                .join(Users, Users.id == Solves.user_id)
                .filter(Solves.date >= window_start, *_mentee_user_filter())
                .scalar()
                or 0
            )
            new_fails = int(
                db.session.query(func.count(Fails.id))
                .join(Users, Users.id == Fails.user_id)
                .filter(Fails.date >= window_start, *_mentee_user_filter())
                .scalar()
                or 0
            )

            active_solver_ids = {
                row.user_id
                for row in db.session.query(Solves.user_id)
                .join(Users, Users.id == Solves.user_id)
                .filter(Solves.date >= window_start, *_mentee_user_filter())
                .distinct()
                .all()
            }
            active_fail_ids = {
                row.user_id
                for row in db.session.query(Fails.user_id)
                .join(Users, Users.id == Fails.user_id)
                .filter(Fails.date >= window_start, *_mentee_user_filter())
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
            cutoff = datetime.datetime.utcfromtimestamp(freeze_ts)
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
            cutoff = datetime.datetime.utcfromtimestamp(freeze_ts)
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
