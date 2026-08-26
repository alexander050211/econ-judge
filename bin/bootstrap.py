"""Bootstrap CTFd on first container start: create admin, mark setup complete,
seed the 15 econ-judge challenges and a minimal index page. Idempotent —
running on every boot lets the deploy survive Render free tier's ephemeral
disk."""

from __future__ import annotations

import datetime
import importlib.util
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CTFD_ROOT = Path(os.environ.get("CTFD_ROOT", "/opt/CTFd"))
sys.path.insert(0, str(CTFD_ROOT))

from CTFd import create_app
from CTFd.models import Challenges, Fails, Pages, Solves, Users, db
from CTFd.utils import get_config, set_config

ADMIN_NAME = os.environ.get("CTFD_ADMIN_NAME", "admin")
ADMIN_EMAIL = os.environ.get("CTFD_ADMIN_EMAIL", "admin@econ-judge.local")
ADMIN_PASSWORD = os.environ.get("CTFD_ADMIN_PASSWORD", "demo1234")
CTF_NAME = os.environ.get("CTFD_NAME", "SNU SENS E-CON 논설")
CTF_DESCRIPTION = os.environ.get(
    "CTFD_DESCRIPTION", "2026 하계 공학 캠프 E-CON 논설 (논리설계) 자동채점 시스템"
)
PROBLEM_SET_VERSION = "2026-summer-v1"
# Final HWP flat-number migration. The five entries are a permutation, so
# existing participant history can follow its semantic challenge rather than
# being deleted when the CTFd primary-key numbers are repurposed.
CHALLENGE_NUMBERING_VERSION = "2026-summer-hwp-flat-v1"
CHALLENGE_ID_RENUMBER = {7: 9, 8: 10, 9: 11, 10: 7, 11: 8}

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
_freeze_env_raw = os.environ.get("CTFD_FREEZE_AT")
_freeze_env = (_freeze_env_raw or "").strip()
# Absent vs. present-but-empty are different intents and a Postgres config
# row outlives the process, so the empty string has to stay reachable: it is
# the only env-side way to CLEAR a freeze (see the set_config call in main).
WRITE_FREEZE = _freeze_env_raw is not None
try:
    FREEZE_AT = int(_freeze_env) if _freeze_env else None
except ValueError:
    print(f"[bootstrap] ignoring invalid CTFD_FREEZE_AT={_freeze_env!r}")
    FREEZE_AT = None
    WRITE_FREEZE = False  # "ignoring" must not silently mean "clearing"

# Four demo teams matching the camp's actual 4-team structure. Solves are
# (challenge_id, minutes_ago_from_now) — spread realistically over a
# 2-hour window to mimic mid-contest state, with easy challenges first,
# harder composition challenges later, and progressive difficulty stacks
# (1조 cleared 14/15, 2조 10/15, 3조 7/15, 4조 3/15).
DEMO_PASSWORD = "demo1234"
# _seed_demo_data stamps this into Solves.provided. A real submission always
# records the uploaded starter's basename there, so this marker is what lets
# _clear_demo_data delete fabricated rows without ever touching a real solve.
DEMO_SOLVE_MARKER = "(demo seed).dig"
DEMO_TEAMS = [
    {
        "name": "1조",
        "email": "team1@econ-judge.local",
        "solves": [
            (1, 118), (2, 114), (3, 108), (4, 103),
            (5, 96), (6, 91), (9, 84), (10, 76), (11, 66),
            (7, 55), (8, 43),
            (12, 34), (13, 25), (15, 16),
        ],  # total: 70 pts
    },
    {
        "name": "2조",
        "email": "team2@econ-judge.local",
        "solves": [
            (1, 116), (2, 111), (3, 105), (4, 99),
            (5, 92), (6, 86), (9, 77), (10, 65), (11, 52), (7, 35),
        ],  # total: 45 pts
    },
    {
        "name": "3조",
        "email": "team3@econ-judge.local",
        "solves": [
            (1, 112), (2, 106), (3, 99), (4, 91),
            (5, 78), (6, 67), (9, 51),
        ],  # total: 21 pts
    },
    {
        "name": "4조",
        "email": "team4@econ-judge.local",
        "solves": [
            (1, 108), (2, 97), (3, 82),
        ],  # total: 7 pts
    },
]

# DEMO_TEAMS' name/email double as the canonical CAMP ROSTER (the `solves`
# field is demo-only). The roster is re-seeded on every boot (see
# _seed_roster) so a Render redeploy / free-tier cold-start — which wipes the
# ephemeral SQLite — can never lock teams out. Real camp passwords live in
# Render's Environment, out of git, same as the admin password:
#   CTFD_TEAM_PASSWORD          shared default for all teams
#   CTFD_TEAM<N>_PASSWORD       per-team override (N = digits in the name,
#                               e.g. "1조" -> CTFD_TEAM1_PASSWORD)
# Falls back to DEMO_PASSWORD if neither is set (fine for review deploys).
TEAM_PASSWORD = os.environ.get("CTFD_TEAM_PASSWORD", DEMO_PASSWORD)


def _team_password(name: str) -> str:
    digits = "".join(c for c in name if c.isdigit())
    if digits:
        per = os.environ.get(f"CTFD_TEAM{digits}_PASSWORD")
        if per:
            return per
    return TEAM_PASSWORD


def _team_password_source(name: str) -> str:
    """Return the selected password source without ever logging its value."""
    digits = "".join(c for c in name if c.isdigit())
    if digits and os.environ.get(f"CTFD_TEAM{digits}_PASSWORD"):
        return "per-team override"
    if os.environ.get("CTFD_TEAM_PASSWORD"):
        return "shared default"
    return "INSECURE public fallback"


def _team_password_is_set() -> bool:
    """True if ANY team password env var is set (shared or per-team). Used to
    warn when a real camp deploy would silently fall back to the demo password."""
    if os.environ.get("CTFD_TEAM_PASSWORD"):
        return True
    return any(
        os.environ.get(f"CTFD_TEAM{''.join(c for c in t['name'] if c.isdigit())}_PASSWORD")
        for t in DEMO_TEAMS
    )

# SENS brand palette extracted from sens.snu.ac.kr's CSS. SENS the club uses
# warm orange/amber, distinct from SNU University's navy. Loaded globally
# via CTFd's `theme_header` config so all pages (login, scoreboard, admin)
# pick up the same Pretendard + color tokens without per-page styling.
THEME_HEADER_CSS = """\
<style id="econ-judge-theme">
/* E-CON 논설 — Direction D editorial-minimal theme. This block is the
   only copy of the theme CSS; keep the comment free of any literal HTML
   tag sequences, which the browser's HTML parser would otherwise treat
   as terminating this style block. */

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

/* ── Dark mode ────────────────────────────────────────────────────
 *
 * CTFd's color_mode_switcher.js sets `data-bs-theme="dark"` on <html>
 * (Bootstrap 5.3 convention) when the navbar sun/moon button is clicked
 * or when prefers-color-scheme: dark is detected. We swap the token
 * palette under that selector — warm charcoal paper, cream phosphor ink,
 * amber stays as the brand accent. Every surface that uses `var(--d-*)`
 * inherits the swap automatically (landing, /my-score, /projector, s2
 * challenges table, s7 login). A handful of inverted-treatment elements
 * (the ink-filled primary button, admin tags, drop-zone glyph) need
 * element-level overrides because they treat --d-ink as a background —
 * flipping ink to cream alone would produce cream-on-dark mush.
 */

:root[data-bs-theme="dark"] {
  --d-paper:       #0e0b07;
  --d-paper-soft:  #161108;
  --d-paper-sunk:  #1f1810;

  --d-ink:         #f5ecd6;
  --d-ink-mid:     #c9b88e;
  --d-ink-light:   #8e7a52;
  --d-ink-soft:    #5a4d33;

  --d-hair:        rgba(245, 168, 61, 0.10);
  --d-hair-strong: rgba(245, 168, 61, 0.22);

  --d-brand:       #f5a83d;
  --d-brand-dark:  #ffc26a;
  --d-brand-ink:   #ffc26a;
  --d-brand-soft:  rgba(245, 168, 61, 0.10);
  --d-brand-line:  rgba(245, 168, 61, 0.32);

  --d-pass:        #6fb368;
  --d-pass-soft:   rgba(111, 179, 104, 0.10);
  --d-pass-line:   rgba(111, 179, 104, 0.32);
  --d-fail:        #d97757;
  --d-fail-soft:   rgba(217, 119, 87, 0.10);
  --d-fail-line:   rgba(217, 119, 87, 0.32);
  --d-warn:        #f5a83d;
  --d-warn-soft:   rgba(245, 168, 61, 0.10);
  --d-warn-line:   rgba(245, 168, 61, 0.32);
}

/* Inverted-treatment overrides — these elements use --d-ink as a
   BACKGROUND in light mode (dark-on-light contrast block). In dark
   mode flip to amber-filled with dark ink as text, echoing Direction
   E's "RUN" key chrome rather than producing cream-on-dark mush. */
:root[data-bs-theme="dark"] .btn-primary,
:root[data-bs-theme="dark"] button[type="submit"],
:root[data-bs-theme="dark"] .submit-row > .key-submit .challenge-submit,
:root[data-bs-theme="dark"] .d-btn-primary {
  background-color: var(--d-brand) !important;
  border-color: var(--d-brand) !important;
  color: #0e0b07 !important;
}
:root[data-bs-theme="dark"] .btn-primary:hover,
:root[data-bs-theme="dark"] button[type="submit"]:hover,
:root[data-bs-theme="dark"] .submit-row > .key-submit .challenge-submit:hover,
:root[data-bs-theme="dark"] .d-btn-primary:hover {
  background-color: var(--d-brand-dark) !important;
  border-color: var(--d-brand-dark) !important;
  color: #0e0b07 !important;
  box-shadow: 0 0 18px rgba(245, 168, 61, 0.22);
}

/* Phosphor glow on the key numeric heroes in dark mode — gives the
   landing + challenges cards a CRT-instrument quality without changing
   their light-mode rendering. */
:root[data-bs-theme="dark"] .s1-stat-num,
:root[data-bs-theme="dark"] .s2-prog-val {
  text-shadow: 0 0 14px rgba(245, 168, 61, 0.18),
               0 0 28px rgba(245, 168, 61, 0.06);
}

/* CTFd Pages content authored before the token system uses raw hex —
   nothing we can do server-side; the user-authored Page CSS stays light.
   Anything WE author (INDEX_CONTENT, MY_SCORE_CONTENT, PROJECTOR_CONTENT,
   s2/s7) uses var(--d-*) and inherits the dark swap above. */

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
<script defer src="/plugins/econ_judge/assets/round-ui.js"></script>
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
      <span class="s1-tm-mid">SNU SENS &middot; 2026 하계 공학 캠프</span>
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
        <div class="s1-stat-num">15</div>
        <div class="s1-stat-en">8문제 + 7문제 · 2 rounds</div>
      </div>
      <div class="s1-stat">
        <div class="d-meta">총점</div>
        <div class="s1-stat-num">80<span class="s1-stat-unit">pt</span></div>
        <div class="s1-stat-en">1라운드 35 pt + 2라운드 45 pt</div>
      </div>
      <div class="s1-stat">
        <div class="d-meta">참가 팀</div>
        <div class="s1-stat-num">4</div>
        <div class="s1-stat-en">조별 약 3명 / 총 12명</div>
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
            <div class="s1-step-sub">1라운드가 끝난 뒤 2라운드가 열립니다. 각 라운드의 제한 시간을 확인하세요.</div>
          </div>
        </li>
        <li class="s1-step">
          <span class="s1-step-n">02</span>
          <div class="s1-step-body">
            <div class="s1-step-h">문제에 맞는 답안 또는 <code class="d-code">.dig</code> 회로를 준비합니다</div>
            <div class="s1-step-sub">회로 문제는 조합논리로 설계하며 클럭과 플립플롭은 사용할 수 없습니다.</div>
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
            <div class="s1-step-h">결과를 확인하고 회로를 보완하세요</div>
            <div class="s1-step-sub">회로 문제는 재제출할 수 있으며, 진리표 문제는 한 번만 제출할 수 있습니다.</div>
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
        <span class="s1-foot-v">2026-07-15</span>
      </div>
    </footer>

  </div>
</div>
"""

# /my-score page content — a static shell only. econ_judge/assets/round-ui.js,
# loaded on every page from THEME_HEADER_CSS, replaces the #ms-root element
# wholesale via outerHTML and owns the live UI from there. The one contract
# this Page has to keep is that #ms-root exists when that script runs.
MY_SCORE_CONTENT = """\
<style>
.econ-shell {
  width: 100%;
  padding: 72px 24px;
  text-align: center;
  font-family: var(--d-f-ko);
  color: var(--d-ink-light);
}
.econ-shell-label {
  font-family: var(--d-f-mono);
  font-size: 11px;
  letter-spacing: 0.13em;
  text-transform: uppercase;
  color: var(--d-ink-soft);
}
.econ-shell-msg { margin: 12px 0 0; font-size: 15px; }
</style>

<div class="econ-shell" id="ms-root">
  <div class="econ-shell-label">SNU SENS · E-CON 논설</div>
  <p class="econ-shell-msg">점수를 불러오는 중입니다.</p>
  <noscript>
    <p class="econ-shell-msg">이 화면은 JavaScript가 필요합니다. 브라우저에서 JavaScript를 켠 뒤 새로고침해 주세요.</p>
  </noscript>
</div>
"""


# /projector page content — a static shell only, same contract as
# MY_SCORE_CONTENT above: round-ui.js replaces #pj-root via outerHTML.
PROJECTOR_CONTENT = """\
<style>
.econ-shell {
  width: 100%;
  padding: 72px 24px;
  text-align: center;
  font-family: var(--d-f-ko);
  color: var(--d-ink-light);
}
.econ-shell-label {
  font-family: var(--d-f-mono);
  font-size: 11px;
  letter-spacing: 0.13em;
  text-transform: uppercase;
  color: var(--d-ink-soft);
}
.econ-shell-msg { margin: 12px 0 0; font-size: 15px; }
</style>

<div class="econ-shell" id="pj-root">
  <div class="econ-shell-label">SNU SENS · E-CON 논설 · 운영 화면</div>
  <p class="econ-shell-msg">현황을 불러오는 중입니다.</p>
  <noscript>
    <p class="econ-shell-msg">이 화면은 JavaScript가 필요합니다. 브라우저에서 JavaScript를 켠 뒤 새로고침해 주세요.</p>
  </noscript>
</div>
"""

def _load_challenges():
    challenge_file = Path(
        os.environ.get(
            "ECON_JUDGE_CHALLENGES_FILE",
            REPO_ROOT / "tests" / "register_challenges.py",
        )
    )
    spec = importlib.util.spec_from_file_location(
        "register_challenges", challenge_file
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.CHALLENGES


CHALLENGES = _load_challenges()


def _migrate_challenge_numbering() -> None:
    """Move Solve/Fail rows to the final HWP's flat challenge numbers once.

    Existing CTFd challenge primary keys are repurposed by the bootstrap
    upsert. This two-pass offset avoids transient unique-key collisions while
    preserving any review submissions that already exist on the service.
    """
    if get_config("econ_challenge_numbering_version") == CHALLENGE_NUMBERING_VERSION:
        return

    moved = 0
    for model in (Solves, Fails):
        records = model.query.filter(
            model.challenge_id.in_(CHALLENGE_ID_RENUMBER)
        ).all()
        for record in records:
            record.challenge_id += 100
        db.session.flush()
        for record in records:
            record.challenge_id = CHALLENGE_ID_RENUMBER[record.challenge_id - 100]
        moved += len(records)
    set_config("econ_challenge_numbering_version", CHALLENGE_NUMBERING_VERSION)
    db.session.commit()
    print(f"[bootstrap] HWP flat challenge numbering migrated ({moved} records)")


def main() -> None:
    app = create_app()
    with app.app_context():
        db.create_all()
        _migrate_challenge_numbering()

        # Challenge ids are reused between camps. Reset attempt history once
        # on a problem-set change so an old Solve cannot score a new problem.
        previous_problem_set = get_config("econ_problem_set_version")
        if previous_problem_set != PROBLEM_SET_VERSION:
            solve_count = Solves.query.delete(synchronize_session=False)
            fail_count = Fails.query.delete(synchronize_session=False)
            set_config("econ_problem_set_version", PROBLEM_SET_VERSION)
            db.session.commit()
            print(
                f"[bootstrap] Problem set migrated to {PROBLEM_SET_VERSION}; "
                f"cleared {solve_count} solves and {fail_count} fails"
            )

        # All configs are always synced — bootstrap is "configuration as
        # code." Changing a value here + redeploying actually rotates the
        # config; the deploy state never drifts from what's in this file.
        set_config("ctf_name", CTF_NAME)
        set_config("ctf_description", CTF_DESCRIPTION)
        set_config("ctf_theme", "core-beta")
        set_config("user_mode", "users")
        set_config("default_locale", "ko")
        set_config("challenge_visibility", "public")
        # Pre-issued team accounts only — no public self-signup. The grade
        # endpoint runs a JVM subprocess on uploaded files, so the auth wall is
        # part of the attack-surface control. Flip to "public" only for a
        # controlled onboarding window, then flip back.
        set_config("registration_visibility", "private")
        # Score visibility is admin-only to prevent the "we're 4th of 4"
        # demoralization. Mentees see only their own + (anonymized) leader's
        # score via /my-score, served by the econ_judge plugin endpoint that
        # bypasses this visibility lock.
        set_config("score_visibility", "admins")
        # Account visibility also admin-only — hides /users entirely so the
        # roster of other teams isn't a "who else is here" social distraction.
        # Cascades to /api/v1/users, /users/{id}, and the navbar Users link.
        set_config("account_visibility", "admins")
        # The one config deliberately NOT force-synced, and the rule has two
        # halves. CTFD_FREEZE_AT absent: leave the stored value alone, so a
        # freeze set through the CTFd admin UI survives every boot. Present:
        # write it — including present-but-empty, which stores None and is
        # the operator's deliberate un-freeze (get_config("freeze") then falls
        # back to its None default, so /my-score and /projector unfreeze).
        # Invalid values are warned about above and change nothing.
        if WRITE_FREEZE:
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
        # the category order to 1라운드 → 2라운드 (final contest flow)
        # and sort challenges within each category by id ASC, which matches the
        # authoring order — easier sub-circuits first, composition challenges
        # (Full Wiring) at the bottom.
        set_config(
            "theme_settings",
            json.dumps(
                {
                    "challenge_category_order": (
                        "(a, b) => { const o = {"
                        "'1라운드': 0, '2라운드': 1"
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
        # on assignment. Wrapped like the roster seeding because entrypoint.sh
        # runs under `set -e`: an IntegrityError here (e.g. a changed
        # CTFD_ADMIN_EMAIL colliding with an existing row) would otherwise take
        # the whole site down. A running site with a stale admin password is
        # recoverable; a container that will not boot is not.
        try:
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
        except Exception as exc:  # noqa: BLE001 — never let this stop the boot
            db.session.rollback()
            print(f"[bootstrap] Admin '{ADMIN_NAME}': FAILED to upsert: {exc}")

        # Smoke-test user (hidden) — pre-created so deploy_smoke.py runs
        # don't pollute the public scoreboard. Idempotent: created if missing,
        # otherwise hidden flag + password reset to canonical values. Same
        # boot-safety wrapper as the admin upsert above, for the same reason.
        try:
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
        except Exception as exc:  # noqa: BLE001 — never let this stop the boot
            db.session.rollback()
            print(f"[bootstrap] Smoke user '{SMOKE_NAME}': FAILED to upsert: {exc}")

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
            page.auth_required = False
            page.hidden = False
            page.draft = False
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

        # Challenges are upserted on every boot from register_challenges.py.
        # Rows removed from the active set are hidden so winter challenges
        # cannot survive when the database persists across a deploy.
        existing_chals = {c.id: c for c in Challenges.query.all()}
        active_ids = {challenge[0] for challenge in CHALLENGES}
        created = 0
        updated = 0
        retired = 0
        for cid, chal in existing_chals.items():
            if cid not in active_ids and chal.state != "hidden":
                chal.state = "hidden"
                retired += 1
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
            if chal.type != "digital":
                chal.type = "digital"; dirty = True
            if dirty:
                updated += 1
        if created or updated or retired:
            db.session.commit()
            print(
                f"[bootstrap] Challenges: {created} created, {updated} updated, "
                f"{retired} retired"
            )
        else:
            print(f"[bootstrap] All {len(CHALLENGES)} challenges already in sync")

        # Roster ALWAYS runs (independent of the demo toggle) so a wiped DB
        # re-seeds the team accounts and no team can be locked out by a
        # redeploy / cold-start. Demo SOLVES are the only demo-toggle-gated part.
        _seed_roster()

        if SEED_DEMO_DATA:
            _seed_demo_data()
        else:
            print("[bootstrap] CTFD_DEMO_DATA=false — skipping demo solves (roster still seeded)")
            _clear_demo_data()


def _seed_roster() -> None:
    """Idempotently ensure the 4 camp team accounts exist, on EVERY boot.

    This is the durability fix for Render's ephemeral SQLite: a redeploy or
    free-tier cold-start wipes the DB, and bootstrap restores admin +
    challenges — but without this the team roster would be gone and every
    team locked out (registration is private). Passwords are re-synced from
    env each boot (see _team_password), mirroring the admin-password pattern,
    so rotating a team password = set the env var + redeploy.

    Solves are never touched here — only account identity. Demo solves (if
    enabled) are layered on separately by _seed_demo_data.

    Camp-day safety: if real-contest mode (CTFD_DEMO_DATA=false) is active but
    no team password env var is set, every team would silently get the public
    demo password — so we warn loudly. We do NOT hard-fail: refusing to boot
    during a mid-camp cold-start would cause the very lockout this function
    exists to prevent; a working-but-weak roster + a loud log is the safer
    failure mode. Each team is committed independently so one bad row (e.g. a
    stray unique-constraint clash) can't roll back the whole roster."""
    if not SEED_DEMO_DATA and not _team_password_is_set():
        print(
            "[bootstrap] WARNING: CTFD_DEMO_DATA=false (real camp) but no "
            "CTFD_TEAM_PASSWORD / CTFD_TEAM<N>_PASSWORD is set; all teams are "
            "using the PUBLIC demo password. Set a team password in Render's "
            "Environment and redeploy."
        )
    created = synced = failed = 0
    password_sources = []
    for team in DEMO_TEAMS:
        pw = _team_password(team["name"])
        password_sources.append(f"{team['name']}={_team_password_source(team['name'])}")
        try:
            user = Users.query.filter_by(name=team["name"]).first()
            if user is None:
                user = Users(
                    name=team["name"],
                    email=team["email"],
                    password=pw,
                    type="user",
                    verified=True,
                    hidden=False,
                )
                db.session.add(user)
                created += 1
            else:
                # Re-sync password (env is source of truth) and ensure visible.
                user.password = pw  # @validates('password') re-hashes on assign
                if user.hidden:
                    user.hidden = False
                synced += 1
            db.session.commit()  # per-team: one failure can't sink the rest
        except Exception as exc:  # noqa: BLE001 — keep seeding the other teams
            db.session.rollback()
            failed += 1
            print(f"[bootstrap] Roster: FAILED to seed {team['name']}: {exc}")
    tail = f", {failed} FAILED" if failed else ""
    print(
        f"[bootstrap] Roster: {created} created, {synced} synced{tail} "
        f"({len(DEMO_TEAMS)} total; password sources: {', '.join(password_sources)})"
    )


def _seed_demo_data() -> None:
    """Layer fabricated Solves onto the (already-seeded) roster for review
    deploys. Idempotent: skips Solves seeding for any user that already has
    Solves. Assumes _seed_roster has created the accounts."""
    now = datetime.datetime.utcnow()
    for team in DEMO_TEAMS:
        user = Users.query.filter_by(name=team["name"]).first()
        if user is None:
            # Roster seeding should have created this; guard defensively.
            print(f"[demo] {team['name']} missing after roster seed — skipping solves")
            continue

        if Solves.query.filter_by(user_id=user.id).first() is not None:
            continue

        for chal_id, minutes_ago in team["solves"]:
            solve = Solves(
                user_id=user.id,
                team_id=None,
                challenge_id=chal_id,
                ip="127.0.0.1",
                provided=DEMO_SOLVE_MARKER,
            )
            solve.date = now - datetime.timedelta(minutes=minutes_ago)
            db.session.add(solve)
        db.session.commit()
        print(f"[demo] {team['name']}: {len(team['solves'])} solves seeded")


def _clear_demo_data() -> None:
    """Delete Solves left behind by an earlier CTFD_DEMO_DATA=true boot.

    Flipping the toggle off is not enough on a database that survives a
    redeploy: without this, fabricated review-deploy scores would still be on
    the scoreboard on camp day. Only rows stamped with DEMO_SOLVE_MARKER are
    touched — a real submission always records the uploaded starter's
    basename in `provided`, so it can never match.

    Deliberately per-object, NOT a bulk .delete(): Solves is joined-table
    inheritance (child "solves", parent "submissions") and `provided` lives
    only on the parent, so a bulk delete carries multi-table criteria —
    SQLite raises NotImplementedError at compile time on EVERY boot (even an
    empty DB), and PostgreSQL compiles `DELETE FROM solves USING submissions`
    with no join predicate, a cross product that wipes real solves while
    leaving the marked parent rows — and hence the marker — behind. An ORM
    delete per object removes both rows; the set is ~34 at most."""
    try:
        doomed = Solves.query.filter_by(provided=DEMO_SOLVE_MARKER).all()
        for solve in doomed:
            db.session.delete(solve)
        db.session.commit()
        print(f"[bootstrap] Demo solves removed: {len(doomed)}")
    except Exception as exc:  # noqa: BLE001 — never let this stop the boot
        db.session.rollback()
        print(f"[bootstrap] Demo solves: FAILED to remove: {exc}")


if __name__ == "__main__":
    main()
