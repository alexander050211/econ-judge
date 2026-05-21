"""Bootstrap CTFd on first container start: create admin, mark setup complete,
seed the 18 econ-judge challenges and a minimal index page. Idempotent —
running on every boot lets the deploy survive Render free tier's ephemeral
disk."""

from __future__ import annotations

import importlib.util
import os
import sys

sys.path.insert(0, "/opt/CTFd")

from CTFd import create_app
from CTFd.models import Challenges, Pages, Users, db
from CTFd.utils import get_config, set_config

ADMIN_NAME = os.environ.get("CTFD_ADMIN_NAME", "admin")
ADMIN_EMAIL = os.environ.get("CTFD_ADMIN_EMAIL", "admin@econ-judge.local")
ADMIN_PASSWORD = os.environ.get("CTFD_ADMIN_PASSWORD", "demo1234")
CTF_NAME = os.environ.get("CTFD_NAME", "econ-judge")

INDEX_CONTENT = """\
# E-CON 논설 Auto-grader

Welcome to the SNU SENS E-CON 논설 auto-grader.

Click **Challenges** in the top nav to see the 18 problems. Submit your
Digital (`.dig`) circuit files; the grader runs them against secret
testcases within ~10 seconds and updates the scoreboard live.

- **연습** (3 problems) — warm-ups
- **미션** (4 problems) — NOR decomposition + leap-year detector
- **Project 1** (6 problems) — adders + battery sizing pipeline
- **Project 2** (5 problems) — comparator + shelter assignment + 7-seg

Register an account to get started.
"""


def _load_challenges():
    spec = importlib.util.spec_from_file_location(
        "register_challenges", "/opt/econ-judge/tests/register_challenges.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.CHALLENGES


CHALLENGES = _load_challenges()


def main() -> None:
    app = create_app()
    with app.app_context():
        db.create_all()
        if get_config("setup"):
            print(f"[bootstrap] CTFd already initialized; admin={ADMIN_NAME}")
        else:
            # SQLAlchemy's @validates('password') hashes automatically — pass plaintext.
            admin = Users(
                name=ADMIN_NAME,
                email=ADMIN_EMAIL,
                password=ADMIN_PASSWORD,
                type="admin",
                verified=True,
                hidden=True,
            )
            db.session.add(admin)
            set_config("ctf_name", CTF_NAME)
            set_config("ctf_description", "SNU SENS E-CON 논설 logic-design auto-grader")
            set_config("ctf_theme", "core-beta")
            set_config("user_mode", "users")
            set_config("challenge_visibility", "public")
            set_config("registration_visibility", "public")
            set_config("score_visibility", "public")
            set_config("account_visibility", "public")
            set_config("verify_emails", False)
            set_config("team_size", None)
            set_config("setup", True)
            db.session.commit()
            print(f"[bootstrap] CTFd initialized; admin={ADMIN_NAME}")

        # Index page so `/` renders instead of 404.
        if not Pages.query.filter_by(route="index").first():
            db.session.add(Pages(
                title=CTF_NAME,
                route="index",
                content=INDEX_CONTENT,
                draft=False,
                hidden=False,
                auth_required=False,
                format="markdown",
            ))
            db.session.commit()
            print("[bootstrap] Index page created")

        existing_ids = {c.id for c in Challenges.query.all()}
        created = 0
        for cid, name, category, value, description, _rows in CHALLENGES:
            if cid in existing_ids:
                continue
            chal = Challenges(
                name=name,
                category=category,
                value=value,
                description=description,
                state="visible",
                type="digital",
            )
            chal.id = cid
            db.session.add(chal)
            created += 1
        if created:
            db.session.commit()
            print(f"[bootstrap] Seeded {created} challenges")
        else:
            print(f"[bootstrap] All {len(CHALLENGES)} challenges already present")


if __name__ == "__main__":
    main()
