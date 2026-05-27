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
/* E-CON 논설 — Direction D editorial-minimal theme (see
   docs/theme-drop-in.css for the source of truth + audit notes;
   the comment is kept short here so its literal text does NOT
   include any HTML tag sequences that the browser's HTML parser
   would interpret as terminating this style block. */

/* ── Font imports ────────────────────────────────────────────────── */

@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable.min.css');
@import url('https://fonts.googleapis.com/css2?family=Inter+Tight:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

/* ── Tokens ──────────────────────────────────────────────────────── */

:root {
  /* Brand — SENS, mandated */
  --d-brand:       #f5a83d;
  --d-brand-dark:  #d69336;
  --d-brand-ink:   #7a5a1f;
  --d-brand-soft:  #fff4e0;
  --d-brand-line:  rgba(245,168,61,0.32);

  /* Paper (warm whites) */
  --d-paper:       #fbfaf6;
  --d-paper-soft:  #f5f1e6;
  --d-paper-sunk:  #efe9d7;

  /* Ink (warm blacks) */
  --d-ink:         #15110a;
  --d-ink-mid:     #4a3f2a;
  --d-ink-light:   #8c8270;
  --d-ink-soft:    #b8ad95;

  /* Lines */
  --d-hair:        #e7dfcd;
  --d-hair-strong: #c8b48a;

  /* Status — warmed greens / rusts / amber */
  --d-pass:        #2e7d52;
  --d-pass-soft:   #e6f1e7;
  --d-pass-line:   #b9d6bf;
  --d-warn:        #a8761d;
  --d-warn-soft:   #fff4dd;
  --d-warn-line:   #e9c98c;
  --d-fail:        #b04a3a;
  --d-fail-soft:   #fbece3;
  --d-fail-line:   #ecbeb0;

  /* Type families (3 only) */
  --d-f-sans: 'Inter Tight', 'Pretendard Variable', system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
  --d-f-ko:   'Pretendard Variable', 'Pretendard', system-ui, sans-serif;
  --d-f-mono: 'IBM Plex Mono', ui-monospace, 'SF Mono', 'JetBrains Mono', Consolas, monospace;

  /* Spacing */
  --d-s-1:  4px;  --d-s-2:  8px;  --d-s-3: 12px;
  --d-s-4: 16px;  --d-s-5: 20px;  --d-s-6: 24px;
  --d-s-7: 32px;  --d-s-8: 48px;  --d-s-9: 64px;

  /* Radius (used sparingly) */
  --d-r-sm:   4px;
  --d-r-md:   8px;
  --d-r-pill: 999px;

  /* CTFd legacy aliases — templates that read --theme-color, --sens-* keep working */
  --theme-color:    var(--d-brand);
  --sens-brand:     var(--d-brand);
  --sens-brand-dark:var(--d-brand-dark);
  --sens-brand-ink: var(--d-brand-ink);
  --sens-brand-soft:var(--d-brand-soft);
}

/* ── Base ────────────────────────────────────────────────────────── */

body {
  background: var(--d-paper);
  color: var(--d-ink);
  font-family: var(--d-f-ko);
  font-feature-settings: 'tnum' on;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

a { color: var(--d-ink); text-decoration: none; }
a:hover { color: var(--d-brand-dark); text-decoration: none; }

/* ── CTFd navbar / jumbotron / buttons overrides ─────────────────── */

.navbar,
.navbar.navbar-dark,
.navbar.bg-dark {
  background-color: var(--d-paper) !important;
  background-image: none !important;
  border-bottom: 1px solid var(--d-hair-strong);
  box-shadow: 0 1px 0 var(--d-paper-sunk);
}
/* CTFd's stock navbar carries Bootstrap classes `navbar-dark bg-dark`,
   which paint .navbar-brand + .nav-link white on the assumption of a
   dark background. We flip the bg to warm paper above, so the text
   needs explicit overrides — otherwise the brand wordmark and link text
   render in white and become invisible. */
.navbar .navbar-brand,
.navbar.navbar-dark .navbar-brand {
  color: var(--d-ink) !important;
  font-family: var(--d-f-sans);
  font-weight: 700;
  letter-spacing: -0.015em;
  font-size: 16px;
}
.navbar .navbar-brand:hover,
.navbar.navbar-dark .navbar-brand:hover {
  color: var(--d-brand-dark) !important;
}
.navbar .nav-link,
.navbar.navbar-dark .nav-link {
  color: var(--d-ink-mid) !important;
  font-family: var(--d-f-ko);
  font-weight: 500;
  font-size: 14px;
  letter-spacing: -0.005em;
  padding: 8px 12px !important;
  border-bottom: 2px solid transparent !important;
  border-radius: 6px;
}
.navbar .nav-link:hover,
.navbar.navbar-dark .nav-link:hover {
  color: var(--d-ink) !important;
  background: var(--d-paper-soft);
}
.navbar .nav-link.active,
.navbar.navbar-dark .nav-link.active,
.navbar .nav-item.active > .nav-link {
  color: var(--d-ink) !important;
  border-bottom: 2px solid var(--d-brand-dark) !important;
  border-radius: 0;
  background: transparent !important;
}
.navbar .navbar-toggler,
.navbar.navbar-dark .navbar-toggler {
  border-color: var(--d-hair-strong) !important;
  color: var(--d-ink) !important;
}
.navbar.navbar-dark .navbar-toggler-icon {
  /* Bootstrap renders the hamburger as a white SVG background-image;
     invert it so it shows on warm paper. */
  filter: invert(1) brightness(0.4);
}

.jumbotron {
  background-color: var(--d-paper) !important;
  border-bottom: 1px solid var(--d-hair);
}

.btn,
.btn-primary,
button[type="submit"] {
  font-family: var(--d-f-sans);
  font-weight: 600;
  letter-spacing: -0.005em;
  border-radius: var(--d-r-pill) !important;
  transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}
.btn-primary,
button[type="submit"] {
  background-color: var(--d-ink) !important;
  border-color: var(--d-ink) !important;
  color: var(--d-paper) !important;
}
.btn-primary:hover,
.btn-primary:focus,
.btn-primary:active,
button[type="submit"]:hover {
  background-color: var(--d-brand-dark) !important;
  border-color: var(--d-brand-dark) !important;
  color: var(--d-paper) !important;
}

.btn-outline-secondary {
  background: transparent !important;
  color: var(--d-ink) !important;
  border-color: var(--d-hair-strong) !important;
}
.btn-outline-secondary:hover {
  background: var(--d-paper-soft) !important;
  border-color: var(--d-ink) !important;
  color: var(--d-ink) !important;
}

/* Korean-only camp — hide CTFd language switcher (Chrome 105+, Safari 15.4+, FF 121+) */
.navbar li.nav-item:has(form[x-data="LanguageForm"]) {
  display: none !important;
}

/* Hide CTFd's built-in light/dark theme toggle. Direction D is light-only;
   the toggle flips a CTFd theme variable that conflicts with our overrides
   and leaves the page in a half-styled state. The CD v2 dark-mode tokens
   in step2/system-d.css were intentionally not integrated for the camp. */
.navbar li.nav-item:has(button.theme-switch) {
  display: none !important;
}

/* Stacked submit-row (kept from original — required by drag-drop dropzone in view.html) */
.submit-row > .col-sm-8,
.submit-row > .col-sm-4 {
  flex: 0 0 100% !important;
  max-width: 100% !important;
}
.submit-row > .key-submit {
  margin-top: var(--d-s-3) !important;
}
.submit-row > .key-submit .challenge-submit {
  height: auto !important;
  padding: 0.65rem 1rem !important;
  background: var(--d-ink) !important;
  border-color: var(--d-ink) !important;
  color: var(--d-paper) !important;
  font-weight: 600 !important;
  border-radius: var(--d-r-pill) !important;
}
.submit-row > .key-submit .challenge-submit:hover {
  background: var(--d-brand-dark) !important;
  border-color: var(--d-brand-dark) !important;
}

/* ── Shared component classes (used by Pages markup + plugin assets) ── */

.d-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-family: var(--d-f-sans);
  font-weight: 600;
  font-size: 14.5px;
  letter-spacing: -0.005em;
  text-decoration: none;
  transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
  cursor: pointer;
  border: 1px solid transparent;
  white-space: nowrap;
}
.d-btn-primary {
  background: var(--d-ink);
  color: var(--d-paper);
  padding: 13px 22px;
  border-radius: var(--d-r-pill);
  border-color: var(--d-ink);
}
.d-btn-primary:hover { background: var(--d-brand-dark); border-color: var(--d-brand-dark); color: var(--d-paper); text-decoration: none; }
.d-btn-ghost {
  background: transparent;
  color: var(--d-ink);
  padding: 13px 22px;
  border-radius: var(--d-r-pill);
  border-color: var(--d-hair-strong);
}
.d-btn-ghost:hover { border-color: var(--d-ink); background: var(--d-paper-soft); text-decoration: none; }
.d-btn-text {
  background: transparent;
  color: var(--d-ink-mid);
  padding: 13px 6px;
  border: none;
  border-bottom: 1px solid transparent;
  border-radius: 0;
}
.d-btn-text:hover { color: var(--d-ink); border-bottom-color: var(--d-ink); text-decoration: none; }

.d-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: var(--d-f-mono);
  font-size: 10.5px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  font-weight: 600;
  padding: 4px 10px 4px 8px;
  border-radius: var(--d-r-pill);
  white-space: nowrap;
  border: 1px solid transparent;
}
.d-pill-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  flex-shrink: 0;
}
.d-pill-pass   { background: var(--d-pass-soft); color: var(--d-pass);   border-color: var(--d-pass-line); }
.d-pill-warn   { background: var(--d-warn-soft); color: var(--d-warn);   border-color: var(--d-warn-line); }
.d-pill-fail   { background: var(--d-fail-soft); color: var(--d-fail);   border-color: var(--d-fail-line); }
.d-pill-locked { background: var(--d-paper-sunk); color: var(--d-ink-light); border-color: var(--d-hair); }
.d-pill-brand  { background: var(--d-brand-soft); color: var(--d-brand-ink); border-color: var(--d-brand-line); }

.d-tag {
  display: inline-flex;
  align-items: center;
  font-family: var(--d-f-mono);
  font-size: 10.5px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  font-weight: 600;
  padding: 3px 8px;
  border-radius: var(--d-r-sm);
  background: var(--d-paper-sunk);
  color: var(--d-ink-mid);
  white-space: nowrap;
}
.d-tag-mission { background: rgba(245,168,61,0.16); color: var(--d-brand-ink); }
.d-tag-p1      { background: rgba(46,125,82,0.13);  color: var(--d-pass); }
.d-tag-p2      { background: rgba(176,74,58,0.13);  color: var(--d-fail); }

.d-livedot {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-family: var(--d-f-mono);
  font-size: 11px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--d-brand-dark);
  font-weight: 600;
}
.d-livedot::before {
  content: '';
  width: 7px; height: 7px;
  background: var(--d-brand-dark);
  border-radius: 50%;
  box-shadow: 0 0 0 4px rgba(214,147,54,0.18);
  animation: d-pulse 2s ease infinite;
}
@keyframes d-pulse {
  0%, 100% { box-shadow: 0 0 0 4px rgba(214,147,54,0.18); }
  50%      { box-shadow: 0 0 0 7px rgba(214,147,54,0.04); }
}

.d-meta {
  font-family: var(--d-f-mono);
  font-size: 11px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--d-ink-light);
}
.d-tiny {
  font-family: var(--d-f-mono);
  font-size: 10px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--d-ink-light);
}
.d-code {
  font-family: var(--d-f-mono);
  font-size: 0.92em;
  background: var(--d-brand-soft);
  padding: 2px 6px;
  border-radius: var(--d-r-sm);
  color: var(--d-brand-ink);
}
.d-rule { height: 1px; background: var(--d-hair); border: none; margin: var(--d-s-7) 0; }
</style>
<script defer src="/plugins/econ_judge/assets/scoreboard.js"></script>
<script defer src="/plugins/econ_judge/assets/challenges.js"></script>
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
.s1-root {
  width: 100%;
  min-height: 100%;
  background: var(--d-paper);
  font-family: var(--d-f-sans);
}
.s1-wrap {
  padding: 36px 64px 36px;
  display: flex;
  flex-direction: column;
  gap: 0;
  min-height: 100%;
}
.s1-root hr { margin: 28px 0; }

.s1-topmeta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-family: var(--d-f-mono);
  font-size: 11px;
  letter-spacing: 0.12em;
  color: var(--d-ink-light);
  text-transform: uppercase;
}
.s1-tm-mid {
  font-family: var(--d-f-ko);
  text-transform: none;
  letter-spacing: 0.02em;
  color: var(--d-ink-mid);
  font-weight: 500;
}

.s1-hero {
  display: grid;
  grid-template-columns: 1.4fr 1fr;
  gap: 56px;
  align-items: end;
}
.s1-hero-text { display: flex; flex-direction: column; }

.s1-h1 {
  font-family: var(--d-f-sans);
  font-weight: 700;
  font-size: 96px;
  line-height: 0.94;
  letter-spacing: -0.045em;
  color: var(--d-ink);
  margin: 0;
}
.s1-h1-dot { color: var(--d-brand-dark); margin-left: 2px; }

.s1-lede {
  font-family: var(--d-f-ko);
  font-size: 18px;
  line-height: 1.55;
  color: var(--d-ink);
  margin: 24px 0 0;
  font-weight: 500;
  max-width: 540px;
  letter-spacing: -0.005em;
}
.s1-lede-mut {
  color: var(--d-ink-light);
  font-size: 15px;
  font-weight: 400;
}

.s1-cta-row { display: flex; align-items: center; gap: 14px; margin-top: 32px; }

.s1-hero-fig {
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-self: end;
  padding-bottom: 6px;
}
.s1-fig-svg {
  width: 100%;
  max-width: 320px;
  height: auto;
  opacity: 0.95;
}

.s1-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 32px;
}
.s1-stat { display: flex; flex-direction: column; gap: 8px; }
.s1-stat-num {
  font-family: var(--d-f-sans);
  font-weight: 600;
  font-size: 64px;
  line-height: 1;
  letter-spacing: -0.04em;
  color: var(--d-ink);
  font-feature-settings: 'tnum';
  display: flex;
  align-items: baseline;
}
.s1-stat-unit {
  font-family: var(--d-f-mono);
  font-size: 16px;
  font-weight: 500;
  color: var(--d-ink-light);
  margin-left: 5px;
  letter-spacing: 0;
}
.s1-stat-en {
  font-family: var(--d-f-ko);
  font-size: 13px;
  color: var(--d-ink-light);
  line-height: 1.45;
}

.s1-proc-section { display: flex; flex-direction: column; gap: 18px; }
.s1-proc-head { display: flex; align-items: baseline; gap: 16px; }
.s1-h2 {
  font-family: var(--d-f-sans);
  font-weight: 600;
  font-size: 28px;
  letter-spacing: -0.025em;
  color: var(--d-ink);
  margin: 0;
}

.s1-proc {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}
.s1-step {
  display: grid;
  grid-template-columns: 64px 1fr;
  padding: 18px 0;
  border-top: 1px solid var(--d-hair);
  align-items: baseline;
}
.s1-step:last-child { border-bottom: 1px solid var(--d-hair); }
.s1-step-n {
  font-family: var(--d-f-mono);
  font-size: 13px;
  font-weight: 500;
  color: var(--d-brand-dark);
  letter-spacing: 0.04em;
}
.s1-step-body { display: flex; flex-direction: column; gap: 6px; }
.s1-step-h {
  font-family: var(--d-f-ko);
  font-size: 17px;
  font-weight: 500;
  color: var(--d-ink);
  letter-spacing: -0.005em;
  line-height: 1.4;
}
.s1-step-sub {
  font-family: var(--d-f-ko);
  font-size: 14px;
  color: var(--d-ink-light);
  line-height: 1.55;
}

.s1-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: auto;
  font-family: var(--d-f-mono);
  font-size: 11px;
  color: var(--d-ink-light);
}
.s1-foot-l, .s1-foot-r { display: inline-flex; align-items: baseline; gap: 8px; }
.s1-foot-v { color: var(--d-ink-mid); }
.s1-foot-sep { color: var(--d-hair); margin: 0 8px; }
</style>

<div class="d s1-root">
  <div class="s1-wrap">

    <div class="s1-topmeta">
      <span class="d-livedot">LIVE</span>
      <span class="s1-tm-mid">SNU SENS &middot; 2026 공헌 공드림 캠프</span>
      <span class="s1-tm-r">SENS&#8209;2026&#8209;001 / v1.0</span>
    </div>

    <hr class="d-rule" />

    <section class="s1-hero">
      <div class="s1-hero-text">
        <h1 class="s1-h1">
          E&#8209;CON 논설<span class="s1-h1-dot">.</span>
        </h1>
        <p class="s1-lede">
          디지털 논리회로 설계 자동채점 시스템.
          <span class="s1-lede-mut">
            Digital 시뮬레이터로 설계한 조합논리 회로를 업로드하면,
            채점 엔진이 비밀 테스트 케이스에 대해 즉시 검증합니다.
          </span>
        </p>

        <div class="s1-cta-row">
          <a class="d-btn d-btn-primary" href="/challenges">
            도전 시작
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
              <path d="M3 8h10M9 4l4 4-4 4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </a>
          <a class="d-btn d-btn-text" href="/my-score">
            내 점수 보기
          </a>
        </div>
      </div>

      <div class="s1-hero-fig">
        <div class="d-tiny">FIG. 1 — HALF-ADDER</div>
        <svg class="s1-fig-svg" viewBox="0 0 280 200" xmlns="http://www.w3.org/2000/svg"
             fill="none" stroke="var(--d-ink)" stroke-width="1" stroke-linecap="round" stroke-linejoin="round">
          <line x1="20" y1="62" x2="110" y2="62"/>
          <line x1="20" y1="138" x2="110" y2="138"/>
          <circle cx="92" cy="62" r="2" fill="var(--d-ink)" stroke="none"/>
          <line x1="92" y1="62" x2="92" y2="162"/>
          <circle cx="92" cy="138" r="2" fill="var(--d-ink)" stroke="none"/>
          <line x1="92" y1="138" x2="92" y2="162"/>
          <text x="6" y="66" font-family="var(--d-f-mono)" font-size="11" fill="var(--d-ink)">A</text>
          <text x="6" y="142" font-family="var(--d-f-mono)" font-size="11" fill="var(--d-ink)">B</text>

          <path d="M110 42 Q132 62 110 82"/>
          <path d="M116 42 Q138 62 116 82 L142 82 Q180 62 142 42 Z"/>
          <line x1="180" y1="62" x2="252" y2="62"/>
          <text x="258" y="66" font-family="var(--d-f-mono)" font-size="11" fill="var(--d-ink)">S</text>

          <path d="M110 152 L110 184 L140 184 Q172 184 172 168 Q172 152 140 152 Z"/>
          <line x1="92" y1="162" x2="110" y2="162"/>
          <line x1="92" y1="176" x2="110" y2="176"/>
          <line x1="172" y1="168" x2="252" y2="168"/>
          <text x="258" y="166" font-family="var(--d-f-mono)" font-size="11" fill="var(--d-ink)">C</text>
          <text x="266" y="172" font-family="var(--d-f-mono)" font-size="8" fill="var(--d-brand-ink)">out</text>
        </svg>
      </div>
    </section>

    <hr class="d-rule" />

    <section class="s1-stats">
      <div class="s1-stat">
        <div class="d-meta">도전 과제</div>
        <div class="s1-stat-num">18</div>
        <div class="s1-stat-en">across 4 categories</div>
      </div>
      <div class="s1-stat">
        <div class="d-meta">총점</div>
        <div class="s1-stat-num">100<span class="s1-stat-unit">pt</span></div>
        <div class="s1-stat-en">sum of all challenge values</div>
      </div>
      <div class="s1-stat">
        <div class="d-meta">참가 팀</div>
        <div class="s1-stat-num">4</div>
        <div class="s1-stat-en">조별 약 8명 / 총 32명</div>
      </div>
      <div class="s1-stat">
        <div class="d-meta">파일 한도</div>
        <div class="s1-stat-num">256<span class="s1-stat-unit">kb</span></div>
        <div class="s1-stat-en">.dig — combinational only</div>
      </div>
    </section>

    <hr class="d-rule" />

    <section class="s1-proc-section">
      <div class="s1-proc-head">
        <h2 class="s1-h2">시작하는 법</h2>
        <span class="d-meta">— GETTING STARTED · 4 STEPS</span>
      </div>

      <ol class="s1-proc">
        <li class="s1-step">
          <span class="s1-step-n">01</span>
          <div class="s1-step-body">
            <div class="s1-step-h">도전 과제 목록에서 문제를 선택합니다</div>
            <div class="s1-step-sub">연습 → 미션 → 프로젝트 순서로 진행. 어느 카테고리부터 시작해도 좋습니다.</div>
          </div>
        </li>
        <li class="s1-step">
          <span class="s1-step-n">02</span>
          <div class="s1-step-body">
            <div class="s1-step-h">Digital에서 회로를 설계하고 <code class="d-code">.dig</code> 파일로 저장합니다</div>
            <div class="s1-step-sub">조합논리 (combinational) — 클럭/플립플롭은 사용할 수 없습니다.</div>
          </div>
        </li>
        <li class="s1-step">
          <span class="s1-step-n">03</span>
          <div class="s1-step-body">
            <div class="s1-step-h">파일을 업로드하면 자동 채점이 즉시 실행됩니다</div>
            <div class="s1-step-sub">통상 2–5초 이내 결과 반환. 비밀 테스트 케이스로 검증합니다.</div>
          </div>
        </li>
        <li class="s1-step">
          <span class="s1-step-n">04</span>
          <div class="s1-step-body">
            <div class="s1-step-h">결과를 확인하고 필요하면 재제출하세요</div>
            <div class="s1-step-sub">횟수 제한 없음 — 가장 높은 점수만 최종 점수에 반영됩니다.</div>
          </div>
        </li>
      </ol>
    </section>

    <hr class="d-rule" />

    <footer class="s1-footer">
      <div class="s1-foot-l">
        <span class="d-tiny">DRAWN BY</span>
        <span class="s1-foot-v">SENS Engineering</span>
        <span class="s1-foot-sep">/</span>
        <span class="d-tiny">ENGINE</span>
        <span class="s1-foot-v">CTFd 3.8.5 &middot; Digital.jar v0.31</span>
      </div>
      <div class="s1-foot-r">
        <span class="d-tiny">UPDATED</span>
        <span class="s1-foot-v">2026-05-27</span>
      </div>
    </footer>

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
.s4-root {
  width: 100%;
  min-height: 100%;
  background: var(--d-paper);
  font-family: var(--d-f-sans);
  padding: 32px 56px 28px;
  display: flex;
  flex-direction: column;
  gap: 20px;
  box-sizing: border-box;
}
.s4-root *, .s4-root *::before, .s4-root *::after { box-sizing: inherit; }

.s4-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 32px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--d-hair);
}
.s4-head-l { display: flex; flex-direction: column; gap: 4px; }
.s4-head-meta {
  display: inline-flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}
.s4-head-doc {
  font-family: var(--d-f-ko);
  font-size: 13px;
  color: var(--d-ink-light);
  letter-spacing: -0.005em;
}
.s4-h1 {
  font-family: var(--d-f-sans);
  font-weight: 600;
  font-size: 48px;
  letter-spacing: -0.03em;
  margin: 8px 0 0;
  color: var(--d-ink);
}
.s4-head-sub {
  font-family: var(--d-f-ko);
  font-size: 14.5px;
  color: var(--d-ink-light);
  line-height: 1.55;
  margin: 8px 0 0;
  max-width: 520px;
}
.s4-head-r { display: flex; gap: 24px; padding-top: 4px; }
.s4-meta-cell { display: flex; flex-direction: column; gap: 4px; align-items: flex-end; }
.s4-meta-cell-v {
  font-family: var(--d-f-mono);
  font-size: 13px;
  color: var(--d-ink);
  font-feature-settings: 'tnum';
  letter-spacing: 0.04em;
}
.s4-frozen-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: var(--d-f-mono);
  font-size: 10.5px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  font-weight: 600;
  color: var(--d-ink-mid);
  background: var(--d-paper-sunk);
  border: 1px solid var(--d-hair-strong);
  padding: 4px 10px;
  border-radius: 999px;
}

.s4-cards {
  display: grid;
  grid-template-columns: 1.1fr 1fr;
  gap: 14px;
}
.s4-card {
  padding: 22px 26px;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  position: relative;
}
.s4-card-own {
  background: var(--d-brand-soft);
  border: 1px solid var(--d-brand-line);
}
.s4-card-leader {
  background: var(--d-paper-soft);
  border: 1px solid var(--d-hair-strong);
}
.s4-card-l { display: flex; flex-direction: column; gap: 4px; }
.s4-team {
  font-family: var(--d-f-sans);
  font-size: 22px;
  font-weight: 600;
  color: var(--d-ink);
  letter-spacing: -0.02em;
}
.s4-team-anon {
  color: var(--d-ink-light);
  font-style: italic;
  font-weight: 500;
}

.s4-score {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-feature-settings: 'tnum';
}
.s4-score-n {
  font-family: var(--d-f-sans);
  font-size: 80px;
  font-weight: 700;
  line-height: 0.95;
  letter-spacing: -0.045em;
  color: var(--d-ink);
}
.s4-score-leader .s4-score-n { color: var(--d-ink-mid); }
.s4-score-u {
  font-family: var(--d-f-mono);
  font-size: 18px;
  font-weight: 500;
  color: var(--d-ink-light);
  letter-spacing: 0.02em;
}
.s4-score-f {
  font-family: var(--d-f-mono);
  font-size: 14px;
  color: var(--d-ink-light);
  margin-left: 6px;
  letter-spacing: 0.02em;
}

.s4-progress { display: flex; flex-direction: column; gap: 8px; }
.s4-prog-track {
  height: 6px;
  background: rgba(245,168,61,0.18);
  border-radius: 3px;
  overflow: hidden;
}
.s4-prog-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--d-brand-dark), var(--d-brand));
  transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
}

.s4-meta-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding-top: 12px;
  border-top: 1px dashed var(--d-brand-line);
}
.s4-card-leader .s4-meta-row { border-top-color: var(--d-hair); }
.s4-meta-v {
  font-family: var(--d-f-mono);
  font-size: 14px;
  font-weight: 600;
  color: var(--d-ink);
  font-feature-settings: 'tnum';
}
.s4-meta-v-mut { color: var(--d-ink-light); }

.s4-leader-note {
  font-family: var(--d-f-ko);
  font-size: 13px;
  color: var(--d-ink-light);
  font-style: italic;
  margin-top: -4px;
}

.s4-frozen-placeholder {
  background: var(--d-paper-sunk);
  border: 1px dashed var(--d-hair-strong);
  border-radius: 6px;
  padding: 22px 16px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  color: var(--d-ink-light);
  margin-top: 4px;
}
.s4-frozen-h {
  font-family: var(--d-f-ko);
  font-size: 15px;
  font-weight: 500;
  color: var(--d-ink-mid);
}
.s4-frozen-sub {
  font-family: var(--d-f-ko);
  font-size: 12.5px;
  color: var(--d-ink-light);
}

.s4-gap {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 26px;
  border: 1px solid var(--d-hair);
  background: var(--d-paper);
}
.s4-gap-l { display: flex; flex-direction: column; gap: 4px; }
.s4-gap-msg {
  font-family: var(--d-f-ko);
  font-size: 16px;
  font-weight: 500;
  color: var(--d-ink);
  line-height: 1.55;
  margin-top: 4px;
}
.s4-gap-r { display: flex; align-items: baseline; gap: 4px; font-feature-settings: 'tnum'; }
.s4-gap-sign {
  font-family: var(--d-f-sans);
  font-size: 24px;
  color: var(--d-ink-mid);
  font-weight: 500;
}
.s4-gap-n {
  font-family: var(--d-f-sans);
  font-size: 38px;
  font-weight: 700;
  color: var(--d-ink);
  letter-spacing: -0.04em;
  line-height: 0.9;
}
.s4-gap-u {
  font-family: var(--d-f-mono);
  font-size: 16px;
  color: var(--d-ink-light);
  letter-spacing: 0.02em;
}
.s4-gap-frozen-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: var(--d-f-mono);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.14em;
  color: var(--d-ink-mid);
  background: var(--d-paper-sunk);
  border: 1px solid var(--d-hair-strong);
  padding: 7px 14px;
  border-radius: 999px;
}
.s4-root[data-frozen="true"] .s4-gap { background: var(--d-paper-soft); }
.s4-root[data-frozen="true"] .s4-gap-msg { color: var(--d-ink-light); }

.s4-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: auto;
  padding-top: 12px;
}
.s4-foot-l, .s4-foot-r { display: inline-flex; align-items: baseline; gap: 8px; }
.s4-foot-v {
  font-family: var(--d-f-mono);
  font-size: 11px;
  color: var(--d-ink-mid);
}

.s4-error {
  padding: 14px 18px;
  border: 1px solid var(--d-fail-line);
  background: var(--d-fail-soft);
  color: var(--d-fail);
  border-radius: 6px;
  font-family: var(--d-f-ko);
  font-size: 14px;
}

@media (max-width: 720px) {
  .s4-root { padding: 24px 18px 22px; gap: 16px; }
  .s4-head { flex-direction: column; gap: 12px; padding-bottom: 14px; }
  .s4-head-r { gap: 14px; align-self: flex-start; }
  .s4-h1 { font-size: 34px; }
  .s4-head-sub { font-size: 13.5px; }
  .s4-cards { grid-template-columns: 1fr; gap: 12px; }
  .s4-card { padding: 18px 20px; gap: 12px; }
  .s4-score-n { font-size: 60px; }
  .s4-gap { flex-direction: column; align-items: flex-start; gap: 10px; padding: 16px 18px; }
  .s4-gap-r { align-self: flex-end; }
  .s4-gap-n { font-size: 32px; }
}
</style>

<div class="d s4-root" id="ms-root" data-frozen="false">

  <header class="s4-head">
    <div class="s4-head-l">
      <div class="s4-head-meta">
        <span class="d-livedot" id="ms-live-pill">LIVE</span>
        <span class="s4-frozen-tag" id="ms-frozen-pill" style="display:none"><span class="d-pill-dot"></span>FROZEN · 동결</span>
        <span class="s4-head-doc">SNU SENS · 2026 공헌 공드림 캠프</span>
      </div>
      <h1 class="s4-h1">내 점수</h1>
      <p class="s4-head-sub">
        우리 조의 진행 상황과 선두 조와의 격차를 확인하세요. 다른 팀의 순위는 표시되지 않습니다.
      </p>
    </div>
    <div class="s4-head-r">
      <div class="s4-meta-cell">
        <span class="d-tiny">UPDATED</span>
        <span class="s4-meta-cell-v" id="ms-updated">— — : — — : — —</span>
      </div>
      <div class="s4-meta-cell">
        <span class="d-tiny">REFRESH</span>
        <span class="s4-meta-cell-v">20 s</span>
      </div>
    </div>
  </header>

  <div id="ms-error" class="s4-error" style="display:none"></div>

  <section class="s4-cards">

    <article class="s4-card s4-card-own">
      <div class="s4-card-l">
        <div class="d-meta">우리 조</div>
        <div class="s4-team" id="ms-team-name">—</div>
      </div>
      <div class="s4-score">
        <span class="s4-score-n" id="ms-score-own">—</span>
        <span class="s4-score-u">pt</span>
        <span class="s4-score-f">/ <span id="ms-total-own">100</span></span>
      </div>
      <div class="s4-progress">
        <div class="d-tiny" id="ms-progress-meta">달성률 · —%</div>
        <div class="s4-prog-track">
          <div class="s4-prog-fill" id="ms-prog-fill" style="width:0%"></div>
        </div>
      </div>
      <div class="s4-meta-row">
        <span class="d-tiny">해결 / 총 과제</span>
        <span class="s4-meta-v" id="ms-solved-own">— / —</span>
      </div>
    </article>

    <article class="s4-card s4-card-leader" id="ms-leader-live">
      <div class="s4-card-l">
        <div class="d-meta">선두 조</div>
        <div class="s4-team s4-team-anon">익명</div>
      </div>
      <div class="s4-score s4-score-leader">
        <span class="s4-score-n" id="ms-score-leader">—</span>
        <span class="s4-score-u">pt</span>
        <span class="s4-score-f">/ <span id="ms-total-leader">100</span></span>
      </div>
      <div class="s4-leader-note">
        팀명은 캠프 종료 후 공개됩니다.
      </div>
      <div class="s4-meta-row">
        <span class="d-tiny">해결 / 총 과제</span>
        <span class="s4-meta-v s4-meta-v-mut" id="ms-solved-leader">— / —</span>
      </div>
    </article>

    <article class="s4-card s4-card-leader" id="ms-leader-frozen" style="display:none">
      <div class="s4-card-l">
        <div class="d-meta">선두 조</div>
        <div class="s4-team s4-team-anon">— —</div>
      </div>
      <div class="s4-frozen-placeholder">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="11" width="18" height="11" rx="2"/>
          <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
        </svg>
        <div class="s4-frozen-h">프로젝트 단계 진행 중</div>
        <div class="s4-frozen-sub">캠프 종료 후 공개됩니다.</div>
      </div>
    </article>

  </section>

  <section class="s4-gap" id="ms-gap-live">
    <div class="s4-gap-l">
      <div class="d-meta">선두까지</div>
      <div class="s4-gap-msg" id="ms-gap-msg">—</div>
    </div>
    <div class="s4-gap-r" id="ms-gap-r-live">
      <span class="s4-gap-sign">−</span>
      <span class="s4-gap-n" id="ms-gap-n">—</span>
      <span class="s4-gap-u">pt</span>
    </div>
  </section>

  <section class="s4-gap" id="ms-gap-frozen" style="display:none">
    <div class="s4-gap-l">
      <div class="d-meta">현황</div>
      <div class="s4-gap-msg">
        현황은 캠프 종료 시점까지 동결됩니다.<br/>
        남은 도전 과제 풀이에 집중하세요.
      </div>
    </div>
    <div class="s4-gap-r">
      <span class="s4-gap-frozen-pill">
        <span class="d-pill-dot"></span>FROZEN
      </span>
    </div>
  </section>

  <footer class="s4-foot">
    <div class="s4-foot-l">
      <span class="d-tiny">DRAWN BY</span>
      <span class="s4-foot-v">E-CON 논설 · Auto-Grader</span>
    </div>
    <div class="s4-foot-r">
      <span class="d-tiny">REV</span>
      <span class="s4-foot-v" id="ms-rev">2026-A</span>
    </div>
  </footer>

</div>

<script>
(function() {
  var API = '/api/v1/digital/my-score';
  var REFRESH_MS = 20000;
  var timer = null;
  var hidden = false;

  document.addEventListener('visibilitychange', function() {
    hidden = document.hidden;
    if (!hidden) tick();
  });

  function tick() {
    fetch(API, { credentials: 'same-origin' })
      .then(function(r) {
        /* CTFd's @authed_only redirects 302 → /login when the session
           expires. fetch follows the redirect and we'd get login HTML
           with status 200, which then crashes r.json(). Detect the
           redirect explicitly and surface as an auth error. */
        if (r.redirected) throw new Error('auth');
        if (r.status === 401) throw new Error('auth');
        if (!r.ok) throw new Error('http ' + r.status);
        return r.json();
      })
      .then(render)
      .catch(handleError)
      .then(schedule);
  }

  function render(json) {
    var d = json.data || {};
    var root = document.getElementById('ms-root');
    var frozen = !!d.frozen;
    root.setAttribute('data-frozen', frozen ? 'true' : 'false');

    document.getElementById('ms-live-pill').style.display   = frozen ? 'none' : '';
    document.getElementById('ms-frozen-pill').style.display = frozen ? '' : 'none';
    document.getElementById('ms-updated').textContent = hms();
    document.getElementById('ms-rev').textContent = '2026-A' + (frozen ? ' · FROZEN' : '');

    var total = (d.total_points != null) ? d.total_points : 100;
    var totalCh = (d.total_challenges != null) ? d.total_challenges : 18;
    document.getElementById('ms-total-own').textContent    = total;
    document.getElementById('ms-total-leader').textContent = total;

    var own = (d.team && d.team.score != null) ? d.team.score : 0;
    var ownSolved = (d.team && d.team.solved != null) ? d.team.solved : 0;
    var teamName = (d.team && d.team.name) ? d.team.name : '—';
    var pct = total > 0 ? Math.round(own / total * 100) : 0;
    document.getElementById('ms-team-name').textContent = teamName;
    document.getElementById('ms-score-own').textContent = own;
    document.getElementById('ms-progress-meta').textContent =
      '달성률 · ' + pct + '%' + (frozen ? ' (동결 시점)' : '');
    document.getElementById('ms-prog-fill').style.width = pct + '%';
    document.getElementById('ms-solved-own').textContent = ownSolved + ' / ' + totalCh;

    var liveCard   = document.getElementById('ms-leader-live');
    var frozenCard = document.getElementById('ms-leader-frozen');
    var liveGap    = document.getElementById('ms-gap-live');
    var frozenGap  = document.getElementById('ms-gap-frozen');

    if (frozen) {
      liveCard.style.display   = 'none';
      frozenCard.style.display = '';
      liveGap.style.display    = 'none';
      frozenGap.style.display  = 'flex';
    } else {
      liveCard.style.display   = '';
      frozenCard.style.display = 'none';
      liveGap.style.display    = 'flex';
      frozenGap.style.display  = 'none';

      var leaderScore  = d.leader ? d.leader.score : null;
      var leaderSolved = (d.leader && d.leader.solved != null) ? d.leader.solved : null;

      if (leaderScore != null) {
        document.getElementById('ms-score-leader').textContent = leaderScore;
        document.getElementById('ms-solved-leader').textContent =
          (leaderSolved != null ? leaderSolved : '—') + ' / ' + totalCh;
      } else {
        document.getElementById('ms-score-leader').textContent = '—';
        document.getElementById('ms-solved-leader').textContent = '— / ' + totalCh;
      }

      var gapMsg = document.getElementById('ms-gap-msg');
      var gapR   = document.getElementById('ms-gap-r-live');
      if (leaderScore != null && leaderScore > own) {
        gapR.innerHTML =
          '<span class="s4-gap-sign">−</span>' +
          '<span class="s4-gap-n">' + (leaderScore - own) + '</span>' +
          '<span class="s4-gap-u">pt</span>';
        gapMsg.textContent = '남은 도전 과제로 충분히 따라잡을 수 있습니다.';
      } else if (leaderScore != null && leaderScore === own && own > 0) {
        gapR.innerHTML =
          '<span class="s4-gap-n">0</span>' +
          '<span class="s4-gap-u">pt</span>';
        gapMsg.textContent = '현재 선두와 동률입니다.';
      } else if (own > 0 && (leaderScore == null || own >= leaderScore)) {
        gapR.innerHTML =
          '<span class="s4-gap-n">1</span>' +
          '<span class="s4-gap-u">위</span>';
        gapMsg.textContent = '현재 1위입니다. 페이스를 유지하세요.';
      } else {
        gapR.innerHTML =
          '<span class="s4-gap-n">—</span>' +
          '<span class="s4-gap-u">pt</span>';
        gapMsg.textContent = '도전 과제를 시작해 보세요.';
      }
    }

    document.getElementById('ms-error').style.display = 'none';
  }

  function handleError(err) {
    var el = document.getElementById('ms-error');
    el.style.display = '';
    el.textContent = (err && err.message === 'auth')
      ? '로그인이 필요합니다. 페이지를 새로고침하여 다시 로그인하세요.'
      : '데이터를 불러오지 못했습니다. 네트워크 상태를 확인하세요.';
    document.getElementById('ms-updated').textContent = 'ERROR';
  }

  function schedule() {
    clearTimeout(timer);
    if (!hidden) timer = setTimeout(tick, REFRESH_MS);
  }

  function hms() {
    var d = new Date();
    function pad(n) { return n < 10 ? '0' + n : '' + n; }
    return pad(d.getHours()) + ' : ' + pad(d.getMinutes()) + ' : ' + pad(d.getSeconds());
  }

  tick();
})();
</script>
"""


PROJECTOR_CONTENT = """\
<style>
.s5-root {
  width: 100%;
  min-height: 100vh;
  background: var(--d-paper);
  font-family: var(--d-f-sans);
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  overflow-x: hidden;
}
.s5-root *, .s5-root *::before, .s5-root *::after { box-sizing: inherit; }

.s5-top {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  padding: 28px 56px;
  border-bottom: 1.5px solid var(--d-hair-strong);
  background: var(--d-paper);
}
.s5-top-l { display: flex; align-items: baseline; gap: 24px; }
.s5-mark {
  font-family: var(--d-f-mono);
  font-size: 20px;
  font-weight: 600;
  letter-spacing: 0.14em;
  color: var(--d-brand-dark);
}
.s5-doc {
  font-family: var(--d-f-ko);
  font-size: 18px;
  color: var(--d-ink-mid);
  letter-spacing: -0.005em;
}
.s5-top-c { justify-self: center; }
.s5-phase {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  font-family: var(--d-f-mono);
  font-size: 22px;
  font-weight: 600;
  color: var(--d-ink);
  letter-spacing: 0.14em;
  text-transform: uppercase;
  background: var(--d-paper-soft);
  border: 1px solid var(--d-hair-strong);
  padding: 12px 26px;
  border-radius: 999px;
}
.s5-phase-dot {
  width: 12px; height: 12px;
  background: var(--d-brand-dark);
  border-radius: 50%;
  box-shadow: 0 0 0 6px rgba(214,147,54,0.18);
  animation: d-pulse 2s ease infinite;
}
.s5-phase-project .s5-phase-dot {
  background: var(--d-ink);
  box-shadow: 0 0 0 6px rgba(21,17,10,0.10);
}
.s5-top-r { justify-self: end; display: inline-flex; align-items: baseline; gap: 16px; }
.s5-clock {
  font-family: var(--d-f-mono);
  font-size: 32px;
  font-weight: 600;
  color: var(--d-ink);
  letter-spacing: 0.08em;
  font-feature-settings: 'tnum';
}

/* ─── Practice mode stage ────────────────────────────── */
.s5p-stage {
  flex: 1;
  padding: 0 80px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 28px;
  min-height: 70vh;
}
.s5p-eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 16px;
}
.s5p-eyebrow .d-livedot { font-size: 16px; letter-spacing: 0.2em; }
.s5p-eyebrow-divider { width: 1px; height: 16px; background: var(--d-hair-strong); }
.s5p-eyebrow-en {
  font-family: var(--d-f-mono);
  font-size: 14px;
  letter-spacing: 0.18em;
  color: var(--d-ink-light);
  text-transform: uppercase;
}
.s5p-label {
  font-family: var(--d-f-ko);
  font-size: 36px;
  font-weight: 500;
  color: var(--d-ink-mid);
  letter-spacing: -0.02em;
}
.s5p-score {
  display: flex;
  align-items: flex-end;
  gap: 24px;
  font-feature-settings: 'tnum';
}
.s5p-score-n {
  font-family: var(--d-f-sans);
  font-size: 320px;
  font-weight: 700;
  line-height: 0.88;
  letter-spacing: -0.06em;
  color: var(--d-ink);
}
.s5p-score-right {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: flex-start;
  padding-bottom: 32px;
}
.s5p-score-u {
  font-family: var(--d-f-mono);
  font-size: 42px;
  color: var(--d-brand-dark);
  letter-spacing: 0.02em;
  font-weight: 500;
}
.s5p-score-f {
  font-family: var(--d-f-mono);
  font-size: 28px;
  color: var(--d-ink-light);
  letter-spacing: 0.04em;
}
.s5p-progress {
  width: 640px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.s5p-prog-track {
  height: 14px;
  background: var(--d-paper-sunk);
  border-radius: 7px;
  overflow: hidden;
}
.s5p-prog-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--d-brand-dark), var(--d-brand));
  transition: width 0.8s cubic-bezier(0.4, 0, 0.2, 1);
}
.s5p-prog-meta {
  display: flex;
  justify-content: space-between;
  font-family: var(--d-f-mono);
  font-size: 18px;
  color: var(--d-ink-mid);
  letter-spacing: 0.04em;
}
.s5p-note {
  font-family: var(--d-f-ko);
  font-size: 28px;
  color: var(--d-ink-light);
  margin-top: 8px;
  font-style: italic;
}

.s5p-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 28px 64px;
  border-top: 1.5px solid var(--d-hair-strong);
  background: var(--d-paper-soft);
  gap: 32px;
}
.s5p-foot-l .d-tiny { font-size: 13px; letter-spacing: 0.16em; }
.s5p-foot-stats { display: flex; align-items: center; gap: 32px; }
.s5p-fstat { display: flex; flex-direction: column; gap: 4px; align-items: flex-end; }
.s5p-fstat-n {
  font-family: var(--d-f-sans);
  font-weight: 600;
  font-size: 56px;
  letter-spacing: -0.035em;
  color: var(--d-ink);
  line-height: 0.95;
  font-feature-settings: 'tnum';
}
.s5p-fstat-l {
  font-family: var(--d-f-ko);
  font-size: 16px;
  color: var(--d-ink-light);
}
.s5p-fstat-sep { width: 1px; height: 56px; background: var(--d-hair-strong); }
.s5p-fstat-mut .s5p-fstat-n { color: var(--d-ink-mid); }

/* ─── Project mode stage ─────────────────────────────── */
.s5j-stage {
  flex: 1;
  padding: 36px 56px 44px;
  display: flex;
  flex-direction: column;
  gap: 24px;
}
.s5j-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--d-hair);
}
.s5j-head-l { display: flex; flex-direction: column; gap: 8px; }
.s5j-eyebrow.d-meta { font-size: 14px; letter-spacing: 0.18em; }
.s5j-h2 {
  font-family: var(--d-f-sans);
  font-weight: 600;
  font-size: 48px;
  letter-spacing: -0.025em;
  color: var(--d-ink);
  margin: 0;
}
.s5j-sub {
  font-family: var(--d-f-ko);
  font-size: 20px;
  color: var(--d-ink-light);
  margin: 4px 0 0;
  max-width: 720px;
  line-height: 1.5;
}
.s5j-head-r { display: flex; gap: 40px; }
.s5j-stat { display: flex; flex-direction: column; gap: 4px; align-items: flex-end; }
.s5j-stat .d-tiny { font-size: 13px; letter-spacing: 0.16em; }
.s5j-stat-v {
  font-family: var(--d-f-sans);
  font-weight: 600;
  font-size: 64px;
  letter-spacing: -0.035em;
  color: var(--d-ink);
  line-height: 0.95;
  font-feature-settings: 'tnum';
}
.s5j-stat-mut { color: var(--d-ink-light); font-size: 32px; }

.s5j-matrix-wrap { flex: 1; display: flex; align-items: stretch; }
.s5j-matrix {
  width: 100%;
  border-collapse: separate;
  border-spacing: 6px;
  table-layout: fixed;
}
.s5j-matrix thead th {
  padding: 4px 0 16px;
  vertical-align: bottom;
  font-weight: 500;
}
.s5j-th-team { width: 130px; }
.s5j-th-col { text-align: center; }
.s5j-th-id {
  font-family: var(--d-f-mono);
  font-size: 24px;
  font-weight: 600;
  color: var(--d-ink);
  letter-spacing: 0.04em;
  margin-bottom: 4px;
}
.s5j-th-name {
  font-family: var(--d-f-ko);
  font-size: 18px;
  color: var(--d-ink-light);
  letter-spacing: 0;
}
.s5j-td-team {
  text-align: right;
  padding-right: 18px;
  vertical-align: middle;
}
.s5j-team-tag {
  font-family: var(--d-f-sans);
  font-size: 40px;
  font-weight: 600;
  color: var(--d-ink);
  letter-spacing: -0.02em;
}
.s5j-cell {
  text-align: center;
  vertical-align: middle;
  background: var(--d-paper);
  border: 1px solid var(--d-hair);
  height: 110px;
  position: relative;
}
.s5j-cell-sub {
  background: var(--d-brand-soft);
  border-color: var(--d-brand);
  color: var(--d-brand-dark);
}
.s5j-cell-sub svg { width: 40px; height: 40px; display: inline-block; }
.s5j-cell-empty {
  background: var(--d-paper-soft);
  border-style: dashed;
  border-color: var(--d-hair);
}
.s5j-dash {
  font-family: var(--d-f-mono);
  font-size: 32px;
  color: var(--d-ink-soft);
}

.s5j-legend {
  display: flex;
  align-items: center;
  gap: 28px;
  padding: 18px 22px;
  background: var(--d-paper-soft);
  border: 1px solid var(--d-hair);
  border-radius: 6px;
}
.s5j-legend-item { display: inline-flex; align-items: center; gap: 12px; }
.s5j-cell-mini {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: 1px solid;
}
.s5j-cell-mini.s5j-cell-sub { background: var(--d-brand-soft); border-color: var(--d-brand); color: var(--d-brand-dark); }
.s5j-cell-mini.s5j-cell-sub svg { width: 14px; height: 14px; }
.s5j-cell-mini.s5j-cell-empty { background: var(--d-paper-soft); border-color: var(--d-hair); border-style: dashed; }
.s5j-cell-mini .s5j-dash { font-size: 16px; }
.s5j-legend-l {
  font-family: var(--d-f-ko);
  font-size: 18px;
  color: var(--d-ink-mid);
}
.s5j-legend-note { margin-left: auto; }
.s5j-legend-note .d-tiny { font-size: 14px; letter-spacing: 0.16em; }

.s5-error {
  padding: 18px 24px;
  background: var(--d-fail-soft);
  border: 1px solid var(--d-fail-line);
  color: var(--d-fail);
  font-family: var(--d-f-ko);
  font-size: 16px;
  margin: 24px 56px;
  border-radius: 6px;
}
</style>

<div class="s5-root" id="pj-root">

  <header class="s5-top">
    <div class="s5-top-l">
      <span class="s5-mark">◤ SNU · SENS</span>
      <span class="s5-doc">E-CON 논설 · Auto-Grader · 2026 공헌 공드림 캠프</span>
    </div>
    <div class="s5-top-c">
      <span class="s5-phase" id="pj-phase-pill">
        <span class="s5-phase-dot"></span>
        <span id="pj-phase-label">— —</span>
      </span>
    </div>
    <div class="s5-top-r">
      <span class="d-tiny">CLOCK</span>
      <span class="s5-clock" id="pj-clock">— — : — —</span>
    </div>
  </header>

  <div id="pj-error" class="s5-error" style="display:none"></div>

  <!-- Practice phase stage -->
  <main class="s5p-stage" id="pj-practice">

    <div class="s5p-eyebrow">
      <span class="d-livedot">LIVE · 익명</span>
      <span class="s5p-eyebrow-divider"></span>
      <span class="s5p-eyebrow-en">CURRENT LEADER · ANONYMOUS</span>
    </div>

    <div class="s5p-label">현재 선두</div>

    <div class="s5p-score">
      <span class="s5p-score-n" id="pj-leader-score">—</span>
      <div class="s5p-score-right">
        <span class="s5p-score-u">pt</span>
        <span class="s5p-score-f">/ <span id="pj-total-points">100</span></span>
      </div>
    </div>

    <div class="s5p-progress">
      <div class="s5p-prog-track">
        <div class="s5p-prog-fill" id="pj-leader-fill" style="width:0%"></div>
      </div>
      <div class="s5p-prog-meta">
        <span id="pj-leader-pct">달성률 —%</span>
        <span id="pj-leader-solved">— / — 도전 과제 해결</span>
      </div>
    </div>

    <div class="s5p-note">
      팀명은 캠프 종료 후 공개됩니다. 지금은 회로 설계에 집중하세요.
    </div>
  </main>

  <footer class="s5p-foot" id="pj-practice-foot">
    <div class="s5p-foot-l">
      <span class="d-tiny">최근 30분 · COLLECTIVE MOMENTUM</span>
    </div>
    <div class="s5p-foot-stats">
      <div class="s5p-fstat">
        <span class="s5p-fstat-n" id="pj-mom-solves">—</span>
        <span class="s5p-fstat-l">새 정답</span>
      </div>
      <span class="s5p-fstat-sep"></span>
      <div class="s5p-fstat">
        <span class="s5p-fstat-n" id="pj-mom-submits">—</span>
        <span class="s5p-fstat-l">제출</span>
      </div>
      <span class="s5p-fstat-sep"></span>
      <div class="s5p-fstat">
        <span class="s5p-fstat-n" id="pj-mom-teams">— / 4</span>
        <span class="s5p-fstat-l">참여 중인 조</span>
      </div>
    </div>
  </footer>

  <!-- Project phase stage -->
  <main class="s5j-stage" id="pj-project" style="display:none">

    <div class="s5j-head">
      <div class="s5j-head-l">
        <div class="s5j-eyebrow d-meta">SUBMISSION MATRIX · 제출 현황</div>
        <h2 class="s5j-h2">진행 중</h2>
        <p class="s5j-sub">
          점수와 통과 여부는 표시되지 않습니다. 채점은 캠프 종료 후 공개됩니다.
        </p>
      </div>
      <div class="s5j-head-r">
        <div class="s5j-stat">
          <span class="d-tiny">제출된 셀</span>
          <span class="s5j-stat-v" id="pj-filled-cells">—<span class="s5j-stat-mut"> / —</span></span>
        </div>
        <div class="s5j-stat">
          <span class="d-tiny">참여 조</span>
          <span class="s5j-stat-v" id="pj-active-teams">—<span class="s5j-stat-mut"> / —</span></span>
        </div>
      </div>
    </div>

    <div class="s5j-matrix-wrap">
      <table class="s5j-matrix">
        <thead id="pj-matrix-head"><tr><th></th></tr></thead>
        <tbody id="pj-matrix-body"></tbody>
      </table>
    </div>

    <div class="s5j-legend">
      <span class="s5j-legend-item">
        <span class="s5j-cell-mini s5j-cell-sub">
          <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
            <rect x="6" y="6" width="12" height="12" fill="currentColor"/>
          </svg>
        </span>
        <span class="s5j-legend-l">제출됨 (1회 이상)</span>
      </span>
      <span class="s5j-legend-item">
        <span class="s5j-cell-mini s5j-cell-empty"><span class="s5j-dash">—</span></span>
        <span class="s5j-legend-l">미제출</span>
      </span>
      <span class="s5j-legend-item s5j-legend-note">
        <span class="d-tiny">통과 여부 / 점수 · NOT SHOWN</span>
      </span>
    </div>
  </main>

</div>

<script>
(function() {
  var API = '/api/v1/digital/projector';
  var REFRESH_MS = 30000;
  var timer = null;
  var hidden = false;

  document.addEventListener('visibilitychange', function() {
    hidden = document.hidden;
    if (!hidden) tick();
  });

  /* clock updates locally every second */
  setInterval(function() {
    var d = new Date();
    function pad(n) { return n < 10 ? '0' + n : '' + n; }
    var el = document.getElementById('pj-clock');
    if (el) el.textContent = pad(d.getHours()) + ' : ' + pad(d.getMinutes());
  }, 1000);

  function svgGlyph() {
    return '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
      + '<rect x="6" y="6" width="12" height="12" fill="currentColor"/></svg>';
  }

  function tick() {
    fetch(API, { credentials: 'same-origin' })
      .then(function(r) {
        /* CTFd's @admins_only redirects 302 → /login for non-admin (or
           non-logged-in) viewers. fetch follows the redirect, returning
           login HTML at status 200 — which then crashes r.json(). Catch
           the redirect explicitly so the mentor laptop shows the
           "관리자 권한이 필요합니다" message instead of a parse error. */
        if (r.redirected) throw new Error('forbidden');
        if (r.status === 401) throw new Error('auth');
        if (r.status === 403) throw new Error('forbidden');
        if (!r.ok) throw new Error('http ' + r.status);
        return r.json();
      })
      .then(render)
      .catch(handleError)
      .then(schedule);
  }

  function render(json) {
    var d = json.data || {};
    var phase = d.phase || 'practice';

    var phasePill = document.getElementById('pj-phase-pill');
    var phaseLabel = document.getElementById('pj-phase-label');
    if (phase === 'project') {
      phasePill.classList.add('s5-phase-project');
      phaseLabel.textContent = '프로젝트 단계 · PROJECT PHASE';
    } else {
      phasePill.classList.remove('s5-phase-project');
      phaseLabel.textContent = '연습 단계 · PRACTICE PHASE';
    }

    var practiceStage   = document.getElementById('pj-practice');
    var practiceFoot    = document.getElementById('pj-practice-foot');
    var projectStage    = document.getElementById('pj-project');

    if (phase === 'project') {
      practiceStage.style.display = 'none';
      practiceFoot.style.display  = 'none';
      projectStage.style.display  = '';
      renderProject(d);
    } else {
      practiceStage.style.display = '';
      practiceFoot.style.display  = '';
      projectStage.style.display  = 'none';
      renderPractice(d);
    }

    document.getElementById('pj-error').style.display = 'none';
  }

  function renderPractice(d) {
    var total = (d.total_points != null) ? d.total_points : 100;
    var totalCh = (d.total_challenges != null) ? d.total_challenges : 18;
    document.getElementById('pj-total-points').textContent = total;

    var leader = d.leader || null;
    if (leader && leader.score != null) {
      var score = leader.score;
      var solved = leader.solved != null ? leader.solved : 0;
      var pct = total > 0 ? Math.round(score / total * 100) : 0;
      document.getElementById('pj-leader-score').textContent = score;
      document.getElementById('pj-leader-fill').style.width = pct + '%';
      document.getElementById('pj-leader-pct').textContent = '달성률 ' + pct + '%';
      document.getElementById('pj-leader-solved').textContent =
        solved + ' / ' + totalCh + ' 도전 과제 해결';
    } else {
      document.getElementById('pj-leader-score').textContent = '—';
      document.getElementById('pj-leader-fill').style.width = '0%';
      document.getElementById('pj-leader-pct').textContent = '달성률 —%';
      document.getElementById('pj-leader-solved').textContent = '— / ' + totalCh + ' 도전 과제 해결';
    }

    var mom = d.momentum || {};
    document.getElementById('pj-mom-solves').textContent =
      mom.new_solves != null ? mom.new_solves : '—';
    document.getElementById('pj-mom-submits').textContent =
      mom.submits != null ? mom.submits : '—';
    var act = mom.active_teams != null ? mom.active_teams : '—';
    var tot = mom.total_teams != null ? mom.total_teams : '—';
    document.getElementById('pj-mom-teams').textContent = act + ' / ' + tot;
  }

  function renderProject(d) {
    var cols  = d.cols  || [];
    var teams = d.teams || [];

    var thead = document.getElementById('pj-matrix-head');
    var tbody = document.getElementById('pj-matrix-body');

    var html = '<tr><th class="s5j-th-team"></th>';
    cols.forEach(function(c) {
      html += '<th class="s5j-th-col">'
            + '<div class="s5j-th-id">' + escHtml(c.short || '') + '</div>'
            + '<div class="s5j-th-name">' + escHtml(c.name || '') + '</div>'
            + '</th>';
    });
    html += '</tr>';
    thead.innerHTML = html;

    var bhtml = '';
    var filled = 0;
    var active = 0;
    teams.forEach(function(t) {
      bhtml += '<tr><td class="s5j-td-team"><span class="s5j-team-tag">'
            + escHtml(t.name || '—') + '</span></td>';
      var any = false;
      (t.submits || []).forEach(function(v) {
        if (v) { filled += 1; any = true; }
        bhtml += '<td class="s5j-cell ' + (v ? 's5j-cell-sub' : 's5j-cell-empty') + '">'
              + (v ? svgGlyph() : '<span class="s5j-dash">—</span>')
              + '</td>';
      });
      bhtml += '</tr>';
      if (any) active += 1;
    });
    tbody.innerHTML = bhtml;

    var totalCells = teams.length * cols.length;
    document.getElementById('pj-filled-cells').innerHTML =
      filled + '<span class="s5j-stat-mut"> / ' + totalCells + '</span>';
    document.getElementById('pj-active-teams').innerHTML =
      active + '<span class="s5j-stat-mut"> / ' + teams.length + '</span>';
  }

  function handleError(err) {
    var el = document.getElementById('pj-error');
    el.style.display = '';
    if (err && err.message === 'auth') {
      el.textContent = '로그인이 필요합니다.';
    } else if (err && err.message === 'forbidden') {
      el.textContent = '관리자 권한이 필요합니다.';
    } else {
      el.textContent = '데이터를 불러오지 못했습니다. (' + (err && err.message ? err.message : 'unknown') + ')';
    }
  }

  function schedule() {
    clearTimeout(timer);
    if (!hidden) timer = setTimeout(tick, REFRESH_MS);
  }

  function escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  tick();
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
            ms_page.draft = False
            db.session.commit()
            print("[bootstrap] /my-score page synced")

        # /projector page — public projector view. Auth not required so the
        # mentor laptop can leave it open without a login session timing out;
        # the data endpoint behind it (/api/v1/digital/projector) is
        # @admins_only, so non-admin viewers see chrome but no payload.
        pj_page = Pages.query.filter_by(route="projector").first()
        if pj_page is None:
            db.session.add(Pages(
                title="Projector",
                route="projector",
                content=PROJECTOR_CONTENT,
                draft=False,
                hidden=True,  # hidden from the public Pages menu
                auth_required=False,
                format="html",
            ))
            db.session.commit()
            print("[bootstrap] /projector page created")
        else:
            pj_page.title = "Projector"
            pj_page.content = PROJECTOR_CONTENT
            pj_page.format = "html"
            pj_page.auth_required = False
            pj_page.hidden = True
            pj_page.draft = False
            db.session.commit()
            print("[bootstrap] /projector page synced")

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
