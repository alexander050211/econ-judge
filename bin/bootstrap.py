"""Bootstrap CTFd on first container start: create admin, mark setup complete,
seed the 18 econ-judge challenges. Idempotent — running on every boot lets
the deploy survive Render free tier's ephemeral disk."""

from __future__ import annotations

import importlib.util
import os
import sys

sys.path.insert(0, "/opt/CTFd")

from CTFd import create_app
from CTFd.models import Challenges, Users, db
from CTFd.utils import get_config, set_config
from CTFd.utils.crypto import hash_password


def _load_challenges():
    spec = importlib.util.spec_from_file_location(
        "register_challenges", "/opt/econ-judge/tests/register_challenges.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.CHALLENGES


CHALLENGES = _load_challenges()

ADMIN_NAME = os.environ.get("CTFD_ADMIN_NAME", "admin")
ADMIN_EMAIL = os.environ.get("CTFD_ADMIN_EMAIL", "admin@econ-judge.local")
ADMIN_PASSWORD = os.environ.get("CTFD_ADMIN_PASSWORD", "demo1234")
CTF_NAME = os.environ.get("CTFD_NAME", "econ-judge")


def main() -> None:
    app = create_app()
    with app.app_context():
        db.create_all()
        if get_config("setup"):
            print(f"[bootstrap] CTFd already initialized; admin={ADMIN_NAME}")
        else:
            admin = Users(
                name=ADMIN_NAME,
                email=ADMIN_EMAIL,
                password=hash_password(ADMIN_PASSWORD),
                type="admin",
                verified=True,
                hidden=True,
            )
            db.session.add(admin)
            set_config("ctf_name", CTF_NAME)
            set_config("ctf_theme", "core-beta")
            set_config("user_mode", "users")
            set_config("setup", True)
            db.session.commit()
            print(f"[bootstrap] CTFd initialized; admin={ADMIN_NAME}")

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
