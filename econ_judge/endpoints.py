import datetime
import os
import tempfile
import time

from flask import abort, jsonify, request
from sqlalchemy import func

from CTFd.models import Challenges, Fails, Solves, Users, db
from CTFd.plugins import bypass_csrf_protection
from CTFd.utils import get_config
from CTFd.utils.decorators import authed_only
from CTFd.utils.user import get_current_team, get_current_user, get_ip

from .grader import grade_submission

MAX_UPLOAD_BYTES = 256 * 1024


def _reject(message: str):
    return jsonify(
        {"success": True, "data": {"status": "incorrect", "message": message}}
    )


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
        score and the (anonymized) leader's score — no ranked list, no leader
        team name. CTFd's stock /api/v1/scoreboard/* is gated behind
        score_visibility=admins for the same reason, so the /my-score page
        cannot use those endpoints. This is the dedicated surrogate.

        Each "조" is modeled as a CTFd user (not a Team), matching how the
        bootstrap demo seed and camp registration work.
        """
        user = get_current_user()

        # Freeze handling: if freeze is set and now >= freeze, scores reflect
        # state at freeze time (solves dated after freeze are excluded). The
        # value is a Unix timestamp string per CTFd's convention.
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

        def score_for(user_id):
            q = (
                db.session.query(func.coalesce(func.sum(Challenges.value), 0))
                .join(Solves, Solves.challenge_id == Challenges.id)
                .filter(
                    Solves.user_id == user_id,
                    Challenges.state == "visible",
                    *date_filter,
                )
            )
            return int(q.scalar() or 0)

        team_score = score_for(user.id)

        # Leader: top non-hidden, non-banned user by total visible-challenge
        # value. We anonymize: the response includes only the leader's score.
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
                Challenges.state == "visible",
                *date_filter,
            )
            .group_by(Users.id)
            .order_by(func.sum(Challenges.value).desc())
            .first()
        )

        leader = None
        if leader_row and int(leader_row.score) > 0:
            leader = {"score": int(leader_row.score)}

        total_points = int(
            db.session.query(func.coalesce(func.sum(Challenges.value), 0))
            .filter(Challenges.state == "visible")
            .scalar()
            or 0
        )

        return jsonify(
            {
                "success": True,
                "data": {
                    "team": {"name": user.name, "score": team_score},
                    "leader": leader,
                    "frozen": frozen,
                    "frozen_at": freeze_ts,
                    "total_points": total_points,
                },
            }
        )
