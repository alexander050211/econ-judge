"""Bootstrap CTFd on first container start: create admin, mark setup complete,
seed the 18 econ-judge challenges and a minimal index page. Idempotent —
running on every boot lets the deploy survive Render free tier's ephemeral
disk."""

from __future__ import annotations

import datetime
import importlib.util
import json
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

# Freeze (Unix timestamp). When set and the current time is past this value,
# the /my-score endpoint reports scores frozen at that timestamp and tags
# the response with frozen=True. Defer: leave unset until the camp day,
# then set CTFD_FREEZE_AT to (contest_end - desired_freeze_offset_seconds)
# via Render's Environment panel. Unset (None) = no freeze.
_freeze_env = os.environ.get("CTFD_FREEZE_AT", "").strip()
try:
    FREEZE_AT = int(_freeze_env) if _freeze_env else None
except ValueError:
    print(f"[bootstrap] ignoring invalid CTFD_FREEZE_AT={_freeze_env!r}")
    FREEZE_AT = None

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

/* Camp is Korean-only — hide CTFd's built-in language switcher. The
   dropdown is wrapped in a navbar <li> that contains a form with
   x-data="LanguageForm"; :has() targets that <li> directly. CSS
   :has() is broadly supported (Chrome 105+, Safari 15.4+, FF 121+). */
.navbar li.nav-item:has(form[x-data="LanguageForm"]) {
  display: none !important;
}

/* Stack the submit button below the input block instead of CTFd's default
   col-sm-8 / col-sm-4 side-by-side. For a drag-drop dropzone, putting the
   submit beside it makes the zone awkwardly narrow. */
.submit-row > .col-sm-8,
.submit-row > .col-sm-4 {
  flex: 0 0 100% !important;
  max-width: 100% !important;
}
.submit-row > .key-submit {
  margin-top: 0.85rem !important;
}
.submit-row > .key-submit .challenge-submit {
  height: auto !important;
  padding: 0.65rem 1rem !important;
  background: var(--sens-brand) !important;
  border-color: var(--sens-brand) !important;
  color: #fff !important;
  font-weight: 600 !important;
}
.submit-row > .key-submit .challenge-submit:hover {
  background: var(--sens-brand-dark) !important;
  border-color: var(--sens-brand-dark) !important;
}
</style>
<script defer src="/plugins/econ_judge/assets/scoreboard.js"></script>
<script>
/* Inject a "내 점수" navbar link before the Challenges link. The full
   Scoreboard link gets hidden by CTFd itself once score_visibility=admins
   (server-side template gate), so this gives mentees a clear destination
   for their personal progress view. Admins keep their Scoreboard link. */
(function() {
  function inject() {
    if (document.getElementById('econ-my-score-link')) return;
    var links = document.querySelectorAll('.navbar-nav .nav-link');
    var chalLink = null;
    for (var i = 0; i < links.length; i++) {
      var href = links[i].getAttribute('href') || '';
      if (href === '/challenges' || href.endsWith('/challenges')) {
        chalLink = links[i];
        break;
      }
    }
    if (!chalLink) return;
    var chalLi = chalLink.parentElement;
    if (!chalLi || chalLi.tagName !== 'LI') return;
    var li = document.createElement('li');
    li.className = 'nav-item';
    li.id = 'econ-my-score-link';
    li.innerHTML = '<a class="nav-link" href="/my-score">내 점수</a>';
    chalLi.parentElement.insertBefore(li, chalLi);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', inject);
  } else {
    inject();
  }
  /* CTFd re-renders the navbar after some auth flows; defensive re-tries */
  setTimeout(inject, 200);
  setTimeout(inject, 1000);
})();
</script>
"""

# HTML index page (CTFd Pages.format = "html"). Designed to fit inside
# CTFd's standard container — no negative-margin breakouts.
INDEX_CONTENT = """\
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=IBM+Plex+Mono:wght@400;500&display=swap');

:root {
  --sens-brand:      #f5a83d;
  --sens-brand-dark: #d69336;
  --sens-brand-ink:  #7a5a1f;
  --sens-brand-soft: #fff4e0;
  --paper:           #faf6ef;
  --paper-rule:      #e8dcc8;
  --ink-heavy:       #1e1508;
  --ink-mid:         #4a3b1c;
  --ink-light:       #9a8060;
  --border-fine:     #c8b48a;
  --mono:            'IBM Plex Mono', 'Courier New', monospace;
  --serif:           'DM Serif Display', Georgia, serif;
  --korean:          'Pretendard Variable', Pretendard, sans-serif;
}

.ec-shell *,
.ec-shell *::before,
.ec-shell *::after {
  box-sizing: border-box;
}

.ec-shell {
  font-family: var(--korean);
  background: var(--paper);
  color: var(--ink-heavy);
  position: relative;
  overflow: hidden;
}

.ec-frame {
  position: relative;
  border: 1.5px solid var(--border-fine);
  margin: 2rem 0 2.5rem;
  padding: 0;
  background: var(--paper);
}

.ec-frame::before {
  content: '';
  position: absolute;
  inset: 5px;
  border: 0.5px solid var(--border-fine);
  pointer-events: none;
  z-index: 0;
}

.ec-frame-corner {
  position: absolute;
  width: 14px;
  height: 14px;
  z-index: 2;
}
.ec-frame-corner::before,
.ec-frame-corner::after {
  content: '';
  position: absolute;
  background: var(--sens-brand-dark);
}
.ec-frame-corner.tl { top: -1px;  left: -1px;  }
.ec-frame-corner.tr { top: -1px;  right: -1px; }
.ec-frame-corner.bl { bottom: -1px; left: -1px; }
.ec-frame-corner.br { bottom: -1px; right: -1px; }

.ec-frame-corner.tl::before { width: 14px; height: 1.5px; top: 0;    left: 0; }
.ec-frame-corner.tl::after  { width: 1.5px; height: 14px; top: 0;    left: 0; }
.ec-frame-corner.tr::before { width: 14px; height: 1.5px; top: 0;    right: 0; }
.ec-frame-corner.tr::after  { width: 1.5px; height: 14px; top: 0;    right: 0; }
.ec-frame-corner.bl::before { width: 14px; height: 1.5px; bottom: 0; left: 0; }
.ec-frame-corner.bl::after  { width: 1.5px; height: 14px; bottom: 0; left: 0; }
.ec-frame-corner.br::before { width: 14px; height: 1.5px; bottom: 0; right: 0; }
.ec-frame-corner.br::after  { width: 1.5px; height: 14px; bottom: 0; right: 0; }

.ec-title-block {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 0;
  border-bottom: 1.5px solid var(--border-fine);
  position: relative;
  z-index: 1;
  background: var(--paper);
}

.ec-title-main {
  padding: 2.25rem 2.5rem 2rem;
  border-right: 1.5px solid var(--border-fine);
}

.ec-meta-block {
  display: flex;
  flex-direction: column;
  min-width: 190px;
  max-width: 220px;
  font-family: var(--mono);
}

.ec-meta-row {
  display: flex;
  flex-direction: column;
  padding: 0.55rem 1.1rem;
  border-bottom: 1px solid var(--border-fine);
  gap: 0.1rem;
  flex: 1;
}

.ec-meta-row:last-child {
  border-bottom: none;
}

.ec-meta-label {
  font-size: 0.6rem;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--ink-light);
  font-weight: 500;
}

.ec-meta-value {
  font-size: 0.78rem;
  color: var(--ink-mid);
  font-weight: 400;
  letter-spacing: 0.02em;
}

.ec-camp-id {
  font-family: var(--mono);
  font-size: 0.68rem;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--ink-light);
  margin-bottom: 0.65rem;
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.ec-camp-id::before {
  content: '';
  display: inline-block;
  width: 18px;
  height: 1px;
  background: var(--sens-brand);
  flex-shrink: 0;
}

.ec-wordmark {
  font-family: var(--serif);
  font-size: clamp(2.8rem, 6vw, 4.4rem);
  line-height: 0.95;
  color: var(--ink-heavy);
  letter-spacing: -0.01em;
  margin: 0 0 0.5rem;
  position: relative;
}

.ec-wordmark-accent {
  color: var(--sens-brand-dark);
}

.ec-subtitle {
  font-family: var(--korean);
  font-size: 0.88rem;
  font-weight: 400;
  color: var(--ink-mid);
  letter-spacing: 0.03em;
  margin-top: 0.7rem;
}

.ec-body-panel {
  display: grid;
  grid-template-columns: 1fr 1fr;
  position: relative;
  z-index: 1;
}

.ec-left-col {
  padding: 2rem 2.5rem 2.5rem;
  border-right: 1.5px solid var(--border-fine);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 1.8rem;
}

.ec-right-col {
  padding: 2rem 2.5rem 2.5rem;
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.ec-stats-label {
  font-family: var(--mono);
  font-size: 0.6rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--ink-light);
  margin-bottom: 0.9rem;
}

.ec-stats-table {
  display: flex;
  flex-direction: column;
  gap: 0;
  border: 1px solid var(--border-fine);
}

.ec-stat-row {
  display: grid;
  grid-template-columns: 5.5rem 1fr;
  border-bottom: 1px solid var(--border-fine);
  align-items: stretch;
}

.ec-stat-row:last-child {
  border-bottom: none;
}

.ec-stat-num {
  font-family: var(--mono);
  font-size: 2.05rem;
  font-weight: 500;
  color: var(--sens-brand-dark);
  line-height: 1;
  padding: 0.7rem 1rem 0.65rem;
  border-right: 1px solid var(--border-fine);
  display: flex;
  align-items: center;
  background: var(--sens-brand-soft);
  letter-spacing: -0.02em;
}

.ec-stat-desc {
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 0.55rem 1rem;
  gap: 0.1rem;
}

.ec-stat-ko {
  font-family: var(--korean);
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--ink-heavy);
  letter-spacing: 0.01em;
}

.ec-stat-en {
  font-family: var(--mono);
  font-size: 0.62rem;
  color: var(--ink-light);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.ec-schematic {
  flex: 1;
  display: flex;
  align-items: flex-end;
  opacity: 0.55;
}

.ec-greeting {
  font-family: var(--korean);
  font-size: 0.92rem;
  line-height: 1.7;
  color: var(--ink-mid);
  font-weight: 400;
  padding: 1rem 1.1rem;
  border-left: 2.5px solid var(--sens-brand);
  background: linear-gradient(to right, rgba(245,168,61,0.06), transparent);
}

.ec-cta-group {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  margin-top: auto;
}

.ec-btn-primary {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.6rem;
  background: var(--sens-brand);
  color: var(--ink-heavy);
  font-family: var(--korean);
  font-size: 0.97rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-decoration: none;
  padding: 0.85rem 1.8rem;
  border: 1.5px solid var(--sens-brand-dark);
  position: relative;
  transition: background 0.15s ease, box-shadow 0.15s ease, transform 0.12s ease;
  box-shadow: 3px 3px 0 var(--sens-brand-dark);
}

.ec-btn-primary:hover {
  background: var(--sens-brand-dark);
  color: var(--ink-heavy);
  text-decoration: none;
  box-shadow: 5px 5px 0 var(--sens-brand-ink);
  transform: translate(-1px, -1px);
}

.ec-btn-primary:active {
  transform: translate(1px, 1px);
  box-shadow: 1px 1px 0 var(--sens-brand-dark);
}

.ec-btn-arrow {
  font-family: var(--mono);
  font-size: 1rem;
  display: inline-block;
  transition: transform 0.15s ease;
}

.ec-btn-primary:hover .ec-btn-arrow {
  transform: translateX(3px);
}

.ec-btn-secondary {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  background: transparent;
  color: var(--ink-mid);
  font-family: var(--korean);
  font-size: 0.84rem;
  font-weight: 500;
  letter-spacing: 0.03em;
  text-decoration: none;
  padding: 0.6rem 1.4rem;
  border: 1px solid var(--border-fine);
  transition: border-color 0.15s ease, color 0.15s ease, background 0.15s ease;
}

.ec-btn-secondary:hover {
  border-color: var(--sens-brand);
  color: var(--sens-brand-ink);
  background: var(--sens-brand-soft);
  text-decoration: none;
}

.ec-steps-label {
  font-family: var(--mono);
  font-size: 0.6rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--ink-light);
  margin-bottom: 0.75rem;
}

.ec-steps {
  display: flex;
  flex-direction: column;
  gap: 0;
  border: 1px solid var(--border-fine);
}

.ec-step {
  display: grid;
  grid-template-columns: 2.4rem 1fr;
  border-bottom: 1px solid var(--border-fine);
  align-items: stretch;
}

.ec-step:last-child {
  border-bottom: none;
}

.ec-step-num {
  font-family: var(--mono);
  font-size: 0.7rem;
  font-weight: 500;
  color: var(--sens-brand-dark);
  background: var(--sens-brand-soft);
  border-right: 1px solid var(--border-fine);
  display: flex;
  align-items: center;
  justify-content: center;
  letter-spacing: 0.05em;
}

.ec-step-text {
  font-family: var(--korean);
  font-size: 0.82rem;
  color: var(--ink-mid);
  padding: 0.6rem 0.85rem;
  line-height: 1.5;
  font-weight: 400;
}

.ec-step-text strong {
  color: var(--ink-heavy);
  font-weight: 600;
}

.ec-revision-strip {
  border-top: 1.5px solid var(--border-fine);
  display: flex;
  align-items: stretch;
  font-family: var(--mono);
  font-size: 0.62rem;
  color: var(--ink-light);
  letter-spacing: 0.07em;
  position: relative;
  z-index: 1;
}

.ec-rev-cell {
  padding: 0.45rem 1rem;
  border-right: 1px solid var(--border-fine);
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 0.1rem;
  flex: 1;
}

.ec-rev-cell:last-child {
  border-right: none;
}

.ec-rev-key {
  text-transform: uppercase;
  font-size: 0.55rem;
  letter-spacing: 0.12em;
  opacity: 0.7;
}

.ec-rev-val {
  color: var(--ink-mid);
  font-size: 0.65rem;
}

.ec-grid-bg {
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  overflow: hidden;
}

@media (max-width: 720px) {
  .ec-title-block {
    grid-template-columns: 1fr;
  }
  .ec-meta-block {
    flex-direction: row;
    max-width: 100%;
    border-top: 1.5px solid var(--border-fine);
    border-right: none;
  }
  .ec-meta-row {
    border-right: 1px solid var(--border-fine);
    border-bottom: none;
  }
  .ec-meta-row:last-child {
    border-right: none;
  }
  .ec-title-main {
    border-right: none;
  }
  .ec-body-panel {
    grid-template-columns: 1fr;
  }
  .ec-left-col {
    border-right: none;
    border-bottom: 1.5px solid var(--border-fine);
  }
  .ec-wordmark {
    font-size: clamp(2.4rem, 12vw, 3.2rem);
  }
  .ec-revision-strip {
    flex-wrap: wrap;
  }
  .ec-rev-cell {
    min-width: 50%;
    border-bottom: 1px solid var(--border-fine);
  }
}

@media (max-width: 480px) {
  .ec-title-main {
    padding: 1.5rem 1.25rem 1.25rem;
  }
  .ec-left-col,
  .ec-right-col {
    padding: 1.5rem 1.25rem;
  }
  .ec-meta-block {
    flex-direction: column;
  }
  .ec-meta-row {
    border-right: none;
    border-bottom: 1px solid var(--border-fine);
  }
  .ec-stat-num {
    font-size: 1.7rem;
  }
}

@keyframes ec-appear {
  from { opacity: 0; transform: translateY(6px); }
  to   { opacity: 1; transform: translateY(0); }
}

.ec-frame {
  animation: ec-appear 0.4s ease both;
}
</style>

<div class="ec-shell">

  <svg class="ec-grid-bg" xmlns="http://www.w3.org/2000/svg" width="100%" height="100%">
    <defs>
      <pattern id="ec-minor" x="0" y="0" width="20" height="20" patternUnits="userSpaceOnUse">
        <path d="M 20 0 L 0 0 0 20" fill="none" stroke="#c8b48a" stroke-width="0.3" opacity="0.35"/>
      </pattern>
      <pattern id="ec-major" x="0" y="0" width="100" height="100" patternUnits="userSpaceOnUse">
        <rect width="100" height="100" fill="url(#ec-minor)"/>
        <path d="M 100 0 L 0 0 0 100" fill="none" stroke="#c8b48a" stroke-width="0.7" opacity="0.4"/>
      </pattern>
    </defs>
    <rect width="100%" height="100%" fill="url(#ec-major)"/>
  </svg>

  <div class="ec-frame">
    <span class="ec-frame-corner tl"></span>
    <span class="ec-frame-corner tr"></span>
    <span class="ec-frame-corner bl"></span>
    <span class="ec-frame-corner br"></span>

    <div class="ec-title-block">
      <div class="ec-title-main">
        <div class="ec-camp-id">SNU SENS &middot; 2026 공헌 공드림 캠프</div>
        <h1 class="ec-wordmark">E&#8209;CON<br><span class="ec-wordmark-accent">논설</span></h1>
        <p class="ec-subtitle">디지털 논리회로 설계 자동채점 시스템</p>
      </div>
      <div class="ec-meta-block">
        <div class="ec-meta-row">
          <span class="ec-meta-label">Document No.</span>
          <span class="ec-meta-value">SENS-2026-001</span>
        </div>
        <div class="ec-meta-row">
          <span class="ec-meta-label">Rev.</span>
          <span class="ec-meta-value">1.0</span>
        </div>
        <div class="ec-meta-row">
          <span class="ec-meta-label">Date</span>
          <span class="ec-meta-value">2026-05-21</span>
        </div>
        <div class="ec-meta-row">
          <span class="ec-meta-label">Sheet</span>
          <span class="ec-meta-value">1 / 1</span>
        </div>
        <div class="ec-meta-row">
          <span class="ec-meta-label">Status</span>
          <span class="ec-meta-value" style="color: var(--sens-brand-dark); font-weight: 500;">LIVE</span>
        </div>
      </div>
    </div>

    <div class="ec-body-panel">

      <div class="ec-left-col">

        <div>
          <div class="ec-stats-label">// system specifications</div>
          <div class="ec-stats-table">
            <div class="ec-stat-row">
              <div class="ec-stat-num">18</div>
              <div class="ec-stat-desc">
                <span class="ec-stat-ko">도전 과제</span>
                <span class="ec-stat-en">Challenges</span>
              </div>
            </div>
            <div class="ec-stat-row">
              <div class="ec-stat-num">100</div>
              <div class="ec-stat-desc">
                <span class="ec-stat-ko">점 만점</span>
                <span class="ec-stat-en">Total points</span>
              </div>
            </div>
            <div class="ec-stat-row">
              <div class="ec-stat-num">4</div>
              <div class="ec-stat-desc">
                <span class="ec-stat-ko">개 조</span>
                <span class="ec-stat-en">Teams</span>
              </div>
            </div>
          </div>
        </div>

        <div class="ec-schematic" aria-hidden="true">
          <svg viewBox="0 0 320 110" xmlns="http://www.w3.org/2000/svg"
               width="100%" height="auto" fill="none"
               stroke="var(--sens-brand-dark)" stroke-linecap="round" stroke-linejoin="round">

            <text x="4" y="28" font-family="'IBM Plex Mono', monospace" font-size="8"
                  fill="var(--ink-light)" letter-spacing="0.05em">A</text>
            <text x="4" y="58" font-family="'IBM Plex Mono', monospace" font-size="8"
                  fill="var(--ink-light)" letter-spacing="0.05em">B</text>

            <line x1="16" y1="26" x2="60" y2="26" stroke-width="1.2"/>
            <line x1="16" y1="56" x2="60" y2="56" stroke-width="1.2"/>

            <path d="M60 14 L60 68 L80 68 Q110 68 110 41 Q110 14 80 14 Z" stroke-width="1.2"/>
            <text x="63" y="44" font-family="'IBM Plex Mono', monospace" font-size="7"
                  fill="var(--ink-light)">U1</text>

            <line x1="110" y1="41" x2="145" y2="41" stroke-width="1.2"/>

            <circle cx="145" cy="41" r="2.5" fill="var(--sens-brand-dark)" stroke="none"/>

            <line x1="145" y1="41" x2="145" y2="18" stroke-width="1.2"/>
            <line x1="145" y1="18" x2="168" y2="18" stroke-width="1.2"/>

            <path d="M168 9 L168 27 L186 18 Z" stroke-width="1.2"/>
            <circle cx="189.5" cy="18" r="3.5" stroke-width="1.2"/>

            <line x1="193" y1="18" x2="218" y2="18" stroke-width="1.2"/>

            <line x1="145" y1="41" x2="145" y2="64" stroke-width="1.2"/>
            <line x1="145" y1="64" x2="168" y2="64" stroke-width="1.2"/>

            <path d="M172 52 L172 76 Q198 76 198 64 Q198 52 172 52 Z" stroke-width="1.2"/>
            <path d="M168 52 Q174 64 168 76" stroke-width="1.2"/>
            <path d="M163 52 Q169 64 163 76" stroke-width="1.2"/>

            <text x="174" y="67" font-family="'IBM Plex Mono', monospace" font-size="7"
                  fill="var(--ink-light)">U2</text>

            <line x1="218" y1="18" x2="228" y2="18" stroke-width="1.2"/>
            <line x1="228" y1="18" x2="228" y2="52" stroke-width="1.2" stroke-dasharray="3 2"/>
            <line x1="228" y1="52" x2="172" y2="52" stroke-width="1.2"/>

            <line x1="198" y1="64" x2="240" y2="64" stroke-width="1.2"/>

            <text x="244" y="67" font-family="'IBM Plex Mono', monospace" font-size="8"
                  fill="var(--sens-brand-dark)" letter-spacing="0.05em">Y</text>

            <rect x="270" y="86" width="46" height="20" stroke-width="0.8" stroke="var(--border-fine)" fill="none"/>
            <text x="273" y="97" font-family="'IBM Plex Mono', monospace" font-size="6.5"
                  fill="var(--ink-light)" letter-spacing="0.06em">DIG-SCH-01</text>

            <line x1="290" y1="2" x2="316" y2="2" stroke-width="1" stroke="var(--border-fine)"/>
            <line x1="293" y1="5" x2="313" y2="5" stroke-width="0.7" stroke="var(--border-fine)"/>
            <line x1="296" y1="8" x2="310" y2="8" stroke-width="0.5" stroke="var(--border-fine)"/>
            <text x="281" y="2" font-family="'IBM Plex Mono', monospace" font-size="6"
                  fill="var(--ink-light)">VCC</text>
          </svg>
        </div>

      </div>

      <div class="ec-right-col">

        <div class="ec-greeting">
          여러분의 회로가 올바르게 동작하는지 자동으로 검증해드립니다.<br>
          <span style="font-size: 0.82rem; color: var(--ink-light); font-weight: 400;">
            Digital 파일을 제출하면 채점 엔진이 즉시 시뮬레이션하여 결과를 반환합니다.
          </span>
        </div>

        <div>
          <div class="ec-steps-label">// 시작 절차 &nbsp; PROCEDURE</div>
          <div class="ec-steps">
            <div class="ec-step">
              <div class="ec-step-num">01</div>
              <div class="ec-step-text"><strong>도전 과제 목록</strong>에서 문제를 선택합니다.</div>
            </div>
            <div class="ec-step">
              <div class="ec-step-num">02</div>
              <div class="ec-step-text">Digital에서 회로를 설계하고 <strong>.dig 파일</strong>을 저장합니다.</div>
            </div>
            <div class="ec-step">
              <div class="ec-step-num">03</div>
              <div class="ec-step-text">파일을 업로드하면 <strong>자동 채점</strong>이 즉시 실행됩니다.</div>
            </div>
            <div class="ec-step">
              <div class="ec-step-num">04</div>
              <div class="ec-step-text">결과를 확인하고 필요하면 <strong>재제출</strong>하세요.</div>
            </div>
          </div>
        </div>

        <div class="ec-cta-group">
          <a href="/challenges" class="ec-btn-primary">
            도전 시작하기
            <span class="ec-btn-arrow">&#x2192;</span>
          </a>
          <a href="/scoreboard" class="ec-btn-secondary">
            현황판 보기
          </a>
        </div>

      </div>
    </div>

    <div class="ec-revision-strip">
      <div class="ec-rev-cell">
        <span class="ec-rev-key">Drawn by</span>
        <span class="ec-rev-val">SENS Engineering</span>
      </div>
      <div class="ec-rev-cell">
        <span class="ec-rev-key">Tool</span>
        <span class="ec-rev-val">econ-judge v1.0</span>
      </div>
      <div class="ec-rev-cell">
        <span class="ec-rev-key">Format</span>
        <span class="ec-rev-val">CTFd 3.8.5 / Bootstrap 5</span>
      </div>
      <div class="ec-rev-cell">
        <span class="ec-rev-key">Checked</span>
        <span class="ec-rev-val">SNU SENS 2026</span>
      </div>
    </div>

  </div>

</div>
"""

# /my-score page content — the anti-toxicity scoreboard surrogate. Mentees
# only see their own score and the (anonymized) leader's score, never a
# ranked list. Designed by the ui-designer subagent in matching schematic-
# notebook aesthetic. Calls /api/v1/digital/my-score (econ_judge plugin)
# which bypasses score_visibility=admins to serve this restricted slice.
MY_SCORE_CONTENT = """\
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=IBM+Plex+Mono:wght@400;500&display=swap');
  @import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable.min.css');

  :root {
    --sens-brand:      #f5a83d;
    --sens-brand-dark: #d69336;
    --sens-brand-ink:  #7a5a1f;
    --sens-brand-soft: #fff4e0;
    --paper:           #faf6ef;
    --paper-rule:      #e8dcc8;
    --ink-heavy:       #1e1508;
    --ink-mid:         #4a3b1c;
    --ink-light:       #9a8060;
    --border-fine:     #c8b48a;
    --gap-behind:      #c0392b;
    --gap-ahead:       #2e7d32;
  }

  #ms-root {
    font-family: 'Pretendard Variable', 'Pretendard', sans-serif;
    background-color: var(--paper);
    background-image:
      linear-gradient(var(--paper-rule) 1px, transparent 1px),
      linear-gradient(90deg, var(--paper-rule) 1px, transparent 1px);
    background-size: 24px 24px;
    color: var(--ink-mid);
    padding: 0;
    margin: 0;
    box-sizing: border-box;
  }

  #ms-root *, #ms-root *::before, #ms-root *::after {
    box-sizing: inherit;
  }

  /* ─── OUTER FRAME ─── */
  .ms-frame {
    border: 1.5px solid var(--border-fine);
    outline: 0.5px solid var(--paper-rule);
    outline-offset: -4px;
    background: rgba(250, 246, 239, 0.94);
    max-width: 820px;
    margin: 32px auto;
    padding: 0;
  }

  /* ─── HEADER STRIP ─── */
  .ms-header {
    border-bottom: 1.5px solid var(--border-fine);
    padding: 20px 28px 16px;
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
  }

  .ms-header-left {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .ms-eyebrow {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--ink-light);
  }

  .ms-eyebrow::before {
    content: '// ';
    color: var(--sens-brand);
  }

  .ms-title {
    font-family: 'DM Serif Display', serif;
    font-size: 36px;
    line-height: 1;
    color: var(--ink-heavy);
    margin: 4px 0 0;
    letter-spacing: -0.01em;
  }

  .ms-subtitle {
    font-size: 13px;
    color: var(--ink-light);
    margin-top: 6px;
    line-height: 1.45;
    max-width: 380px;
  }

  .ms-header-right {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    line-height: 1.8;
    color: var(--ink-light);
    text-align: right;
    flex-shrink: 0;
    padding-top: 2px;
  }

  .ms-header-right .ms-meta-key {
    color: var(--ink-light);
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }

  .ms-header-right .ms-meta-val {
    color: var(--ink-mid);
    font-weight: 500;
  }

  .ms-status-live {
    color: var(--sens-brand-dark);
    font-weight: 500;
  }

  .ms-status-frozen {
    color: var(--ink-light);
    font-weight: 500;
  }

  .ms-frozen-tag {
    display: inline-block;
    background: var(--paper-rule);
    border: 1px solid var(--border-fine);
    font-family: 'IBM Plex Mono', monospace;
    font-size: 9px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--ink-light);
    padding: 2px 6px;
    margin-left: 6px;
    vertical-align: middle;
  }

  /* ─── BODY ─── */
  .ms-body {
    padding: 24px 28px;
    display: flex;
    flex-direction: column;
    gap: 20px;
  }

  /* ─── SKELETON ─── */
  .ms-skeleton-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }

  .ms-skeleton-card {
    height: 160px;
    border: 1.5px solid var(--paper-rule);
    background: linear-gradient(90deg, var(--paper-rule) 25%, #ede8de 50%, var(--paper-rule) 75%);
    background-size: 200% 100%;
    animation: ms-shimmer 1.4s ease infinite;
  }

  .ms-skeleton-bar {
    height: 38px;
    border: 1.5px solid var(--paper-rule);
    background: linear-gradient(90deg, var(--paper-rule) 25%, #ede8de 50%, var(--paper-rule) 75%);
    background-size: 200% 100%;
    animation: ms-shimmer 1.4s ease infinite;
  }

  .ms-skeleton-delta {
    height: 60px;
    border: 1.5px solid var(--paper-rule);
    background: linear-gradient(90deg, var(--paper-rule) 25%, #ede8de 50%, var(--paper-rule) 75%);
    background-size: 200% 100%;
    animation: ms-shimmer 1.4s ease infinite;
  }

  @keyframes ms-shimmer {
    0%   { background-position: 200% 0; }
    100% { background-position: -200% 0; }
  }

  /* ─── ERROR STATE ─── */
  .ms-error-block {
    border: 1.5px solid var(--border-fine);
    padding: 32px 24px;
    text-align: center;
  }

  .ms-error-label {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--ink-light);
    margin-bottom: 10px;
  }

  .ms-error-label::before {
    content: '// ';
    color: var(--sens-brand);
  }

  .ms-error-msg {
    font-size: 14px;
    color: var(--ink-mid);
    margin-bottom: 18px;
    line-height: 1.5;
  }

  .ms-retry-btn {
    display: inline-block;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 8px 20px;
    border: 1.5px solid var(--sens-brand);
    background: var(--sens-brand-soft);
    color: var(--sens-brand-ink);
    cursor: pointer;
    transition: background 0.15s ease;
  }

  .ms-retry-btn:hover {
    background: var(--sens-brand);
    color: var(--ink-heavy);
  }

  /* ─── EMPTY STATES ─── */
  .ms-empty-block {
    border: 1.5px solid var(--paper-rule);
    padding: 40px 24px;
    text-align: center;
  }

  .ms-empty-label {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--ink-light);
    margin-bottom: 10px;
  }

  .ms-empty-label::before {
    content: '// ';
    color: var(--paper-rule);
  }

  .ms-empty-msg {
    font-size: 14px;
    color: var(--ink-light);
    line-height: 1.5;
  }

  /* ─── SCORE CARDS ─── */
  .ms-cards-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0;
    border: 1.5px solid var(--border-fine);
  }

  .ms-card {
    padding: 22px 24px 24px;
    position: relative;
    transition: background 0.2s ease;
  }

  .ms-card-own {
    background: var(--sens-brand-soft);
    border-right: 1px solid var(--border-fine);
  }

  .ms-card-leader {
    background: var(--paper);
  }

  .ms-card-label {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    margin-bottom: 10px;
  }

  .ms-card-own .ms-card-label {
    color: var(--sens-brand-dark);
  }

  .ms-card-own .ms-card-label::before {
    content: '// ';
    color: var(--sens-brand);
  }

  .ms-card-leader .ms-card-label {
    color: var(--ink-light);
  }

  .ms-card-leader .ms-card-label::before {
    content: '// ';
    color: var(--paper-rule);
  }

  .ms-card-team-name {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    color: var(--ink-light);
    margin-bottom: 8px;
    letter-spacing: 0.04em;
  }

  .ms-card-own .ms-card-team-name {
    color: var(--sens-brand-ink);
  }

  .ms-score-number {
    font-family: 'DM Serif Display', serif;
    font-size: 64px;
    line-height: 1;
    letter-spacing: -0.02em;
    color: var(--ink-heavy);
    transition: color 0.3s ease;
  }

  .ms-card-leader .ms-score-number {
    color: var(--ink-light);
  }

  .ms-score-unit {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
    color: var(--ink-light);
    margin-top: 6px;
    letter-spacing: 0.06em;
  }

  .ms-card-own .ms-score-unit {
    color: var(--sens-brand-ink);
  }

  .ms-score-fraction {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    color: var(--ink-light);
    opacity: 0.7;
  }

  /* ─── PROGRESS BAR (own card only) ─── */
  .ms-progress-wrap {
    margin-top: 16px;
  }

  .ms-progress-label {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--sens-brand-ink);
    margin-bottom: 5px;
    opacity: 0.7;
  }

  .ms-progress-track {
    height: 6px;
    background: var(--paper-rule);
    border: 1px solid var(--border-fine);
    position: relative;
    overflow: hidden;
  }

  .ms-progress-fill {
    height: 100%;
    background: linear-gradient(90deg, var(--sens-brand-dark), var(--sens-brand));
    transition: width 0.8s cubic-bezier(0.4, 0, 0.2, 1);
    width: 0%;
  }

  /* ─── DELTA ROW ─── */
  .ms-delta-row {
    border: 1.5px solid var(--border-fine);
    padding: 18px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    background: var(--paper);
  }

  .ms-delta-label {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--ink-light);
    flex-shrink: 0;
  }

  .ms-delta-label::before {
    content: '// ';
    color: var(--paper-rule);
  }

  .ms-delta-value {
    font-family: 'DM Serif Display', serif;
    font-size: 28px;
    line-height: 1;
    letter-spacing: -0.01em;
  }

  .ms-delta-behind {
    color: var(--ink-mid);
  }

  .ms-delta-tied {
    color: var(--sens-brand-dark);
  }

  .ms-delta-ahead {
    color: var(--sens-brand-dark);
  }

  .ms-delta-sub {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    color: var(--ink-light);
    text-align: right;
    flex-shrink: 0;
    line-height: 1.6;
  }

  /* ─── FOOTER STRIP ─── */
  .ms-footer {
    border-top: 1.5px solid var(--border-fine);
    padding: 12px 28px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .ms-footer-left {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--ink-light);
    line-height: 1.7;
  }

  .ms-footer-right {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--ink-light);
    text-align: right;
    line-height: 1.7;
  }

  .ms-footer-divider {
    width: 1px;
    height: 28px;
    background: var(--border-fine);
    flex-shrink: 0;
  }

  /* ─── LEADING STATE: own card accent ─── */
  .ms-card-own.ms-is-leader {
    background: linear-gradient(135deg, var(--sens-brand-soft) 0%, #fff8e8 100%);
  }

  .ms-leading-mark {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--sens-brand-dark);
    border: 1px solid var(--sens-brand);
    background: rgba(245, 168, 61, 0.12);
    padding: 2px 6px;
    display: inline-block;
    margin-top: 8px;
  }

  /* ─── RESPONSIVE ─── */
  @media (max-width: 600px) {
    .ms-frame {
      margin: 0;
      border-left: none;
      border-right: none;
    }

    .ms-header {
      flex-direction: column;
      gap: 12px;
      padding: 16px 18px 14px;
    }

    .ms-header-right {
      text-align: left;
      border-top: 1px solid var(--paper-rule);
      padding-top: 10px;
      width: 100%;
    }

    .ms-body {
      padding: 16px 18px;
    }

    .ms-cards-row {
      grid-template-columns: 1fr;
    }

    .ms-card-own {
      border-right: none;
      border-bottom: 1px solid var(--border-fine);
    }

    .ms-score-number {
      font-size: 52px;
    }

    .ms-delta-row {
      flex-direction: column;
      align-items: flex-start;
      gap: 8px;
      padding: 16px 18px;
    }

    .ms-delta-sub {
      text-align: left;
    }

    .ms-title {
      font-size: 28px;
    }

    .ms-footer {
      padding: 10px 18px;
      flex-direction: column;
      align-items: flex-start;
      gap: 6px;
    }

    .ms-footer-divider {
      display: none;
    }

    .ms-footer-right {
      text-align: left;
    }
  }
</style>

<div id="ms-root">
  <div class="ms-frame">
    <div class="ms-header">
      <div class="ms-header-left">
        <div class="ms-eyebrow">SNU SENS &middot; 2026 공헌 공드림 캠프</div>
        <div class="ms-title">내 점수<span id="ms-frozen-badge"></span></div>
        <div class="ms-subtitle">우리 조의 현재 진행 상황과 선두 조와의 격차를 확인하세요.</div>
      </div>
      <div class="ms-header-right">
        <div><span class="ms-meta-key">DOC</span>&nbsp;&nbsp;<span class="ms-meta-val">E-CON / SCORE-TRACK</span></div>
        <div><span class="ms-meta-key">STATUS</span>&nbsp;&nbsp;<span id="ms-status-val" class="ms-meta-val ms-status-live">LIVE</span></div>
        <div><span class="ms-meta-key">REFRESH</span>&nbsp;&nbsp;<span class="ms-meta-val">20s</span></div>
        <div><span class="ms-meta-key">UPDATED</span>&nbsp;&nbsp;<span class="ms-meta-val" id="ms-last-updated">--:--:--</span></div>
      </div>
    </div>

    <div class="ms-body" id="ms-body">
      <!-- content injected by JS -->
    </div>

    <div class="ms-footer">
      <div class="ms-footer-left">
        <div>DRAWN BY &nbsp; / &nbsp; E-CON 논설 Auto-Grader</div>
        <div>TOOL &nbsp; CTFd 3.8.5 + Digital.jar v0.31</div>
      </div>
      <div class="ms-footer-divider"></div>
      <div class="ms-footer-right">
        <div>REV &nbsp; 2026-A</div>
        <div>SNU SENS &nbsp; / &nbsp; 2026 SUMMER</div>
      </div>
    </div>
  </div>
</div>

<script>
(function() {
  'use strict';

  var REFRESH_MS = 20000;
  var API_URL = '/api/v1/digital/my-score';

  var body       = document.getElementById('ms-body');
  var lastUpdEl  = document.getElementById('ms-last-updated');
  var statusEl   = document.getElementById('ms-status-val');
  var frozenBadge = document.getElementById('ms-frozen-badge');

  var state = {
    ownScore: null,
    leaderScore: null,
    totalPoints: 100,
    frozen: false,
    teamName: null
  };

  var animFrame = null;
  var refreshTimer = null;
  var isHidden = false;

  /* ── VISIBILITY PAUSE ── */
  document.addEventListener('visibilitychange', function() {
    isHidden = document.hidden;
    if (!isHidden) {
      clearTimeout(refreshTimer);
      fetchData();
    }
  });

  /* ── TIME FORMATTING ── */
  function nowHMS() {
    var d = new Date();
    var pad = function(n) { return n < 10 ? '0' + n : String(n); };
    return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }

  /* ── COUNT-UP ANIMATION ── */
  function animateCount(el, from, to, duration) {
    if (animFrame) cancelAnimationFrame(animFrame);
    var start = null;
    function step(ts) {
      if (!start) start = ts;
      var prog = Math.min((ts - start) / duration, 1);
      var eased = 1 - Math.pow(1 - prog, 3);
      el.textContent = Math.round(from + (to - from) * eased);
      if (prog < 1) animFrame = requestAnimationFrame(step);
      else el.textContent = to;
    }
    animFrame = requestAnimationFrame(step);
  }

  /* ── SKELETON ── */
  function renderSkeleton() {
    body.innerHTML =
      '<div class="ms-skeleton-row">' +
        '<div class="ms-skeleton-card"></div>' +
        '<div class="ms-skeleton-card"></div>' +
      '</div>' +
      '<div class="ms-skeleton-delta"></div>' +
      '<div class="ms-skeleton-bar"></div>';
  }

  /* ── ERROR ── */
  function renderError(msg) {
    body.innerHTML =
      '<div class="ms-error-block">' +
        '<div class="ms-error-label">오류</div>' +
        '<div class="ms-error-msg">' + msg + '</div>' +
        '<button class="ms-retry-btn" id="ms-retry">다시 시도</button>' +
      '</div>';
    var btn = document.getElementById('ms-retry');
    if (btn) btn.addEventListener('click', function() { fetchData(); });
  }

  /* ── EMPTY STATES ── */
  function renderNoTeam() {
    body.innerHTML =
      '<div class="ms-empty-block">' +
        '<div class="ms-empty-label">팀 정보 없음</div>' +
        '<div class="ms-empty-msg">팀에 배정되지 않았습니다. 운영진에게 문의하세요.</div>' +
      '</div>';
  }

  function renderNoLeader() {
    /* we still have team data — show own score, no leader section */
    buildScoreView(true);
  }

  /* ── MAIN SCORE VIEW ── */
  function buildScoreView(leaderAbsent) {
    var own     = state.ownScore;
    var leader  = leaderAbsent ? null : state.leaderScore;
    var total   = state.totalPoints;
    var name    = state.teamName || '우리 조';

    var gap     = leader !== null ? leader - own : null;
    var isTied  = gap === 0;
    var isLeader = gap !== null && gap < 0;

    /* ── OWN CARD ── */
    var ownCardClass = 'ms-card ms-card-own' + (isLeader ? ' ms-is-leader' : '');
    var leadingMark  = isLeader
      ? '<div class="ms-leading-mark">선두</div>'
      : '';
    var ownPct = total > 0 ? Math.round((own / total) * 100) : 0;

    var ownCard =
      '<div class="' + ownCardClass + '">' +
        '<div class="ms-card-label">우리 조</div>' +
        '<div class="ms-card-team-name">' + escHtml(name) + '</div>' +
        '<div class="ms-score-number" id="ms-own-num">0</div>' +
        '<div class="ms-score-unit">점 &nbsp;<span class="ms-score-fraction">/ ' + total + '</span></div>' +
        leadingMark +
        '<div class="ms-progress-wrap">' +
          '<div class="ms-progress-label">달성률</div>' +
          '<div class="ms-progress-track">' +
            '<div class="ms-progress-fill" id="ms-prog-fill"></div>' +
          '</div>' +
        '</div>' +
      '</div>';

    /* ── LEADER CARD ── */
    var leaderCard;
    if (leaderAbsent) {
      leaderCard =
        '<div class="ms-card ms-card-leader">' +
          '<div class="ms-card-label">선두 조</div>' +
          '<div class="ms-empty-msg" style="font-size:13px;color:var(--ink-light);margin-top:16px;">' +
            '아직 점수를 기록한 조가 없습니다.' +
          '</div>' +
        '</div>';
    } else if (isLeader) {
      leaderCard =
        '<div class="ms-card ms-card-leader">' +
          '<div class="ms-card-label">선두 조</div>' +
          '<div class="ms-card-team-name" style="font-style:italic;">익명</div>' +
          '<div class="ms-score-number" id="ms-lead-num" style="color:var(--ink-light);">0</div>' +
          '<div class="ms-score-unit" style="color:var(--ink-light);">점 &nbsp;<span class="ms-score-fraction">/ ' + total + '</span></div>' +
        '</div>';
    } else {
      leaderCard =
        '<div class="ms-card ms-card-leader">' +
          '<div class="ms-card-label">선두 조</div>' +
          '<div class="ms-card-team-name" style="font-style:italic;">익명</div>' +
          '<div class="ms-score-number" id="ms-lead-num" style="color:var(--ink-light);">0</div>' +
          '<div class="ms-score-unit" style="color:var(--ink-light);">점 &nbsp;<span class="ms-score-fraction">/ ' + total + '</span></div>' +
        '</div>';
    }

    /* ── DELTA ROW ── */
    var deltaHTML;
    if (leaderAbsent) {
      deltaHTML = '';
    } else if (isTied) {
      deltaHTML =
        '<div class="ms-delta-row">' +
          '<div class="ms-delta-label">선두까지</div>' +
          '<div class="ms-delta-value ms-delta-tied">동점</div>' +
          '<div class="ms-delta-sub">선두 조와 같은 점수입니다.</div>' +
        '</div>';
    } else if (isLeader) {
      var aheadBy = Math.abs(gap);
      deltaHTML =
        '<div class="ms-delta-row">' +
          '<div class="ms-delta-label">격차</div>' +
          '<div class="ms-delta-value ms-delta-ahead" id="ms-delta-num">+0점 앞서고 있음</div>' +
          '<div class="ms-delta-sub">현재 선두입니다.<br>계속 유지하세요.</div>' +
        '</div>';
    } else {
      deltaHTML =
        '<div class="ms-delta-row">' +
          '<div class="ms-delta-label">선두까지</div>' +
          '<div class="ms-delta-value ms-delta-behind" id="ms-delta-num">-0점</div>' +
          '<div class="ms-delta-sub">선두 조까지의 점수 차이입니다.<br>최대 ' + total + '점 도전 중.</div>' +
        '</div>';
    }

    body.innerHTML =
      '<div class="ms-cards-row">' +
        ownCard + leaderCard +
      '</div>' +
      deltaHTML;

    /* ── ANIMATE NUMBERS ── */
    var ownEl = document.getElementById('ms-own-num');
    if (ownEl) animateCount(ownEl, 0, own, 600);

    var leadEl = document.getElementById('ms-lead-num');
    if (leadEl && leader !== null) {
      animateCount(leadEl, 0, leader, 600);
    }

    /* ── PROGRESS BAR ── */
    var fill = document.getElementById('ms-prog-fill');
    if (fill) {
      setTimeout(function() {
        fill.style.width = ownPct + '%';
      }, 60);
    }

    /* ── DELTA TEXT ── */
    if (!leaderAbsent && !isTied) {
      var deltaEl = document.getElementById('ms-delta-num');
      if (deltaEl) {
        if (isLeader) {
          var aheadBy = Math.abs(gap);
          var start2 = 0;
          var t0 = null;
          (function anim(ts) {
            if (!t0) t0 = ts;
            var p = Math.min((ts - t0) / 600, 1);
            var e = 1 - Math.pow(1 - p, 3);
            var val = Math.round(start2 + aheadBy * e);
            deltaEl.textContent = '+' + val + '점 앞서고 있음';
            if (p < 1) requestAnimationFrame(anim);
            else deltaEl.textContent = '+' + aheadBy + '점 앞서고 있음';
          })(performance.now());
        } else {
          var behindBy = gap;
          var t0b = null;
          (function animB(ts) {
            if (!t0b) t0b = ts;
            var p = Math.min((ts - t0b) / 600, 1);
            var e = 1 - Math.pow(1 - p, 3);
            var val = Math.round(behindBy * e);
            deltaEl.textContent = '-' + val + '점';
            if (p < 1) requestAnimationFrame(animB);
            else deltaEl.textContent = '-' + behindBy + '점';
          })(performance.now());
        }
      }
    }
  }

  /* ── SOFT REFRESH (numbers only, no full re-render) ── */
  function softUpdate(own, leader, total) {
    var ownEl = document.getElementById('ms-own-num');
    if (ownEl) {
      var prev = parseInt(ownEl.textContent) || 0;
      if (prev !== own) animateCount(ownEl, prev, own, 600);
    }

    var leadEl = document.getElementById('ms-lead-num');
    if (leadEl && leader !== null) {
      var prevL = parseInt(leadEl.textContent) || 0;
      if (prevL !== leader) animateCount(leadEl, prevL, leader, 600);
    }

    var fill = document.getElementById('ms-prog-fill');
    if (fill && total > 0) {
      fill.style.width = Math.round((own / total) * 100) + '%';
    }

    var gap = leader !== null ? leader - own : null;
    if (gap !== null) {
      var deltaEl = document.getElementById('ms-delta-num');
      if (deltaEl) {
        var isLeader = gap < 0;
        var isTied   = gap === 0;
        if (!isTied) {
          var target = Math.abs(gap);
          var prev2 = parseInt(deltaEl.textContent.replace(/[^0-9]/g, '')) || 0;
          var t0c = null;
          (function animC(ts) {
            if (!t0c) t0c = ts;
            var p = Math.min((ts - t0c) / 600, 1);
            var e = 1 - Math.pow(1 - p, 3);
            var val = Math.round(prev2 + (target - prev2) * e);
            if (isLeader) deltaEl.textContent = '+' + val + '점 앞서고 있음';
            else          deltaEl.textContent = '-' + val + '점';
            if (p < 1) requestAnimationFrame(animC);
            else {
              if (isLeader) deltaEl.textContent = '+' + target + '점 앞서고 있음';
              else          deltaEl.textContent = '-' + target + '점';
            }
          })(performance.now());
        }
      }
    }
  }

  var firstRender = true;

  /* ── FETCH ── */
  function fetchData() {
    if (firstRender) renderSkeleton();

    fetch(API_URL, { credentials: 'same-origin' })
      .then(function(res) {
        if (res.status === 401) throw { type: 'auth' };
        if (!res.ok) throw { type: 'http', status: res.status };
        return res.json();
      })
      .then(function(json) {
        var d = json.data;

        /* update frozen state */
        var frozen = d.frozen || false;
        state.frozen = frozen;
        frozenBadge.innerHTML = frozen
          ? '<span class="ms-frozen-tag">현황 동결</span>'
          : '';
        if (frozen) {
          statusEl.textContent = 'FROZEN';
          statusEl.className = 'ms-meta-val ms-status-frozen';
        } else {
          statusEl.textContent = 'LIVE';
          statusEl.className = 'ms-meta-val ms-status-live';
        }

        lastUpdEl.textContent = nowHMS();

        /* no team */
        if (!d.team) {
          firstRender = true;
          renderNoTeam();
          scheduleNext();
          return;
        }

        var ownScore    = d.team.score;
        var teamName    = d.team.name;
        var leaderScore = d.leader ? d.leader.score : null;
        var total       = d.total_points || 100;

        if (firstRender ||
            state.teamName !== teamName ||
            state.totalPoints !== total ||
            leaderScore === null !== (state.leaderScore === null)) {
          /* structural change — full re-render */
          state.ownScore    = ownScore;
          state.leaderScore = leaderScore;
          state.totalPoints = total;
          state.teamName    = teamName;
          if (!d.leader) {
            renderNoLeader();
          } else {
            buildScoreView(false);
          }
          firstRender = false;
        } else {
          /* soft update — just animate numbers */
          state.ownScore    = ownScore;
          state.leaderScore = leaderScore;
          state.totalPoints = total;
          softUpdate(ownScore, leaderScore, total);
        }

        scheduleNext();
      })
      .catch(function(err) {
        lastUpdEl.textContent = 'ERROR';
        if (err && err.type === 'auth') {
          renderError('로그인이 필요합니다. 페이지를 새로고침하여 다시 로그인하세요.');
        } else {
          renderError('데이터를 불러오지 못했습니다. 네트워크 상태를 확인하세요.');
        }
        firstRender = true;
        scheduleNext();
      });
  }

  function scheduleNext() {
    clearTimeout(refreshTimer);
    if (!isHidden) {
      refreshTimer = setTimeout(function() {
        fetchData();
      }, REFRESH_MS);
    }
  }

  function escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/'/g, '&#39;');
  }

  /* ── BOOT ── */
  fetchData();
})();
</script>
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
        # Score visibility is admin-only to prevent the "we're 4th of 4"
        # demoralization. Mentees see only their own + (anonymized) leader's
        # score via /my-score, served by the econ_judge plugin endpoint that
        # bypasses this visibility lock.
        set_config("score_visibility", "admins")
        # Account visibility also admin-only — hides /users entirely so the
        # roster of other teams isn't a "who else is here" social distraction.
        # Cascades to /api/v1/users, /users/{id}, and the navbar Users link.
        set_config("account_visibility", "admins")
        set_config("freeze", FREEZE_AT)
        set_config("challenge_ratings", "disabled")
        # `Configs.social_shares` in templates is a @property that calls
        # `get_config("social_shares", default=True)`. A NULL/missing row makes
        # `_get_config` return the KeyError sentinel, which then falls through
        # to the hardcoded `default=True` — so storing None here does NOT
        # disable the share button. Storing the literal string "false" works:
        # `_get_config` lowercases it and returns Python False, which Jinja
        # then treats as falsy. (Storing False directly would persist as "0"
        # — non-empty, hence truthy in Jinja.)
        set_config("social_shares", "false")
        set_config("verify_emails", None)
        set_config("team_size", None)
        set_config("theme_header", THEME_HEADER_CSS)
        # Theme settings drive the per-category and per-challenge sort on the
        # /challenges page. CTFd 3.8.5 reads `themeSettings.challenge_category_order`
        # and `themeSettings.challenge_order` from this config; each value is a JS
        # comparator source string that gets eval'd via `new Function`. We pin
        # the category order to P1 → P2 → 연습 → 미션 (camp's intended flow)
        # and sort challenges within each category by id ASC, which matches the
        # authoring order — easier sub-circuits first, composition challenges
        # (Full Wiring) at the bottom.
        set_config(
            "theme_settings",
            json.dumps(
                {
                    "challenge_category_order": (
                        "(a, b) => { const o = {"
                        "'연습': 0, '미션': 1, "
                        "'Project 1': 2, 'Project 2': 3"
                        "}; return (o[a] ?? 99) - (o[b] ?? 99); }"
                    ),
                    "challenge_order": "(a, b) => a.id - b.id",
                },
                ensure_ascii=False,
            ),
        )
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

        # /my-score page — auth-required, mentee-only personal progress view.
        ms_page = Pages.query.filter_by(route="my-score").first()
        if ms_page is None:
            db.session.add(Pages(
                title="내 점수",
                route="my-score",
                content=MY_SCORE_CONTENT,
                draft=False,
                hidden=True,  # hidden from the public Pages menu — accessed via /my-score
                auth_required=True,
                format="html",
            ))
            db.session.commit()
            print("[bootstrap] /my-score page created")
        else:
            ms_page.title = "내 점수"
            ms_page.content = MY_SCORE_CONTENT
            ms_page.format = "html"
            ms_page.auth_required = True
            ms_page.hidden = True
            db.session.commit()
            print("[bootstrap] /my-score page synced")

        # Challenges are upserted on every boot — source of truth is
        # tests/register_challenges.py. The HA/FA category move (P1 → 연습)
        # and description annotations need to apply to existing rows from
        # previous deploys, not just newly-created ones.
        existing_chals = {c.id: c for c in Challenges.query.all()}
        created = 0
        updated = 0
        for cid, name, category, value, description, _rows in CHALLENGES:
            chal = existing_chals.get(cid)
            if chal is None:
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
                continue
            dirty = False
            if chal.name != name:
                chal.name = name; dirty = True
            if chal.category != category:
                chal.category = category; dirty = True
            if chal.value != value:
                chal.value = value; dirty = True
            if chal.description != description:
                chal.description = description; dirty = True
            if chal.state != "visible":
                chal.state = "visible"; dirty = True
            if dirty:
                updated += 1
        if created or updated:
            db.session.commit()
            print(f"[bootstrap] Challenges: {created} created, {updated} updated")
        else:
            print(f"[bootstrap] All {len(CHALLENGES)} challenges already in sync")

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
