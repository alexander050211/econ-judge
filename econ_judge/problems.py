"""Participant-facing, full-page problem views.

CTFd's stock challenge view is modal-only. This module keeps CTFd as the
source of truth for challenge/auth/solve data while giving econ-judge a stable
URL and enough room for richer problem statements and submission controls.
"""

from flask import Blueprint, abort, render_template

from CTFd.models import Challenges, Fails, Solves
from CTFd.utils.config.pages import build_markdown
from CTFd.utils.decorators import authed_only
from CTFd.utils.user import get_current_user


_CATEGORY_ORDER = {
    "연습": 0,
    "미션": 1,
    "프로젝트": 2,
    # Keep the current problem set ordered correctly until it is replaced.
    "Project 1": 2,
    "Project 2": 3,
}

problem_pages = Blueprint(
    "econ_judge_problem_pages",
    __name__,
    template_folder="templates",
)


def _problem_sort_key(challenge):
    category = challenge.category or ""
    return (_CATEGORY_ORDER.get(category, 99), category, challenge.id)


def _problem_neighbors(challenge_id: int):
    challenges = sorted(
        Challenges.query.filter_by(state="visible").all(),
        key=_problem_sort_key,
    )
    ids = [challenge.id for challenge in challenges]
    try:
        index = ids.index(challenge_id)
    except ValueError:
        return None, None

    previous_id = ids[index - 1] if index > 0 else None
    next_id = ids[index + 1] if index + 1 < len(ids) else None
    return previous_id, next_id


@problem_pages.route("/problems/<int:challenge_id>", methods=["GET"])
@authed_only
def digital_problem_page(challenge_id):
    challenge = Challenges.query.filter_by(id=challenge_id).first_or_404()
    user = get_current_user()

    if challenge.state != "visible" and user.type != "admin":
        abort(404)
    if challenge.type != "digital":
        abort(404)

    solved = (
        Solves.query.filter_by(
            user_id=user.id,
            challenge_id=challenge_id,
        ).first()
        is not None
    )
    failed_attempts = Fails.query.filter_by(
        user_id=user.id,
        challenge_id=challenge_id,
    ).count()
    record_count = failed_attempts + (1 if solved else 0)
    previous_id, next_id = _problem_neighbors(challenge_id)

    return render_template(
        "problems/view.html",
        title=challenge.name,
        challenge=challenge,
        description_html=build_markdown(
            challenge.description or "",
            sanitize=True,
        ),
        solved=solved,
        record_count=record_count,
        previous_id=previous_id,
        next_id=next_id,
    )


def register_problem_pages(app):
    app.register_blueprint(problem_pages)
