import os
import tempfile

from flask import abort, jsonify, request

from CTFd.models import Challenges, Fails, Solves, db
from CTFd.plugins import bypass_csrf_protection
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
