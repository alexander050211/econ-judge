"""Bootstrap CTFd on first container start: create admin, mark setup complete,
seed the 18 econ-judge challenges and a minimal index page. Idempotent —
running on every boot lets the deploy survive Render free tier's ephemeral
disk."""

from __future__ import annotations

import datetime
import importlib.util
import os
import sys

sys.path.insert(0, "/opt/CTFd")

from CTFd import create_app
from CTFd.models import Challenges, Pages, Solves, Users, db
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

# Demo data toggle — set CTFD_DEMO_DATA=false in Render env for the actual
# camp day so the production scoreboard starts empty. Default true so
# fresh deploys are immediately visually populated for review.
SEED_DEMO_DATA = os.environ.get("CTFD_DEMO_DATA", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# Four demo teams matching the camp's actual 4-team structure. Solves are
# (challenge_id, minutes_ago_from_now) — spread realistically over a
# 2-hour window to mimic mid-contest state, with easy challenges first,
# harder composition challenges later, and progressive difficulty stacks
# (1조 cleared 16/18, 2조 13/18, 3조 9/18, 4조 4/18).
DEMO_PASSWORD = "demo1234"
DEMO_TEAMS = [
    {
        "name": "1조",
        "email": "team1@econ-judge.local",
        "solves": [
            (5, 112), (6, 108), (7, 102),       # 연습 (10 pts)
            (8, 95), (9, 90), (10, 85), (11, 78),  # 미션 (16 pts)
            (1, 70), (2, 55), (3, 42),          # P1 adders (18 pts)
            (12, 38), (13, 28),                  # P1 보수/÷3 (14 pts)
            (4, 65), (15, 32),                   # P2 비교기 (9 pts)
            (14, 25), (17, 18),                  # P2 대피소/7-seg (11 pts)
        ],  # total: 78 pts
    },
    {
        "name": "2조",
        "email": "team2@econ-judge.local",
        "solves": [
            (5, 115), (6, 110), (7, 105),
            (8, 100), (9, 92), (10, 80),
            (1, 75), (2, 60), (3, 48),
            (12, 35),
            (4, 70), (15, 40),
            (14, 33),
        ],  # total: 60 pts
    },
    {
        "name": "3조",
        "email": "team3@econ-judge.local",
        "solves": [
            (5, 110), (6, 100), (7, 88),
            (8, 95), (9, 80),
            (11, 70),
            (1, 65), (2, 50),
            (4, 60),
        ],  # total: 38 pts
    },
    {
        "name": "4조",
        "email": "team4@econ-judge.local",
        "solves": [
            (5, 108), (6, 95),
            (8, 75),
            (1, 50),
        ],  # total: 12 pts
    },
]

# SENS brand palette extracted from sens.snu.ac.kr's CSS. SENS the club uses
# warm orange/amber, distinct from SNU University's navy. Loaded globally
# via CTFd's `theme_header` config so all pages (login, scoreboard, admin)
# pick up the same Pretendard + color tokens without per-page styling.
THEME_HEADER_CSS = """\
<style id="econ-judge-theme">
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable.min.css');

:root {
  --theme-color: #f5a83d;
  --sens-brand: #f5a83d;
  --sens-brand-dark: #d69336;
  --sens-brand-ink: #7a5a1f;
  --sens-brand-soft: #fff4e0;
}
body {
  font-family: 'Pretendard Variable', Pretendard, -apple-system,
    BlinkMacSystemFont, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif;
}
.navbar { background-color: var(--sens-brand) !important; }
.jumbotron { background-color: var(--sens-brand) !important; }
.btn-primary {
  background-color: var(--sens-brand) !important;
  border-color: var(--sens-brand) !important;
}
.btn-primary:hover,
.btn-primary:focus,
.btn-primary:active {
  background-color: var(--sens-brand-dark) !important;
  border-color: var(--sens-brand-dark) !important;
}
a { color: var(--sens-brand-ink); }
a:hover { color: var(--sens-brand-dark); text-decoration: none; }
</style>
"""

# HTML index page (CTFd Pages.format = "html"). Designed to fit inside
# CTFd's standard container — no negative-margin breakouts.
INDEX_CONTENT = """\
<style>
.econ-landing { font-family: 'Pretendard Variable', Pretendard, sans-serif; color: #1f2937; }
.econ-landing .hero {
  position: relative;
  padding: 3.5rem 2rem 3rem;
  margin: 0 0 2rem;
  background: linear-gradient(135deg, #fff4e0 0%, #fef9e7 60%, #fffbf2 100%);
  border-radius: 20px;
  text-align: center;
  overflow: hidden;
  border: 1px solid rgba(245, 168, 61, 0.25);
}
.econ-landing .hero::before {
  content: '';
  position: absolute; inset: 0;
  background-image: radial-gradient(rgba(245, 168, 61, 0.18) 1px, transparent 1px);
  background-size: 18px 18px;
  background-position: 0 0;
  mask-image: linear-gradient(180deg, rgba(0,0,0,0.7) 0%, rgba(0,0,0,0) 80%);
  -webkit-mask-image: linear-gradient(180deg, rgba(0,0,0,0.7) 0%, rgba(0,0,0,0) 80%);
  pointer-events: none;
}
.econ-landing .hero .eyebrow {
  position: relative;
  display: inline-block;
  padding: 0.25rem 0.9rem;
  background: rgba(245, 168, 61, 0.18);
  color: #7a5a1f;
  border-radius: 999px;
  font-size: 0.78rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  margin-bottom: 1.1rem;
}
.econ-landing .hero h1 {
  position: relative;
  margin: 0;
  font-size: clamp(1.9rem, 4.5vw, 2.6rem);
  font-weight: 700;
  letter-spacing: -0.025em;
  color: #1f2937;
  line-height: 1.2;
}
.econ-landing .hero h1 .accent {
  background: linear-gradient(90deg, #f5a83d, #d69336);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
.econ-landing .hero .tagline {
  position: relative;
  margin: 0.9rem auto 0;
  max-width: 38rem;
  font-size: 1.02rem;
  line-height: 1.65;
  color: #5b6471;
}
.econ-landing .stats {
  position: relative;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.75rem;
  max-width: 30rem;
  margin: 2rem auto 0;
}
.econ-landing .stat {
  padding: 0.85rem 0.6rem;
  background: #ffffff;
  border-radius: 12px;
  border: 1px solid rgba(245, 168, 61, 0.28);
  box-shadow: 0 1px 2px rgba(31, 41, 55, 0.04);
}
.econ-landing .stat .num {
  font-family: ui-monospace, 'SF Mono', monospace;
  font-size: 1.7rem;
  font-weight: 700;
  color: #b45309;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
.econ-landing .stat .label {
  margin-top: 0.3rem;
  font-size: 0.78rem;
  color: #6b7280;
  letter-spacing: 0.02em;
}
.econ-landing .cats {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.9rem;
}
.econ-landing .cat {
  position: relative;
  padding: 1.25rem 1.3rem;
  background: #ffffff;
  border: 1px solid #e5e7eb;
  border-radius: 14px;
  transition: transform 200ms ease, box-shadow 200ms ease, border-color 200ms ease;
  overflow: hidden;
}
.econ-landing .cat::after {
  content: '';
  position: absolute; left: 0; top: 0; bottom: 0;
  width: 3px;
  background: var(--bar, #f5a83d);
  border-radius: 3px 0 0 3px;
}
.econ-landing .cat:hover {
  transform: translateY(-2px);
  border-color: rgba(245, 168, 61, 0.45);
  box-shadow: 0 10px 24px rgba(245, 168, 61, 0.12);
}
.econ-landing .cat .row {
  display: flex; align-items: baseline; justify-content: space-between; gap: 0.5rem;
  margin-bottom: 0.4rem;
}
.econ-landing .cat .name {
  font-size: 1.05rem;
  font-weight: 600;
  letter-spacing: -0.01em;
}
.econ-landing .cat .count {
  font-family: ui-monospace, 'SF Mono', monospace;
  font-size: 0.78rem;
  color: #b45309;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.econ-landing .cat .desc {
  margin: 0;
  font-size: 0.88rem;
  line-height: 1.55;
  color: #6b7280;
}
.econ-landing .cta {
  margin-top: 2rem;
  text-align: center;
  font-size: 0.95rem;
  color: #6b7280;
}
.econ-landing .cta a {
  color: #7a5a1f;
  font-weight: 600;
  text-decoration: underline;
  text-underline-offset: 3px;
}
.econ-landing .cta a:hover { color: #d69336; }
@media (max-width: 640px) {
  .econ-landing .hero { padding: 2.5rem 1.25rem 2rem; }
  .econ-landing .stats { grid-template-columns: 1fr 1fr 1fr; max-width: none; }
  .econ-landing .cats { grid-template-columns: 1fr; }
}
</style>

<div class="econ-landing">
  <div class="hero">
    <span class="eyebrow">SNU SENS · 공헌</span>
    <h1>E-CON <span class="accent">논설</span> 자동채점기</h1>
    <p class="tagline">
      공드림 캠프의 논리설계 과제를 위한 자동 채점 플랫폼.<br>
      Digital 시뮬레이터로 작성한 회로 파일을 업로드하면 비밀 테스트케이스로 검증해 약 10초 안에 결과를 돌려드립니다.
    </p>
    <div class="stats">
      <div class="stat"><div class="num">18</div><div class="label">문제</div></div>
      <div class="stat"><div class="num">100</div><div class="label">총점</div></div>
      <div class="stat"><div class="num">4</div><div class="label">카테고리</div></div>
    </div>
  </div>

  <div class="cats">
    <div class="cat" style="--bar: #fbbf24;">
      <div class="row"><span class="name">연습</span><span class="count">3 문제 · 10점</span></div>
      <p class="desc">진리표에서 불 대수 식 유도, 3-입력 AND 게이트, 2:1 멀티플렉서</p>
    </div>
    <div class="cat" style="--bar: #f5a83d;">
      <div class="row"><span class="name">미션</span><span class="count">4 문제 · 16점</span></div>
      <p class="desc">NOR 게이트만으로 NOT·AND·XOR 구현, 21세기 윤년 판독기</p>
    </div>
    <div class="cat" style="--bar: #d97706;">
      <div class="row"><span class="name">프로젝트 1</span><span class="count">6 문제 · 42점</span></div>
      <p class="desc">반/전/3비트 가산기 → 보수 계산기 → ÷3 연산기 → 7-세그먼트 출력</p>
    </div>
    <div class="cat" style="--bar: #b45309;">
      <div class="row"><span class="name">프로젝트 2</span><span class="count">5 문제 · 32점</span></div>
      <p class="desc">2비트 / 2,3비트 비교기 → 대피소 배정 로직 → 7-세그먼트 표시</p>
    </div>
  </div>

  <p class="cta">
    <a href="/register">계정을 등록</a>하고 문제를 풀어보세요.
  </p>
</div>
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
        # HTML format so the hero + category-card layout renders without
        # markdown-quirk fights.
        page = Pages.query.filter_by(route="index").first()
        if page is None:
            db.session.add(Pages(
                title=CTF_NAME,
                route="index",
                content=INDEX_CONTENT,
                draft=False,
                hidden=False,
                auth_required=False,
                format="html",
            ))
            db.session.commit()
            print("[bootstrap] Index page created")
        else:
            page.title = CTF_NAME
            page.content = INDEX_CONTENT
            page.format = "html"
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

        if SEED_DEMO_DATA:
            _seed_demo_data()
        else:
            print("[bootstrap] CTFD_DEMO_DATA=false — skipping demo seed")


def _seed_demo_data() -> None:
    """Create the 4 demo teams + their Solves with realistic timestamp spread.
    Idempotent: skips users that already exist, and skips Solves seeding for
    users that already have any Solves recorded."""
    now = datetime.datetime.utcnow()
    for team in DEMO_TEAMS:
        user = Users.query.filter_by(name=team["name"]).first()
        created_user = False
        if user is None:
            user = Users(
                name=team["name"],
                email=team["email"],
                password=DEMO_PASSWORD,
                type="user",
                verified=True,
                hidden=False,
            )
            db.session.add(user)
            db.session.commit()  # need user.id below
            created_user = True

        if Solves.query.filter_by(user_id=user.id).first() is not None:
            if created_user:
                print(f"[demo] {team['name']} created (user existed without solves? skipping seed)")
            continue

        for chal_id, minutes_ago in team["solves"]:
            solve = Solves(
                user_id=user.id,
                team_id=None,
                challenge_id=chal_id,
                ip="127.0.0.1",
                provided="(demo seed).dig",
            )
            solve.date = now - datetime.timedelta(minutes=minutes_ago)
            db.session.add(solve)
        db.session.commit()
        print(
            f"[demo] {team['name']}: "
            f"{'created + ' if created_user else 'existing user, '}"
            f"{len(team['solves'])} solves seeded"
        )


if __name__ == "__main__":
    main()
