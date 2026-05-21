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
                        "'Project 1': 0, 'Project 2': 1, "
                        "'연습': 2, '미션': 3"
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
