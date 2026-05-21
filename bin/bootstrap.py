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
CTF_NAME = os.environ.get("CTFD_NAME", "SNU SENS E-CON 논설")
CTF_DESCRIPTION = os.environ.get(
    "CTFD_DESCRIPTION", "공헌 공드림 캠프 E-CON 논설 (논리설계) 자동채점 시스템"
)

# Hidden user reserved for tests/deploy_smoke.py runs.
SMOKE_NAME = "smoke-test-1"
SMOKE_EMAIL = "smoke1@econ-judge.local"
SMOKE_PASSWORD = "smoketest-pw-1"

# SNU brand navy applied to navbar + primary accents. Mirrors the CSS that
# CTFd's /setup wizard generates so it survives any future theme updates.
THEME_HEADER_CSS = """\
<style id="econ-judge-theme">
:root { --theme-color: #003876; }
.navbar { background-color: var(--theme-color) !important; }
.jumbotron { background-color: var(--theme-color) !important; }
.btn-primary {
  background-color: var(--theme-color) !important;
  border-color: var(--theme-color) !important;
}
a { color: var(--theme-color); }
</style>
"""

INDEX_CONTENT = """\
# E-CON 논설 자동채점기

**SNU SENS 공헌** 공드림 캠프 E-CON **논설** (논리설계) 과제의 자동채점 시스템입니다.

상단 메뉴의 **Challenges** 를 클릭해 18개 문제를 확인하세요. Digital (`.dig`) 회로 파일을 업로드하면 약 10초 이내에 비밀 테스트케이스에 대한 채점 결과가 나오고, 스코어보드가 실시간으로 갱신됩니다.

| 분류 | 문항 수 | 내용 |
|---|---|---|
| **연습** | 3 | 진리표 → 불 대수, 3-입력 AND, 2:1 MUX |
| **미션** | 4 | NOR 게이트 분해, 21세기 윤년 판독기 |
| **프로젝트 1** | 6 | 가산기 (반/전/3비트) → 보수 계산기 → ÷3 연산기 → 7-세그먼트 |
| **프로젝트 2** | 5 | 2비트 / 2,3비트 비교기 → 대피소 배정 → 7-세그먼트 |

계정을 등록하고 시작해보세요.
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

        # All configs are always synced — bootstrap is "configuration as
        # code." Changing a value here + redeploying actually rotates the
        # config; the deploy state never drifts from what's in this file.
        set_config("ctf_name", CTF_NAME)
        set_config("ctf_description", CTF_DESCRIPTION)
        set_config("ctf_theme", "core-beta")
        set_config("user_mode", "users")
        set_config("default_locale", "ko")
        set_config("challenge_visibility", "public")
        set_config("registration_visibility", "public")
        set_config("score_visibility", "public")
        set_config("account_visibility", "public")
        set_config("verify_emails", False)
        set_config("team_size", None)
        set_config("theme_header", THEME_HEADER_CSS)
        first_time = not get_config("setup")
        set_config("setup", True)
        db.session.commit()
        print(f"[bootstrap] CTFd {'initialized' if first_time else 'configs re-synced'}")

        # Admin user upsert — env var is the source of truth for the password,
        # so changing CTFD_ADMIN_PASSWORD in Render's Environment + redeploying
        # actually rotates the admin password. @validates('password') re-hashes
        # on assignment.
        admin = Users.query.filter_by(name=ADMIN_NAME).first()
        if admin is None:
            admin = Users(
                name=ADMIN_NAME,
                email=ADMIN_EMAIL,
                password=ADMIN_PASSWORD,
                type="admin",
                verified=True,
                hidden=True,
            )
            db.session.add(admin)
            db.session.commit()
            print(f"[bootstrap] Admin '{ADMIN_NAME}' created")
        else:
            admin.password = ADMIN_PASSWORD
            db.session.commit()
            print(f"[bootstrap] Admin '{ADMIN_NAME}' password synced from env")

        # Smoke-test user (hidden) — pre-created so deploy_smoke.py runs
        # don't pollute the public scoreboard. Idempotent: created if missing,
        # otherwise hidden flag + password reset to canonical values.
        smoke = Users.query.filter_by(name=SMOKE_NAME).first()
        if smoke is None:
            smoke = Users(
                name=SMOKE_NAME,
                email=SMOKE_EMAIL,
                password=SMOKE_PASSWORD,
                type="user",
                verified=True,
                hidden=True,
            )
            db.session.add(smoke)
            db.session.commit()
            print(f"[bootstrap] Smoke user '{SMOKE_NAME}' created (hidden)")
        else:
            changed = False
            if not smoke.hidden:
                smoke.hidden = True
                changed = True
            # @validates('password') re-hashes on assignment
            smoke.password = SMOKE_PASSWORD
            if changed:
                db.session.commit()
                print(f"[bootstrap] Smoke user '{SMOKE_NAME}' marked hidden")
            else:
                db.session.commit()

        # Index page upsert — content always synced from INDEX_CONTENT, same
        # "configuration as code" pattern as the configs block above.
        page = Pages.query.filter_by(route="index").first()
        if page is None:
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
        else:
            page.title = CTF_NAME
            page.content = INDEX_CONTENT
            page.format = "markdown"
            db.session.commit()
            print("[bootstrap] Index page synced")

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
