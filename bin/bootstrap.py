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

# The whole product's design tokens. SENS the club brands itself in warm
# orange/amber (from sens.snu.ac.kr), distinct from SNU University's navy;
# Direction B keeps that amber as a mark rather than a palette, because it
# cannot carry text on a near-white ground, and gives the work of an
# accent colour to blue. Loaded globally via CTFd's `theme_header` config
# so every page (login, scoreboard, admin) picks up the same fonts and
# color tokens without per-page styling.
THEME_HEADER_CSS = """\
<style id="econ-judge-theme">
/* E-CON 논설 — Direction B "editorial product" theme. This block is the
   only copy of the theme CSS; keep the comment free of any literal HTML
   tag sequences, which the browser's HTML parser would otherwise treat
   as terminating this style block. Cool-neutral ground, white cards,
   hairline borders, one blue action colour. The mandated SENS amber
   survives in exactly two places — the navbar brand dot and the PARTIAL
   verdict disc — because amber text does not clear AA on near-white. */

/* ── Font imports ────────────────────────────────────────────────── */

/* Pretendard is imported first AND listed ahead of Noto Sans KR in every
   stack below. Font matching is per character, so whichever Korean face
   comes first in the stack claims all Hangul; put Noto first and the
   jsDelivr file is downloaded on every page load and never drawn. */
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable.min.css');
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400..700&family=Geist+Mono:wght@400..700&family=Noto+Sans+KR:wght@400;500;700&display=swap');

/* ── Tokens ──────────────────────────────────────────────────────── */

:root {
  /* Direction B palette. These were contrast-checked as a set, so reach
     for one of them rather than mixing a new shade: --d-text-3 is the
     lowest rung and only clears 4.5:1 against --d-surface, never against
     --d-surface-2. */
  --d-bg:            #f7f8fa;
  --d-surface:       #ffffff;
  --d-surface-2:     #f1f3f6;
  --d-console:       #f1f3f6;
  --d-border:        #e4e7ec;
  --d-border-strong: #cfd5dd;

  --d-ink:           #1a1d23;
  --d-text-2:        #5c6370;
  --d-text-3:        #626977;

  --d-accent:        #2457e0;
  --d-accent-hover:  #1e4bc7;
  --d-accent-soft:   #e9efff;
  --d-accent-text:   #1e47b8;
  --d-on-accent:     #ffffff;

  --d-ok:            #0f7a57;
  --d-ok-soft:       #e3f6ec;
  --d-ok-text:       #0f6b4c;
  --d-ok-line:       #bcd6cd;
  --d-on-ok:         #ffffff;

  /* On light, amber is a fill under dark glyphs and never a text colour:
     --d-warn paints the PARTIAL disc, --d-warn-fill the bar and the card
     strip, --d-warn-text the words. In dark the two amber roles merge. */
  --d-warn:          #f5a83d;
  --d-warn-fill:     #8a5412;
  --d-warn-soft:     #fff4e0;
  --d-warn-text:     #8a5412;
  --d-warn-line:     #dcccb8;
  --d-on-warn:       #1a1d23;

  --d-bad:           #c93833;
  --d-bad-soft:      #fdeceb;
  --d-bad-text:      #b42323;
  --d-bad-line:      #eac1c1;
  --d-on-bad:        #ffffff;

  --d-notice-soft:   #f1f3f6;
  --d-notice-text:   #5c6370;

  /* Brand — SENS, mandated */
  --d-brand:         #f5a83d;

  --d-focus:         rgba(36,87,224,.35);
  --d-navbar:        rgba(255,255,255,.85);

  --d-shadow-1: 0 1px 2px rgba(16,24,40,.04), 0 1px 3px rgba(16,24,40,.06);
  --d-shadow-2: 0 0 0 1px rgba(16,24,40,.04), 0 4px 12px -2px rgba(16,24,40,.06), 0 12px 32px -8px rgba(16,24,40,.08);
  --d-shadow-3: 0 0 0 1px rgba(16,24,40,.05), 0 12px 24px -8px rgba(16,24,40,.10), 0 32px 64px -24px rgba(16,24,40,.14);
  --d-ease: cubic-bezier(.2,.8,.2,1);

  /* Legacy aliases — REQUIRED. MY_SCORE_CONTENT, PROJECTOR_CONTENT and
     the challenge modal's assets/view.html still read the warm-paper
     names, and so can any Page an organiser wrote in CTFd's admin UI,
     which lives in the database where we cannot grep; re-pointing them
     here is what lets the palette change without touching those call
     sites, and the dark swap below reaches them for free. --d-brand-dark
     reads as "the interactive colour" wherever it survives, and in B
     that role belongs to the blue accent, not to a darker amber. */
  --d-paper:       var(--d-bg);
  --d-paper-soft:  var(--d-surface);
  --d-paper-sunk:  var(--d-surface-2);
  --d-ink-mid:     var(--d-text-2);
  --d-ink-light:   var(--d-text-3);
  --d-ink-soft:    var(--d-text-3);
  --d-hair:        var(--d-border);
  --d-hair-strong: var(--d-border-strong);
  --d-brand-dark:  var(--d-accent);
  --d-brand-ink:   var(--d-warn-text);
  --d-brand-soft:  var(--d-warn-soft);
  --d-brand-line:  var(--d-warn-line);
  --d-pass:        var(--d-ok);
  --d-pass-soft:   var(--d-ok-soft);
  --d-pass-line:   var(--d-ok-line);
  --d-fail:        var(--d-bad);
  --d-fail-soft:   var(--d-bad-soft);
  --d-fail-line:   var(--d-bad-line);

  /* Type families (3 names, 2 real stacks — B does not split the Latin
     and Korean faces). Pretendard leads Noto Sans KR everywhere; the
     mono stack carries the same Korean fallback so 통과 / 실패 inside the
     testbench render in a designed face instead of dropping to whatever
     Hangul the OS pairs with Consolas. */
  --d-f-sans: 'Inter', 'Pretendard Variable', 'Noto Sans KR', system-ui, -apple-system, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif;
  --d-f-ko:   var(--d-f-sans);
  --d-f-mono: 'Geist Mono', ui-monospace, SFMono-Regular, Consolas, 'Pretendard Variable', 'Noto Sans KR', monospace;

  /* Spacing */
  --d-s-1:  4px;  --d-s-2:  8px;  --d-s-3: 12px;
  --d-s-4: 16px;  --d-s-5: 20px;  --d-s-6: 24px;
  --d-s-7: 32px;  --d-s-8: 48px;  --d-s-9: 64px;

  /* Radius — B's 6 / 8 / 10 / 14 scale. --d-r-pill keeps its 999px value
     rather than being retargeted: CTFd Pages authored through the admin
     UI can reference it and those live in the database where we cannot
     grep. Nothing we ship uses it any more, and no button does. */
  --d-r-sm:   6px;
  --d-r-md:   8px;
  --d-r-lg:  10px;
  --d-r-xl:  14px;
  --d-r-pill: 999px;

  /* CTFd legacy aliases — templates that read --theme-color, --sens-* keep
     working, and keep meaning the amber they were authored against. */
  --theme-color:    var(--d-brand);
  --sens-brand:     var(--d-brand);
  --sens-brand-dark:var(--d-brand-ink);
  --sens-brand-ink: var(--d-brand-ink);
  --sens-brand-soft:var(--d-brand-soft);

  /* Bootstrap 5.3 resolves its own chrome — buttons, links, tables, form
     controls, modals — through these. Leave them alone and CTFd paints a
     second, brighter blue right next to our accent. */
  --bs-primary: #2457e0;
  --bs-primary-rgb: 36, 87, 224;
  --bs-link-color: var(--d-accent-text);
  --bs-link-color-rgb: 30, 71, 184;
  --bs-link-hover-color: var(--d-accent-hover);
  --bs-border-color: var(--d-border);
  --bs-border-color-translucent: var(--d-border);
  --bs-border-radius: 6px;
  --bs-border-radius-sm: 4px;
  --bs-border-radius-lg: 10px;
  --bs-body-bg: var(--d-bg);
  --bs-body-color: var(--d-ink);
  --bs-body-font-family: var(--d-f-sans);
  --bs-body-font-size: 14px;
  --bs-emphasis-color: var(--d-ink);
  --bs-secondary-color: var(--d-text-2);
  --bs-focus-ring-color: var(--d-focus);
  --bs-focus-ring-width: 3px;
}

/* ── Base ────────────────────────────────────────────────────────── */

body {
  background: var(--d-bg);
  color: var(--d-ink);
  font-family: var(--d-f-sans);
  font-size: 14px;
  line-height: 1.5;
  /* Korean wants to break between phrases, but a Digital filename or an
     underscore-joined problem title offers no phrase boundary — let those
     break mid-token instead of shoving a card off the grid. */
  word-break: keep-all;
  overflow-wrap: anywhere;
  /* 'cv05'/'cv11' are Inter's single-storey l and open-tail g, which is
     what makes B's captions read as a product rather than a document.
     'tnum' stays on globally, unlike the specimen: MY_SCORE_CONTENT and
     PROJECTOR_CONTENT are frozen for this pass and their score columns
     line up only because the whole page has tabular figures. */
  font-feature-settings: 'cv05', 'cv11', 'tnum';
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-rendering: optimizeLegibility;
}

/* Links inherit rather than claim the accent — in B the blue is reserved
   for the one action on the screen, and a page full of blue words is
   exactly what that reservation is protecting against. */
a { color: inherit; text-decoration: none; }
a:hover { color: var(--d-accent-hover); text-decoration: none; }

/* ── CTFd navbar / jumbotron / buttons overrides ─────────────────── */

/* B's bar is a 56px sheet of translucent near-white over a blurred page,
   held down by a single hairline. --d-navbar carries the alpha, so the
   blur has something to reveal. */
.navbar,
.navbar.navbar-dark,
.navbar.bg-dark {
  --bs-navbar-padding-y: 0;
  min-height: 56px;
  background-color: var(--d-navbar) !important;
  background-image: none !important;
  border-bottom: 1px solid var(--d-border);
  box-shadow: none;
  -webkit-backdrop-filter: blur(8px);
  backdrop-filter: blur(8px);
}
/* CTFd's stock navbar carries Bootstrap classes `navbar-dark bg-dark`,
   which paint .navbar-brand + .nav-link white on the assumption of a
   dark background. We flip the bg to near-white above, so the text
   needs explicit overrides — otherwise the brand wordmark and link text
   render in white and become invisible. */
.navbar .navbar-brand,
.navbar.navbar-dark .navbar-brand {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  color: var(--d-ink) !important;
  font-family: var(--d-f-sans);
  font-weight: 600;
  letter-spacing: -0.005em;
  font-size: 15px;
}
/* The one amber in the chrome. It is a pseudo-element so that CTFd's own
   brand slot — the ctf_name text or an uploaded logo — stays whatever the
   organiser configured. */
.navbar .navbar-brand::before {
  content: '';
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--d-brand);
}
.navbar .navbar-brand:hover,
.navbar.navbar-dark .navbar-brand:hover {
  color: var(--d-ink) !important;
}
.navbar .nav-link,
.navbar.navbar-dark .nav-link {
  color: var(--d-text-2) !important;
  font-family: var(--d-f-sans);
  font-weight: 500;
  font-size: 14px;
  letter-spacing: 0;
  padding: 6px 10px !important;
  border-bottom: 0 !important;
  border-radius: var(--d-r-sm);
  transition: background 0.15s var(--d-ease), color 0.15s var(--d-ease);
}
.navbar .nav-link:hover,
.navbar.navbar-dark .nav-link:hover {
  color: var(--d-ink) !important;
  background: var(--d-surface-2);
}
/* The current page is marked with an offset accent underline rather than
   a border on the box: the box already uses its background for hover, and
   a border would fight the 6px corner. */
.navbar .nav-link.active,
.navbar.navbar-dark .nav-link.active,
.navbar .nav-item.active > .nav-link {
  color: var(--d-ink) !important;
  background: transparent !important;
  text-decoration: underline;
  text-decoration-color: var(--d-accent);
  text-decoration-thickness: 2px;
  text-underline-offset: 7px;
}
.navbar .navbar-toggler,
.navbar.navbar-dark .navbar-toggler {
  border-color: var(--d-border-strong) !important;
  border-radius: var(--d-r-sm);
  color: var(--d-ink) !important;
  padding: 4px 9px;
}
.navbar.navbar-dark .navbar-toggler-icon {
  /* Bootstrap renders the hamburger as a white SVG background-image;
     invert it so it shows on the near-white bar. */
  filter: invert(1) brightness(0.35);
}
.navbar .nav-link:focus-visible,
.navbar .navbar-brand:focus-visible,
.navbar .navbar-toggler:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--d-focus);
  border-radius: var(--d-r-sm);
}
/* CTFd's light/dark toggle is an icon inside a nav-link; give it the
   square footprint B uses for icon-only actions so it stops reading as a
   word that lost its letters. */
.navbar #color-mode-switcher {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 32px;
}

.jumbotron {
  background-color: var(--d-surface) !important;
  border-bottom: 1px solid var(--d-border);
}

/* One filled button per screen, and it is blue. Everything else is a
   hairline ghost on white. Geometry goes through Bootstrap's own --bs-btn-*
   variables so that .btn-sm and .btn-lg still mean something; the colours
   stay on !important because CTFd's compiled theme sets some of them as
   plain declarations and would otherwise win the cascade. */
.btn {
  --bs-btn-padding-y: 7px;
  --bs-btn-padding-x: 14px;
  --bs-btn-font-family: var(--d-f-sans);
  --bs-btn-font-size: 14px;
  --bs-btn-font-weight: 500;
  --bs-btn-border-radius: var(--d-r-sm);
  --bs-btn-focus-box-shadow: 0 0 0 3px var(--d-focus);
  letter-spacing: 0;
  transition: background 0.15s var(--d-ease), border-color 0.15s var(--d-ease),
              color 0.15s var(--d-ease), box-shadow 0.15s var(--d-ease);
}
.btn-sm { --bs-btn-padding-y: 4px; --bs-btn-padding-x: 12px; --bs-btn-font-size: 13px; }
.btn-lg { --bs-btn-padding-y: 11px; --bs-btn-padding-x: 20px; --bs-btn-font-size: 14.5px; }
/* The submit rules carry the type and the attribute, which out-specifies
   the plain .btn-outline-secondary class below however late that rule
   comes — so a secondary submit anywhere in CTFd would render as the one
   filled blue action. Excusing the two secondary classes here is what
   keeps "one filled button per screen" true off the mentee screens. */
.btn-primary,
button[type="submit"]:not(.btn-outline-secondary):not(.btn-secondary) {
  background-color: var(--d-accent) !important;
  border-color: var(--d-accent) !important;
  color: var(--d-on-accent) !important;
  box-shadow: 0 1px 2px rgba(16,24,40,.12);
}
.btn-primary:hover,
.btn-primary:focus,
.btn-primary:active,
button[type="submit"]:not(.btn-outline-secondary):not(.btn-secondary):hover,
button[type="submit"]:not(.btn-outline-secondary):not(.btn-secondary):focus,
button[type="submit"]:not(.btn-outline-secondary):not(.btn-secondary):active {
  background-color: var(--d-accent-hover) !important;
  border-color: var(--d-accent-hover) !important;
  color: var(--d-on-accent) !important;
}
/* Some CTFd forms submit through a bare button that never gets .btn, so
   it inherits the UA's outset border and 2px corner. Spell the shape out
   or those buttons alone keep the old chrome. */
button[type="submit"]:not(.btn) {
  padding: 7px 14px;
  border: 1px solid var(--d-accent);
  border-radius: var(--d-r-sm);
  font-family: var(--d-f-sans);
  font-size: 14px;
  font-weight: 500;
}
/* The colour rules above are !important, so the disabled state has to be
   too or a dead button keeps advertising itself as the live one. The grey
   is --d-text-2 rather than the lighter --d-text-3, because the fill here
   is --d-surface-2 — the one ground --d-text-3 is not allowed to sit on.
   The two :not() clauses are carried over from the live rule because each
   of them adds specificity: without them the blue outranks the dead
   state and every disabled submit still looks clickable. */
.btn-primary:disabled,
.btn-primary.disabled,
button[type="submit"]:not(.btn-outline-secondary):not(.btn-secondary):disabled {
  background-color: var(--d-surface-2) !important;
  border-color: var(--d-border) !important;
  color: var(--d-text-2) !important;
  box-shadow: none;
  opacity: 1;
}

.btn-outline-secondary,
.btn-secondary {
  background: var(--d-surface) !important;
  color: var(--d-ink) !important;
  border-color: var(--d-border-strong) !important;
  box-shadow: var(--d-shadow-1);
}
.btn-outline-secondary:hover,
.btn-secondary:hover {
  background: var(--d-surface-2) !important;
  border-color: var(--d-border-strong) !important;
  color: var(--d-ink) !important;
}
.btn:focus-visible,
button:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--d-focus) !important;
}

/* ── Form controls ───────────────────────────────────────────────── */

/* Bootstrap's focus shadow is a hard-coded washed blue that does not
   answer to --bs-primary, so it is replaced outright by the 3px --d-focus
   ring. 44px is B's input height; textareas keep growing. */
.form-control,
.form-select {
  border-color: var(--d-border-strong);
  border-radius: var(--d-r-sm);
  background-color: var(--d-surface);
  color: var(--d-ink);
  font-family: var(--d-f-sans);
  font-size: 14px;
  box-shadow: var(--d-shadow-1);
  transition: border-color 0.15s var(--d-ease), box-shadow 0.15s var(--d-ease);
}
input.form-control,
.form-select { height: 44px; padding: 0 12px; }
input.form-control-sm,
.form-select-sm { height: 34px; font-size: 13px; }
textarea.form-control { min-height: 96px; padding: 10px 12px; line-height: 1.6; }
.form-control::placeholder { color: var(--d-text-3); opacity: 1; }
.form-control:focus,
.form-select:focus {
  border-color: var(--d-accent);
  background-color: var(--d-surface);
  color: var(--d-ink);
  box-shadow: 0 0 0 3px var(--d-focus);
}
.form-check-input:checked { background-color: var(--d-accent); border-color: var(--d-accent); }
.form-check-input:focus { border-color: var(--d-accent); box-shadow: 0 0 0 3px var(--d-focus); }
.form-label { font-size: 13px; font-weight: 500; color: var(--d-ink); }
.form-text { color: var(--d-text-2); }
.input-group-text {
  background: var(--d-surface-2);
  border-color: var(--d-border-strong);
  color: var(--d-text-2);
}

/* --bs-body-bg is the page ground now, so the Bootstrap surfaces that
   default to it would come out grey; they are cards and want white. */
.card,
.modal-content,
.dropdown-menu,
.list-group-item {
  background-color: var(--d-surface);
  border-color: var(--d-border);
  color: var(--d-ink);
}

/* Korean-only camp — hide CTFd language switcher (Chrome 105+, Safari 15.4+, FF 121+) */
.navbar li.nav-item:has(form[x-data="LanguageForm"]) {
  display: none !important;
}

/* ── Dark mode ────────────────────────────────────────────────────
 *
 * CTFd's color_mode_switcher.js sets `data-bs-theme="dark"` on the root
 * element (Bootstrap 5.3 convention) when the navbar sun/moon button is
 * clicked or when prefers-color-scheme: dark is detected. Only the token
 * values change here — every surface that reads var(--d-*) follows, and
 * so do the legacy aliases above, because they are declared as var()
 * references rather than as copies of the light hexes.
 *
 * The old build needed a second block of element-level overrides for
 * anything that painted --d-ink as a BACKGROUND. B has no such element:
 * the filled button is --d-accent on --d-on-accent in both modes, and
 * both sides of that pair swap together. The overrides are gone with it.
 */

[data-bs-theme="dark"] {
  --d-bg:            #0f1115;
  --d-surface:       #171a20;
  --d-surface-2:     #1f232b;
  --d-console:       #0f1115;
  --d-border:        #2a2f38;
  --d-border-strong: #3a404b;

  --d-ink:           #eceef2;
  --d-text-2:        #a3aab6;
  --d-text-3:        #8a91a1;

  --d-accent:        #6f92ff;
  --d-accent-hover:  #86a4ff;
  --d-accent-soft:   #1a2440;
  --d-accent-text:   #9db4ff;
  --d-on-accent:     #0f1115;

  --d-ok:            #34c98a;
  --d-ok-soft:       #10281f;
  --d-ok-text:       #5fd9a4;
  --d-ok-line:       #2b5045;
  --d-on-ok:         #0f1115;

  /* On a dark ground amber finally clears AA, so the disc colour and the
     bar colour collapse back into one value. */
  --d-warn:          #f5a83d;
  --d-warn-fill:     #f5a83d;
  --d-warn-soft:     #2c2214;
  --d-warn-text:     #f7be6b;
  --d-warn-line:     #5a4b37;
  --d-on-warn:       #0f1115;

  --d-bad:           #f0625d;
  --d-bad-soft:      #2e1717;
  --d-bad-text:      #ff8a85;
  --d-bad-line:      #58393c;
  --d-on-bad:        #0f1115;

  --d-notice-soft:   #1f232b;
  --d-notice-text:   #a3aab6;

  --d-focus:         rgba(111,146,255,.45);
  --d-navbar:        rgba(15,17,21,.8);

  --d-shadow-1: 0 0 0 1px rgba(255,255,255,.04), 0 1px 2px rgba(0,0,0,.4);
  --d-shadow-2: 0 0 0 1px rgba(255,255,255,.05), 0 8px 24px -8px rgba(0,0,0,.6);
  --d-shadow-3: 0 0 0 1px rgba(255,255,255,.06), 0 24px 48px -16px rgba(0,0,0,.7);

  --bs-primary: #6f92ff;
  --bs-primary-rgb: 111, 146, 255;
  --bs-link-color-rgb: 157, 180, 255;
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
  padding: 11px 16px !important;
  background: var(--d-accent) !important;
  border-color: var(--d-accent) !important;
  color: var(--d-on-accent) !important;
  font-weight: 500 !important;
  border-radius: var(--d-r-sm) !important;
}
.submit-row > .key-submit .challenge-submit:hover {
  background: var(--d-accent-hover) !important;
  border-color: var(--d-accent-hover) !important;
}

/* ── Shared component classes (used by Pages markup + plugin assets) ── */

.d-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 36px;
  padding: 0 14px;
  border: 1px solid var(--d-border-strong);
  border-radius: var(--d-r-sm);
  background: var(--d-surface);
  color: var(--d-ink);
  box-shadow: var(--d-shadow-1);
  font-family: var(--d-f-sans);
  font-weight: 500;
  font-size: 14px;
  letter-spacing: 0;
  text-decoration: none;
  white-space: nowrap;
  cursor: pointer;
  transition: background 0.15s var(--d-ease), color 0.15s var(--d-ease),
              border-color 0.15s var(--d-ease), box-shadow 0.15s var(--d-ease);
}
.d-btn:hover { background: var(--d-surface-2); color: var(--d-ink); text-decoration: none; }
.d-btn:focus-visible { outline: none; box-shadow: 0 0 0 3px var(--d-focus); }
.d-btn:active { transform: translateY(1px); box-shadow: none; }
/* 44px for the one button that carries a whole page — a login submit or
   a landing CTA — matching the input height it sits under. */
.d-btn-lg { height: 44px; padding: 0 20px; font-size: 14.5px; }
.d-btn-primary {
  background: var(--d-accent);
  border-color: var(--d-accent);
  color: var(--d-on-accent);
  box-shadow: 0 1px 2px rgba(16,24,40,.12);
}
.d-btn-primary:hover { background: var(--d-accent-hover); border-color: var(--d-accent-hover); color: var(--d-on-accent); text-decoration: none; }
.d-btn-ghost {
  background: var(--d-surface);
  color: var(--d-ink);
  border-color: var(--d-border-strong);
}
.d-btn-ghost:hover { background: var(--d-surface-2); border-color: var(--d-border-strong); color: var(--d-ink); text-decoration: none; }
/* The quiet third tier: reads as a link, keeps a button's hit area. */
.d-btn-text {
  background: transparent;
  border-color: transparent;
  box-shadow: none;
  color: var(--d-accent-text);
  padding: 0 6px;
}
.d-btn-text:hover { background: transparent; color: var(--d-accent-hover); text-decoration: underline; text-underline-offset: 3px; }

.d-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 24px;
  padding: 0 8px;
  border: 1px solid transparent;
  border-radius: var(--d-r-sm);
  background: var(--d-surface-2);
  color: var(--d-text-2);
  font-family: var(--d-f-sans);
  font-size: 12.5px;
  font-weight: 500;
  letter-spacing: 0;
  white-space: nowrap;
}
.d-pill-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  flex-shrink: 0;
}
.d-pill-pass   { background: var(--d-ok-soft);   color: var(--d-ok-text);   border-color: var(--d-ok-line); }
.d-pill-warn   { background: var(--d-warn-soft); color: var(--d-warn-text); border-color: var(--d-warn-line); }
.d-pill-fail   { background: var(--d-bad-soft);  color: var(--d-bad-text);  border-color: var(--d-bad-line); }
/* Locked takes --d-text-2, not the lighter --d-text-3: on --d-surface-2
   that rung drops under 4.5:1, and a locked row still has to be read. */
.d-pill-locked { background: var(--d-surface-2); color: var(--d-text-2);   border-color: var(--d-border-strong); }
/* "Brand" here means the amber family, which on light can only appear as
   a wash behind --d-warn-text, never as the text itself. */
.d-pill-brand  { background: var(--d-warn-soft); color: var(--d-warn-text); border-color: var(--d-warn-line); }

.d-tag {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 7px;
  border-radius: var(--d-r-sm);
  background: var(--d-surface-2);
  color: var(--d-text-2);
  font-family: var(--d-f-mono);
  font-size: 11.5px;
  font-weight: 500;
  letter-spacing: 0;
  white-space: nowrap;
}
.d-tag-mission { background: var(--d-warn-soft); color: var(--d-warn-text); }
.d-tag-p1      { background: var(--d-ok-soft);   color: var(--d-ok-text); }
.d-tag-p2      { background: var(--d-bad-soft);  color: var(--d-bad-text); }

/* The amber dot is the brand mark, so it stays; the word beside it moves
   to --d-text-2, because amber lettering fails AA on a near-white page. */
.d-livedot {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-family: var(--d-f-sans);
  font-size: 12.5px;
  letter-spacing: 0;
  color: var(--d-text-2);
  font-weight: 500;
}
.d-livedot::before {
  content: '';
  width: 7px; height: 7px;
  background: var(--d-brand);
  border-radius: 50%;
  box-shadow: 0 0 0 4px rgba(245,168,61,0.18);
  animation: d-pulse 2s ease infinite;
}
@keyframes d-pulse {
  0%, 100% { box-shadow: 0 0 0 4px rgba(245,168,61,0.18); }
  50%      { box-shadow: 0 0 0 7px rgba(245,168,61,0.04); }
}

.d-meta {
  font-family: var(--d-f-sans);
  font-size: 12.5px;
  font-weight: 500;
  letter-spacing: 0;
  color: var(--d-text-2);
}
.d-tiny {
  font-family: var(--d-f-sans);
  font-size: 12px;
  font-weight: 400;
  letter-spacing: 0;
  color: var(--d-text-3);
}
.d-code {
  font-family: var(--d-f-mono);
  font-size: 0.92em;
  background: var(--d-surface-2);
  border: 1px solid var(--d-border);
  padding: 1px 5px;
  border-radius: 4px;
  color: var(--d-ink);
  word-break: break-all;
}
.d-rule { height: 1px; background: var(--d-border); border: none; margin: var(--d-s-7) 0; }

/* ── Round countdown ─────────────────────────────────────────────
 *
 * round-ui.js builds this widget inside the navbar and injects its own
 * layout CSS at runtime, so these rules are keyed on the id: an id beats
 * a class no matter which style element the browser saw last, and the
 * two files stay out of each other's way. Placement stays round-ui.js's
 * business — only the skin and the digits are ours. .is-warn (5분 이하)
 * and .is-crit (1분 이하) are set by round-ui.js.
 */

#econ-round-countdown {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  height: 38px;
  padding: 0 10px 0 12px;
  border: 1px solid var(--d-border);
  border-radius: var(--d-r-sm);
  background: var(--d-surface);
  color: var(--d-ink);
  box-shadow: var(--d-shadow-1);
  transition: background 0.22s var(--d-ease), color 0.22s var(--d-ease),
              border-color 0.22s var(--d-ease);
}
/* round-ui.js hides the pill between phases with the hidden attribute, but
   both it and the rule above declare a display, and any author display
   beats the UA's [hidden] rule — so the clock has to be switched off here
   or a stale time sits in the navbar with nothing counting it down. */
#econ-round-countdown[hidden] { display: none; }
#econ-round-countdown .econ-round-countdown-label {
  flex: 0 1 auto;
  font-family: var(--d-f-sans);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  color: var(--d-text-2);
  white-space: nowrap;
}
/* Tabular figures plus a fixed 8ch box, so 00:42:17 and 00:09:08 occupy
   the same width and the bar never twitches on the tick. round-ui.js sets
   this element's type with the `font` shorthand, which resets
   font-variant-numeric — hence restating it at higher specificity. */
#econ-round-countdown .econ-round-countdown-time {
  font-family: var(--d-f-sans);
  font-size: 18px;
  font-weight: 600;
  line-height: 1;
  letter-spacing: 0.01em;
  color: inherit;
  font-variant-numeric: tabular-nums;
  min-width: 8ch;
  text-align: right;
  white-space: nowrap;
}
#econ-round-countdown.is-warn {
  background: var(--d-warn-soft);
  border-color: var(--d-warn-line);
  color: var(--d-warn-text);
}
#econ-round-countdown.is-crit {
  background: var(--d-bad-soft);
  border-color: var(--d-bad);
  color: var(--d-bad-text);
}
#econ-round-countdown.is-warn .econ-round-countdown-label,
#econ-round-countdown.is-crit .econ-round-countdown-label { color: inherit; }
/* Weight, not just hue, so the last minute is legible from the back of
   the room and to anyone who cannot separate the two washes. */
#econ-round-countdown.is-crit .econ-round-countdown-time { font-weight: 700; }
/* Contest over: the clock becomes a record, not an alarm. */
#econ-round-countdown[data-phase="finished"] {
  min-width: 0;
  justify-content: center;
  background: var(--d-surface-2);
  border-color: var(--d-border);
  color: var(--d-text-2);
  box-shadow: none;
}
@media (max-width: 760px) {
  /* Phone: the label is the part that can go; the digits are the part
     the room is watching. */
  #econ-round-countdown { height: 32px; padding: 0 8px; gap: 6px; }
  #econ-round-countdown .econ-round-countdown-label { font-size: 11px; }
  #econ-round-countdown .econ-round-countdown-time { font-size: 15px; min-width: 7ch; }
}
@media (max-width: 400px) {
  /* Below 560px round-ui.js stops centring the countdown and tucks it in
     beside the toggler, which clears the brand down to about 390px. On a
     360px phone the two would still touch by a couple of pixels, so the
     brand gives up one step of size rather than truncating: measured at
     360px the brand now ends at 170 and the pill starts at 190. */
  .navbar .navbar-brand,
  .navbar.navbar-dark .navbar-brand { font-size: 13.5px; gap: 7px; }
}

/* Colour and opacity still carry every state; only movement stops. */
@media (prefers-reduced-motion: reduce) {
  .d-livedot::before { animation: none; }
  .d-btn, .btn, .navbar .nav-link, #econ-round-countdown,
  .form-control, .form-select { transition: none; }
  .d-btn:active, .btn:active { transform: none; }
}
</style>
<script defer src="/plugins/econ_judge/assets/scoreboard.js"></script>
<script defer src="/plugins/econ_judge/assets/challenges.js"></script>
<script defer src="/plugins/econ_judge/assets/round-ui.js"></script>
<script defer src="/plugins/econ_judge/assets/landing.js"></script>
<script>
/* Inject a "내 점수" navbar link before the Challenges link, and give that
   link the name the plugin and the front page use for the same place
   (도전 과제; CTFd's own translation says 챌린지). CTFd's navbar hides the
   Scoreboard link from everyone once score_visibility=admins, admins
   included (they reach /scoreboard by URL or from the admin panel), so this
   gives mentees a clear destination for their personal progress view. */
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
    chalLink.textContent = '도전 과제';
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

# HTML index page (CTFd Pages.format = "html"): landing candidate F, "Live
# status, finished". CTFd serves the .stage with no attributes. landing.js
# (loaded from THEME_HEADER_CSS) writes data-auth on it from window.init at
# once, then data-phase and data-tail from /api/v1/digital/competition
# (round-ui.js's shared poll), along with the clock digits and classes, the
# ruler's --el / --ticks / --major, the text slots and the one-line phase
# announcement in #econ-landing-status. The attribute-free first paint is a
# designed state (연결 중, --:--, and 로그인 for a signed-out visitor; a
# signed-in one gets no action) and is what a visitor without JavaScript
# keeps. The status band breaks out of CTFd's container to the
# window's edges, so this page's own style block also sets
# main { overflow-x: clip } — it only exists on /, so no other page is
# affected. A raw string, so a CSS escape reaches the browser as written.
INDEX_CONTENT = r"""<style>
/* E-CON 논설 front page. Every colour comes from a THEME_HEADER_CSS token,
   so CTFd's dark toggle reaches this page with nothing declared here; the
   one literal is the filled button's 1px shadow, the same value
   THEME_HEADER_CSS gives .btn-primary. Bootstrap already owns .btn, .card
   and .mark, and CTFd's core CSS styles them with state rules that outrank
   a scoped selector (a pressed .btn flashes transparent), so this page's
   are .lbtn, .panel and .wordmark. No JavaScript in this file. */

/* ── the page's frame inside CTFd ─────────────────────────────────── */
/* The band breaks out of main > .container to the window's edges. 100vw
   counts the scrollbar, so the band is wider than the page by that much.
   The guard against sideways scroll sits on main: main spans the window
   and sits outside the .container the band breaks out of, and unlike
   body's, its overflow is not handed to the viewport, so what it clips
   leaves nothing to pan, by script or by hand. Never clip .econ-landing
   or .container: clipping the box the band breaks out of cancels the
   breakout. This style element only exists on /, so it reaches no other
   page. */
main { overflow-x: clip; }
/* CTFd pads main for a fixed navbar, but round-ui.js puts the navbar back
   in flow on every page, which leaves a navbar-high gap above the content.
   The band sits flush under the bar instead, with or without JavaScript. */
body:has(#econ-landing) .navbar.fixed-top { position: relative; }
main:has(#econ-landing) { padding-top: 0; }
/* The hero clock replaces the navbar pill, but only once landing.js has
   written a phase: until then, or if it never runs, the pill stays. */
body:has(#econ-landing .stage[data-phase]) #econ-round-countdown { display: none !important; }

/* ── root & primitives ────────────────────────────────────────────── */
.econ-landing {
  /* .wrap carries the page gutter, so the container's own goes */
  margin-inline: calc(var(--bs-gutter-x, 1.5rem) * -.5);
  color: var(--d-ink);
  font-family: var(--d-f-sans); font-size: 14px; line-height: 1.5;
  word-break: keep-all; overflow-wrap: anywhere;
  -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
  /* no global 'tnum' here: figures that tick or line up opt in below */
  font-feature-settings: 'cv05', 'cv11';
}
.econ-landing *, .econ-landing *::before, .econ-landing *::after { box-sizing: border-box; }
/* Bootstrap's reboot and CTFd's core theme both style bare elements (CTFd
   letter-spaces h1 and h2 by 2px), so each one used here is reset first and
   decided by its own rule below. */
.econ-landing h1, .econ-landing h2, .econ-landing h3, .econ-landing p, .econ-landing ol,
.econ-landing ul, .econ-landing dl, .econ-landing dd, .econ-landing table { margin: 0; padding: 0; }
.econ-landing h1, .econ-landing h2, .econ-landing h3 { font-size: inherit; font-weight: 600; line-height: inherit; letter-spacing: 0; color: var(--d-ink); }
.econ-landing li { list-style: none; }
.econ-landing a { color: inherit; text-decoration: none; }
.econ-landing b { font-weight: 600; }
/* slashed-zero only changes the fallback faces: Geist Mono's zero is
   slashed already */
.econ-landing .mono { font-family: var(--d-f-mono); font-variant-numeric: tabular-nums slashed-zero; }
.econ-landing code { font-family: var(--d-f-mono); font-size: 12.5px; background: var(--d-surface-2); border: 1px solid var(--d-border); border-radius: 4px; padding: 1px 5px; color: var(--d-ink); }
.econ-landing svg.ic { width: 16px; height: 16px; flex: none; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.econ-landing .grow { flex: 1; }
/* one unit that must not break across lines (N / M, 7-segment) */
.econ-landing .nw { white-space: nowrap; }
/* read out, never drawn: the phase announcement landing.js writes */
.econ-landing .vh { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0; border: 0; overflow: hidden; clip: rect(0 0 0 0); clip-path: inset(50%); white-space: nowrap; }
.econ-landing .stage { container-type: inline-size; display: block; }
/* The column lines up with CTFd's navbar: the container's width and the
   container's own 12px gutter (phones take 16px, below). */
.econ-landing .wrap { padding-inline: calc(var(--bs-gutter-x, 1.5rem) * .5); }

/* ── chips & buttons (the theme's 6px chip and button, as .lbtn) ──── */
.econ-landing .chip { display: inline-flex; align-items: center; gap: 6px; height: 26px; padding: 0 10px; border-radius: 6px; font-size: 13px; font-weight: 500; background: var(--d-surface-2); color: var(--d-text-2); white-space: nowrap; }
.econ-landing .chip svg.ic { width: 13px; height: 13px; stroke-width: 2.4; }
.econ-landing .chip.accent { background: var(--d-accent-soft); color: var(--d-accent-text); }
.econ-landing .chip.ok { background: var(--d-ok-soft); color: var(--d-ok-text); }
.econ-landing .chip.warn { background: var(--d-warn-soft); color: var(--d-warn-text); }
.econ-landing .chip.bad { background: var(--d-bad-soft); color: var(--d-bad-text); }
.econ-landing .chip.mono { font-family: var(--d-f-mono); font-size: 12px; }
.econ-landing .chip i.live { position: relative; width: 7px; height: 7px; border-radius: 50%; background: currentColor; flex: none; }
.econ-landing .chip i.live::after { content: ""; position: absolute; inset: -3px; border-radius: 50%; border: 1.5px solid currentColor; opacity: 0; animation: lp-pulse 2.2s var(--d-ease) infinite; }
.econ-landing .lbtn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; height: 36px; margin: 0; padding: 0 14px; border-radius: 6px; font-family: inherit; font-size: 14px; font-weight: 500; line-height: 1.5; letter-spacing: 0; border: 1px solid var(--d-border-strong); background: var(--d-surface); color: var(--d-ink); box-shadow: var(--d-shadow-1); cursor: pointer; white-space: nowrap; transition: background .15s var(--d-ease), border-color .15s var(--d-ease), box-shadow .15s var(--d-ease); }
.econ-landing .lbtn:hover { background: var(--d-surface-2); }
.econ-landing .lbtn.primary { background: var(--d-accent); border-color: var(--d-accent); color: var(--d-on-accent); box-shadow: 0 1px 2px rgba(16,24,40,.12); }
.econ-landing .lbtn.primary:hover { background: var(--d-accent-hover); border-color: var(--d-accent-hover); }
.econ-landing .lbtn.xl { height: 52px; padding: 0 26px; font-size: 16px; font-weight: 600; border-radius: 8px; letter-spacing: -.005em; }
.econ-landing .lbtn.wide { width: 100%; }
.econ-landing .lbtn.sm { height: 32px; font-size: 13px; padding: 0 12px; }
.econ-landing .lbtn:active { transform: translateY(1px); box-shadow: none; }
/* Windows High Contrast drops a box-shadow ring. The transparent
   outline beside it is invisible everywhere else and is drawn there in
   the system's colour instead; the toy's rings below do the same. */
.econ-landing .lbtn:focus-visible { outline: 2px solid transparent; outline-offset: 2px; border-color: var(--d-accent); box-shadow: 0 0 0 3px var(--d-focus); }
/* On a filled button the halo is too faint to see (under 3:1 in both
   themes), so the one blue action draws a solid accent ring outside a
   surface-coloured gap instead. */
.econ-landing .lbtn.primary:focus-visible { box-shadow: 0 0 0 2px var(--d-surface), 0 0 0 4px var(--d-accent); }
.econ-landing ::selection { background: var(--d-accent-soft); color: var(--d-ink); }

/* ── phase × auth: the whole state machine, in CSS ────────────────── */
/* landing.js writes data-phase only with one of competition.py's seven
   phases and removes it otherwise, so a stage WITHOUT the attribute is the
   pending state: the first paint every visitor gets, and what a visitor
   without JavaScript keeps. Every phase-bound element is a .ph variant
   named after its phase (.ph.pending when there is no attribute), and .au
   is the action for one auth state. What each state shows:

   phase          chip / lede / note under the clock   clock        signed in       signed out
   (none)         연결 중 / 불러오는 중 / none          --:-- dim    no action       로그인
   before         시작 전 / headline and toy / none     counts       waiting note    로그인
   round1         1라운드 / 진행 중 / the ruler         alarms       도전 과제로      로그인
   break          휴식 시간 / 2라운드 안내 / note        counts       내 점수 보기     로그인
   round2         2라운드 / 진행 중 / the ruler         alarms       도전 과제로      로그인
   finished       종료 / 끝났습니다 / none              00:00 done   내 점수 보기     로그인
   open           준비 모드 / 운영 검토 / note           --:-- dim    도전 과제로      로그인
   misconfigured  설정 필요 / 설정 오류 / red note       --:-- dim    no-action note  로그인

   "counts" never changes colour: a start is not a deadline. "alarms"
   turns amber with 5 minutes left and red with 1 (.is-warn / .is-crit),
   and data-tail swaps the chip and the lede for their 곧 마감 twins. In
   before, the toy takes the action column's place and the action moves
   under the clock. */
.econ-landing .ph, .econ-landing .au { display: none; }
.econ-landing .stage:not([data-phase]) .ph.pending,
.econ-landing [data-phase="before"]   .ph.before,
.econ-landing [data-phase="round1"]   .ph.round1,
.econ-landing [data-phase="break"]    .ph.break,
.econ-landing [data-phase="round2"]   .ph.round2,
.econ-landing [data-phase="finished"] .ph.finished,
.econ-landing [data-phase="open"]     .ph.open,
.econ-landing [data-phase="misconfigured"] .ph.misconfigured { display: var(--ph, inline-flex); }
.econ-landing .clabel .ph { --ph: inline; }
.econ-landing .lede.ph, .econ-landing .rnote.ph { --ph: block; }
.econ-landing .rlabels .ph, .econ-landing .strip .ph { --ph: inline; }
/* One action, chosen by phase × auth. Signed out — or before data-auth is
   written — it is 로그인. A signed-in team is only sent to 도전 과제 in a
   phase where competition.py shows challenges; before a phase is known it
   gets no action at all, and in 시작 전 / 일정 설정 필요 it gets a sentence. */
.econ-landing [data-auth="out"] .au.out,
.econ-landing .stage:not([data-auth]) .au.out,
.econ-landing [data-auth="in"][data-phase="round1"]   .au.go,
.econ-landing [data-auth="in"][data-phase="round2"]   .au.go,
.econ-landing [data-auth="in"][data-phase="open"]     .au.go,
.econ-landing [data-auth="in"][data-phase="break"]    .au.score,
.econ-landing [data-auth="in"][data-phase="finished"] .au.score,
.econ-landing [data-auth="in"][data-phase="before"]   .au.wait,
.econ-landing [data-auth="in"][data-phase="misconfigured"] .au.none { display: block; }
/* CTFd prints a logout link only for a signed-in visitor, and its navbar
   comes before the stage, so it is the one auth signal there is at first
   paint: a signed-in team is never shown 로그인 while landing.js is still
   on its way. */
body:has(.navbar a[href$="/logout"]) .econ-landing .stage:not([data-auth]) .au.out { display: none; }

/* 마감 5분 전 · landing.js writes data-tail="1" on the same test that puts
   .is-warn / .is-crit on the clock, so the chip and the lede turn with it.
   Only those two have a 곧 마감 twin: the clock label, the ruler and the
   strip go on naming the round, so the swap is keyed on .chip and .lede. */
.econ-landing [data-phase] .ph.tail { display: none; }
.econ-landing [data-tail="1"][data-phase="round1"] .ph.round1.tail,
.econ-landing [data-tail="1"][data-phase="round2"] .ph.round2.tail { display: var(--ph, inline-flex); }
.econ-landing [data-tail="1"][data-phase="round1"] .chip.ph.round1:not(.tail),
.econ-landing [data-tail="1"][data-phase="round1"] .lede.ph.round1:not(.tail),
.econ-landing [data-tail="1"][data-phase="round2"] .chip.ph.round2:not(.tail),
.econ-landing [data-tail="1"][data-phase="round2"] .lede.ph.round2:not(.tail) { display: none; }

/* ── the band · full-bleed status header ──────────────────────────── */
.econ-landing .band { background: var(--d-surface); border-bottom: 1px solid var(--d-border); box-shadow: var(--d-shadow-1); padding-block: 22px 20px; margin-inline: calc(50% - 50vw); padding-inline: calc(50vw - 50%); }
.econ-landing .topline { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--d-text-2); margin-bottom: 20px; }
.econ-landing .topline .sep { color: var(--d-border-strong); }
.econ-landing .topline .wordmark { display: flex; align-items: baseline; gap: 8px; font-size: 13px; font-weight: 500; line-height: 1.5; letter-spacing: 0; color: var(--d-ink); }
.econ-landing .topline .wordmark i { align-self: center; width: 8px; height: 8px; border-radius: 50%; background: var(--d-brand); flex: none; }
.econ-landing .topline .prod { color: var(--d-text-2); font-weight: 400; }
.econ-landing .topline .camp { color: var(--d-text-2); }
.econ-landing .hero { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 32px; align-items: end; }
.econ-landing .clabel { font-size: 13px; font-weight: 500; color: var(--d-text-2); margin-bottom: 6px; }

/* The clock: hours+minutes carry the room, seconds are detail, and the
   hours group only has text while an hour is left. --lclock is the clock's
   type size, resolved on .hero so its cqi are the stage's. The clock
   column is a container of its own, and the cqi term of each min() below
   is the most that group of digits can take without leaving the column —
   a start set days ahead brings a 3-digit hour, which landing.js marks
   .is-long. The 600 of the last minute is a real Geist Mono weight: the
   font request in THEME_HEADER_CSS asks for 400 to 700. */
@property --lclock { syntax: "<length>"; inherits: true; initial-value: 88px; }
.econ-landing .hero { --lclock: clamp(64px, min(14cqi, 17vh), 152px); }
.econ-landing .clockcol { container-type: inline-size; min-width: 0; }
.econ-landing .clock { font-family: var(--d-f-mono); font-weight: 500;
  font-size: 88px;                                   /* fallback: no container units */
  font-size: min(var(--lclock), 44cqi); line-height: .96; letter-spacing: -.052em; font-variant-numeric: tabular-nums slashed-zero; color: var(--d-ink); display: flex; align-items: baseline; }
.econ-landing .clock:has(.hh:not(:empty)) { font-size: min(var(--lclock), 25cqi); }
.econ-landing .clock:has(.hh:not(:empty)).is-long { font-size: min(var(--lclock), 19cqi); }
.econ-landing .clock .hh, .econ-landing .clock .mm, .econ-landing .clock .ss { flex: none; }
.econ-landing .clock .hh:empty { display: none; }
.econ-landing .clock .ss { color: var(--d-text-3); font-weight: 400; font-size: .52em; letter-spacing: -.02em; margin-left: .06em; }
.econ-landing .clock.is-warn { color: var(--d-warn-text); }
.econ-landing .clock.is-warn .ss { color: var(--d-warn-text); }
.econ-landing .clock.is-crit { color: var(--d-bad-text); font-weight: 600; }
.econ-landing .clock.is-crit .ss { color: var(--d-bad); }
.econ-landing .clock.done { color: var(--d-text-3); font-weight: 400; }
.econ-landing .clock.done .ss { color: var(--d-text-3); }
/* nothing is counting: the dashes are a placeholder, not a time */
.econ-landing .stage:not([data-phase]) .clock,
.econ-landing [data-phase="open"] .clock,
.econ-landing [data-phase="misconfigured"] .clock { color: var(--d-text-3); font-weight: 400; }

/* the minute ruler — the current round unrolled, one tick per minute;
   landing.js sets --ticks / --major / --el from the round's timestamps */
.econ-landing .ruler-wrap { display: none; margin-top: 18px; }
.econ-landing [data-phase="round1"] .ruler-wrap, .econ-landing [data-phase="round2"] .ruler-wrap { display: block; }
.econ-landing .ruler { position: relative; height: 20px; }
.econ-landing .ruler i { position: absolute; inset: 0; background-repeat: no-repeat; background-position: 0 100%, 0 100%;
  background-image: repeating-linear-gradient(90deg, currentColor 0 1px, transparent 1px calc(100% / var(--ticks, 70))),
                    repeating-linear-gradient(90deg, currentColor 0 1px, transparent 1px calc(100% / var(--major, 7)));
  background-size: 100% 9px, 100% 19px; }
.econ-landing .ruler i.base { color: var(--d-border-strong); }
.econ-landing .ruler i.el { color: var(--d-accent); clip-path: inset(0 calc(100% - var(--el, 0%)) 0 0); transition: clip-path 1s linear; }
.econ-landing .rlabels { display: flex; justify-content: space-between; font-size: 12px; color: var(--d-text-3); margin-top: 5px; font-variant-numeric: tabular-nums; }
.econ-landing .rlabels span { white-space: nowrap; }
.econ-landing .rlabels b { color: var(--d-accent-text); font-weight: 500; }
/* Where the ruler would be, a phase with nothing running gets a sentence
   the lede beside it does not say. 연결 중, 시작 전 and 종료 have none:
   their lede already carries it (and 시작 전 has the toy). */
.econ-landing .rnote { margin-top: 18px; font-size: 13.5px; color: var(--d-text-2); border-top: 1px solid var(--d-border); padding-top: 12px; }
.econ-landing .rnote.misconfigured { color: var(--d-bad-text); }

/* the one action */
.econ-landing .actcol { padding-bottom: 4px; }
.econ-landing .lede { font-size: 15px; line-height: 1.6; color: var(--d-text-2); margin-bottom: 16px; }
.econ-landing .lede b { color: var(--d-ink); }
.econ-landing .au .hint { font-size: 12.5px; color: var(--d-text-3); margin-top: 9px; line-height: 1.45; }
.econ-landing .au .quiet { display: flex; gap: 10px; align-items: flex-start; padding: 12px 14px; border: 1px solid var(--d-border); border-radius: 8px; background: var(--d-surface-2); font-size: 13.5px; line-height: 1.55; color: var(--d-text-2); max-width: 46ch; }
.econ-landing .au .quiet svg.ic { margin-top: 2px; color: var(--d-text-3); }
.econ-landing .au .quiet b { color: var(--d-ink); }
.econ-landing .au.none .quiet { background: var(--d-bad-soft); border-color: var(--d-bad-line); color: var(--d-bad-text); }
.econ-landing .au.none .quiet svg.ic, .econ-landing .au.none .quiet b { color: var(--d-bad-text); }

/* ── the rail · five phases as one instrument ─────────────────────── */
/* The two rounds and the break are drawn to their real durations, in
   minutes (ROUND_1_DURATION / BREAK_DURATION / ROUND_2_DURATION in
   econ_judge/competition.py); --rounds is the only copy, and the media
   queries below change only the end caps. 시작 전 and 종료 have no
   duration at all, so they are fixed end caps, never fr units. */
.econ-landing .rail { margin-top: 22px; padding-top: 18px; border-top: 1px solid var(--d-border); }
.econ-landing .segs { --rounds: 70fr 10fr 80fr; display: grid; grid-template-columns: 72px var(--rounds) 72px; gap: 0 8px; }
.econ-landing .seg { min-width: 0; }
.econ-landing .seg .track { position: relative; height: 6px; border-radius: 3px; background: var(--d-surface-2); box-shadow: inset 0 0 0 1px var(--seg-ring, var(--d-border)); overflow: hidden; }
.econ-landing .seg .track i { display: block; height: 100%; width: var(--seg-fill, 0); background: var(--seg-bg, var(--d-text-3)); border-radius: inherit; transition: var(--seg-tr, none); animation-name: var(--seg-anim, none); animation-duration: .5s; animation-timing-function: var(--d-ease); animation-fill-mode: both; }
.econ-landing .seg .cap { display: var(--seg-cap, block); font-size: 12.5px; color: var(--seg-capc, var(--d-text-3)); font-weight: var(--seg-capw, 400); margin-top: 8px; line-height: 1.35; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.econ-landing .seg .cap::after { content: var(--seg-tick, ""); color: var(--d-ok-text); font-weight: 600; }
.econ-landing .seg .here { display: var(--seg-here, none); align-items: center; height: 18px; padding: 0 6px; margin-right: 6px; border-radius: 4px; background: var(--d-accent-soft); color: var(--d-accent-text); font-size: 11px; font-weight: 600; letter-spacing: .01em; vertical-align: 1px; }
/* 완료 and 지금 are DERIVED from data-phase: the phase picks the segment,
   the segment sets inherited custom properties, and the rules above read
   them, so the whole rail moves on one attribute write and cannot drift
   from the phase copy. 완료 is never colour alone: the fill goes to text-3
   AND the caption gains a ✓. A completed segment is painted full at once;
   only the current one rises in and then glides as --el moves, so a reload
   never sweeps the finished part of the rail again. */
.econ-landing [data-phase="round1"] .seg:nth-child(-n+1),
.econ-landing [data-phase="break"] .seg:nth-child(-n+2),
.econ-landing [data-phase="round2"] .seg:nth-child(-n+3),
.econ-landing [data-phase="finished"] .seg:nth-child(-n+4) { --seg-fill: 100%; --seg-tick: " ✓"; }
.econ-landing [data-phase="before"] .seg:nth-child(1),
.econ-landing [data-phase="round1"] .seg:nth-child(2),
.econ-landing [data-phase="break"] .seg:nth-child(3),
.econ-landing [data-phase="round2"] .seg:nth-child(4),
.econ-landing [data-phase="finished"] .seg:nth-child(5) { --seg-fill: var(--el, 0%); --seg-tr: width 1s linear; --seg-bg: var(--d-accent); --seg-ring: var(--d-accent); --seg-anim: lp-fill; --seg-capc: var(--d-ink); --seg-capw: 500; --seg-here: inline-flex; --seg-cap: block; }
/* 시작 전 draws nothing it would later have to take back: the current
   segment is marked 지금 and carries no fill. */
.econ-landing [data-phase="before"] .seg:nth-child(1) { --seg-fill: 0%; --seg-anim: none; }

/* finished · the state this page is in for about eleven months of the
   year. A dead 00:00 must not own the first screen, so the clock steps
   down and the band closes up: the strip and 여기서 하는 일 rise. */
.econ-landing [data-phase="finished"] .hero { --lclock: clamp(40px, 5.5cqi, 72px); }
.econ-landing [data-phase="finished"] .band { padding-bottom: 12px; }

/* ── 시작 전 · the wait becomes the toy ──────────────────────────── */
/* In `before` the right column swaps the action for the truth-table toy,
   and the action moves under the clock, beneath its own headline (.bhead).
   The toy comes first in the markup, though the two are never shown
   together: when a round opens under a keyboard user's focus in the toy,
   the browser drops that focus and the next Tab starts from where the
   toy was, which is then just before the action the round has brought. */
.econ-landing .toycol, .econ-landing .bhead, .econ-landing .au-inline { display: none; }
.econ-landing [data-phase="before"] .hero { grid-template-columns: minmax(0, 1fr) minmax(0, 430px); align-items: start; gap: 40px; }
.econ-landing [data-phase="before"] .actcol { display: none; }
.econ-landing [data-phase="before"] .toycol,
.econ-landing [data-phase="before"] .au-inline,
.econ-landing [data-phase="before"] .bhead { display: block; }
.econ-landing .bhead { font-size: 22px; font-weight: 600; letter-spacing: -.018em; line-height: 1.3; color: var(--d-ink); margin: 24px 0 8px; text-wrap: balance; }
.econ-landing .au-inline .lede { margin-bottom: 18px; max-width: 46ch; }
.econ-landing .au-inline .lbtn.xl { width: auto; min-width: 260px; }
.econ-landing .au-inline .au .hint { max-width: 46ch; }
/* From 1200px the lede opens to 58ch: one sentence a line, so the
   invitation stays near the toy it points at (any wider and the second
   sentence splits 맛보기 표에서 across the break). */
@media (min-width: 1200px) { .econ-landing .au-inline .lede { max-width: 58ch; } }

/* ── below the fold ───────────────────────────────────────────────── */
.econ-landing .body { padding-top: 22px; padding-bottom: 28px; }
.econ-landing .strip { display: grid; grid-template-columns: repeat(4, 1fr); background: var(--d-surface); border: 1px solid var(--d-border); border-radius: 10px; box-shadow: var(--d-shadow-1); }
.econ-landing .strip > div { padding: 14px 20px; border-left: 1px solid var(--d-border); min-width: 0; }
.econ-landing .strip > div:first-child { border-left: 0; }
.econ-landing .strip small { display: block; font-size: 12.5px; font-weight: 400; line-height: 1.5; color: var(--d-text-2); margin-bottom: 4px; }
/* A figure may wrap, but only in front of its dimmed unit (.den), which
   moves down whole: "1 · 2라운드" over "· 개방", never a stray "·". */
.econ-landing .strip b { display: block; font-size: 20px; font-weight: 600; line-height: 1.2; letter-spacing: -.01em; font-variant-numeric: tabular-nums; white-space: normal; }
.econ-landing .strip b .den { color: var(--d-text-3); font-weight: 500; font-size: 15px; white-space: nowrap; }

.econ-landing .sech { display: flex; align-items: baseline; gap: 10px; margin: 34px 0 14px; }
.econ-landing .sech h2 { font-size: 17px; font-weight: 600; line-height: 1.5; letter-spacing: -.01em; }
.econ-landing .sech span { font-size: 13px; color: var(--d-text-3); }
.econ-landing .steps { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
.econ-landing .two { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
/* One card, two names: .step (with an .sh header and an .n ordinal) in
   여기서 하는 일, .panel (with a .ch header) everywhere else. */
.econ-landing .step, .econ-landing .panel { background: var(--d-surface); border: 1px solid var(--d-border); border-radius: 10px; box-shadow: var(--d-shadow-1); padding: 16px 18px 18px; min-width: 0; }
.econ-landing .step .sh, .econ-landing .panel .ch { display: flex; align-items: center; gap: 9px; margin-bottom: 8px; }
.econ-landing .step .sh svg.ic, .econ-landing .panel .ch svg.ic { width: 17px; height: 17px; color: var(--d-accent); stroke-width: 1.8; }
.econ-landing .step .sh b, .econ-landing .panel .ch b { font-size: 14.5px; font-weight: 600; }
/* flex: none, or the root's overflow-wrap: anywhere lets a squeezed
   header break "02" between its digits */
.econ-landing .step .sh .n { flex: none; font-family: var(--d-f-mono); font-size: 12px; color: var(--d-text-3); margin-left: auto; }
.econ-landing .step p, .econ-landing .panel > p { font-size: 13.5px; line-height: 1.6; color: var(--d-text-2); }
.econ-landing .panel .ch .chip { margin-left: auto; height: 24px; font-size: 12.5px; }
.econ-landing .panel > .fig + p, .econ-landing .panel > .tbl + .rule { margin-top: 10px; }

/* the submission folder: an example that really grades */
.econ-landing .fig { border: 1px solid var(--d-border); border-radius: 8px; background: var(--d-surface); margin-top: 12px; overflow: hidden; container-type: inline-size; }
.econ-landing .figcap { display: flex; justify-content: space-between; gap: 10px; padding: 7px 10px; border-bottom: 1px solid var(--d-border); background: var(--d-surface-2); font-family: var(--d-f-mono); font-size: 10.5px; color: var(--d-text-3); }
.econ-landing .tree { display: grid; grid-template-columns: minmax(0,1fr) auto; column-gap: 16px; align-items: baseline; font-family: var(--d-f-mono); font-size: 12px; line-height: 1.85; padding: 10px 12px; color: var(--d-text-2); }
.econ-landing .tree > div { display: contents; }
/* A file name is never cut short: a long one wraps, and its second line
   hangs under the name, clear of the ├─ branch. */
.econ-landing .tree .ln { white-space: pre-wrap; padding-left: 3ch; text-indent: -3ch; }
.econ-landing .tree b { color: var(--d-ink); font-weight: 500; }
.econ-landing .tree em { font-style: normal; font-family: var(--d-f-sans); font-size: 11.5px; color: var(--d-accent-text); white-space: nowrap; }
/* Too narrow for a name and its note side by side (phones, the 720px
   container's two cards): each note goes under its line. */
@container (max-width: 380px) {
  .econ-landing .tree { grid-template-columns: minmax(0, 1fr); }
  .econ-landing .tree em { padding-left: 3ch; line-height: 1.5; margin-bottom: 5px; }
}
.econ-landing .tbl { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.econ-landing .tbl th { font-size: 11.5px; font-weight: 500; line-height: 1.5; color: var(--d-text-3); text-align: right; padding: 0 8px 6px; border-bottom: 1px solid var(--d-border-strong); font-family: var(--d-f-mono); }
.econ-landing .tbl th:first-child, .econ-landing .tbl td:first-child { text-align: left; padding-left: 0; }
.econ-landing .tbl th:last-child, .econ-landing .tbl td:last-child { padding-right: 0; }
.econ-landing .tbl td { font-size: 13.5px; line-height: 1.5; text-align: right; padding: 7px 8px; border-bottom: 1px solid var(--d-border); color: var(--d-text-2); }
.econ-landing .tbl td:first-child { color: var(--d-ink); font-weight: 500; }
.econ-landing .tbl tr:last-child td { border-bottom: 0; border-top: 1px solid var(--d-border-strong); font-weight: 600; color: var(--d-ink); }
.econ-landing .rule { margin-top: 12px; padding: 11px 13px; border-radius: 8px; background: var(--d-surface-2); border: 1px solid var(--d-border); font-size: 13.5px; line-height: 1.6; color: var(--d-text-2); }
.econ-landing .rule b { color: var(--d-ink); }
.econ-landing .lim { margin-top: 10px; font-size: 12.5px; line-height: 1.55; color: var(--d-text-3); font-variant-numeric: tabular-nums; }
.econ-landing .lim b { color: var(--d-text-2); font-weight: 500; }

/* the failure list, then the sentences the problem page really shows */
.econ-landing .fails { margin-top: 4px; }
.econ-landing .fails li { display: grid; grid-template-columns: 18px minmax(0,1fr); gap: 8px; align-items: start; font-size: 13.5px; line-height: 1.6; color: var(--d-text-2); padding: 7px 0; border-top: 1px solid var(--d-border); }
.econ-landing .fails li:first-child { border-top: 0; padding-top: 0; }
.econ-landing .fails li b { color: var(--d-ink); font-weight: 600; }
.econ-landing .fails .x { width: 18px; height: 18px; border-radius: 5px; background: var(--d-bad-soft); color: var(--d-bad-text); display: grid; place-items: center; margin-top: 2px; }
.econ-landing .fails .x svg.ic { width: 11px; height: 11px; stroke-width: 3; }
.econ-landing details.raw { margin-top: 12px; border: 1px solid var(--d-border); border-radius: 8px; background: var(--d-surface-2); padding: 8px 10px; font-size: 12.5px; }
.econ-landing details.raw summary { list-style: none; cursor: pointer; display: flex; align-items: center; gap: 6px; font-weight: 500; color: var(--d-accent-text); }
.econ-landing details.raw summary::-webkit-details-marker { display: none; }
.econ-landing details.raw summary svg.ic { width: 13px; height: 13px; margin-left: auto; transition: transform .15s var(--d-ease); }
.econ-landing details.raw[open] summary svg.ic { transform: rotate(180deg); }
/* no border to turn accent, and the halo alone is under 3:1 on the
   surface-2 ground, so the summary takes a solid ring */
.econ-landing details.raw summary:focus-visible { outline: 2px solid var(--d-accent); outline-offset: 2px; border-radius: 4px; }
/* the quoted sentences are prose, not machine output; each one's <i> says
   where it appears (the result card, or under the folder picker) */
.econ-landing details.raw .t { font-family: var(--d-f-sans); font-size: 12.5px; line-height: 1.7; color: var(--d-text-2); margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--d-border); }
.econ-landing details.raw .t span { display: block; }
.econ-landing details.raw .t span + span { margin-top: 4px; }
.econ-landing details.raw .t i { font-style: normal; font-size: 11.5px; font-weight: 500; color: var(--d-text-3); margin-right: 6px; }

.econ-landing .facts { display: grid; grid-template-columns: repeat(3, 1fr); border: 1px solid var(--d-border); border-radius: 10px; overflow: hidden; background: var(--d-surface); box-shadow: var(--d-shadow-1); }
.econ-landing .facts > div { padding: 14px 18px 16px; border-left: 1px solid var(--d-border); border-top: 1px solid var(--d-border); }
.econ-landing .facts > div:nth-child(3n+1) { border-left: 0; }
.econ-landing .facts > div:nth-child(-n+3) { border-top: 0; }
.econ-landing .facts dt { font-size: 12.5px; font-weight: 400; color: var(--d-text-2); margin-bottom: 4px; }
.econ-landing .facts dd { margin: 0; font-size: 14.5px; font-weight: 500; letter-spacing: -.005em; font-variant-numeric: tabular-nums; }
.econ-landing .facts dd small { display: block; font-size: 12.5px; font-weight: 400; color: var(--d-text-3); margin-top: 3px; letter-spacing: 0; }

.econ-landing .foot { margin-top: 32px; padding-top: 16px; border-top: 1px solid var(--d-border); display: flex; flex-wrap: wrap; gap: 8px 18px; align-items: center; font-size: 12.5px; color: var(--d-text-3); }
.econ-landing .foot .mono { font-size: 12px; }
.econ-landing .foot .end { margin-left: auto; }

/* ── the truth-table toy (CSS only), scoped under .toy ────────────── */
/* Zero JavaScript: sixteen radios and a checkbox, graded by sibling
   selectors and CSS counters. It is not a contest problem and sends
   nothing anywhere, and it says so under the table. The short names:
   .v an answer radio (.i0 to .i7 its row, .o0 / .o1 its value), .run the
   채점 checkbox, .tb the table, .r a row (.hd the header, .r0 to .r7 the
   cases), .cs the case id, .ab an A / B / C input, .yh the Y header,
   .rad the 0 / 1 picker (.l0 / .l1 its labels), .mk the grade mark
   (--g its glyph, --gc its colour), .tf the 채점 row with .cnt the answer
   count, .vwrap the verdict, .vp a verdict card (.partial, .fail or
   .pass; .vhead its top line, --vc its colour, --von the ink on it, --vb
   its border, --vs its soft ground, --vt its text), .okn the pass count,
   .po the pass card's link. */
.econ-landing .toy { position: relative; display: flex; flex-direction: column; background: var(--d-surface); border: 1px solid var(--d-border); border-radius: 14px; box-shadow: var(--d-shadow-3); padding: 16px 16px 14px; counter-reset: lp-ans 0 lp-ok 0; }
.econ-landing .toy .v, .econ-landing .toy .run { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; margin: 0; opacity: 0; }
/* Each clipped input is pinned onto the control that stands in for it, so
   the browser's own focus scroll brings that row (or 채점) into view, not
   the toy's top-left corner, when a keyboard user tabs through at 200%
   zoom. Without anchor positioning the inputs stay in the corner. */
@supports (anchor-name: --a) {
  .econ-landing .toy .r0 .rad { anchor-name: --lp-toy-r0; }
  .econ-landing .toy .r1 .rad { anchor-name: --lp-toy-r1; }
  .econ-landing .toy .r2 .rad { anchor-name: --lp-toy-r2; }
  .econ-landing .toy .r3 .rad { anchor-name: --lp-toy-r3; }
  .econ-landing .toy .r4 .rad { anchor-name: --lp-toy-r4; }
  .econ-landing .toy .r5 .rad { anchor-name: --lp-toy-r5; }
  .econ-landing .toy .r6 .rad { anchor-name: --lp-toy-r6; }
  .econ-landing .toy .r7 .rad { anchor-name: --lp-toy-r7; }
  .econ-landing .toy [data-toy-run] { anchor-name: --lp-toy-run; }
  .econ-landing .toy .i0 { position-anchor: --lp-toy-r0; }
  .econ-landing .toy .i1 { position-anchor: --lp-toy-r1; }
  .econ-landing .toy .i2 { position-anchor: --lp-toy-r2; }
  .econ-landing .toy .i3 { position-anchor: --lp-toy-r3; }
  .econ-landing .toy .i4 { position-anchor: --lp-toy-r4; }
  .econ-landing .toy .i5 { position-anchor: --lp-toy-r5; }
  .econ-landing .toy .i6 { position-anchor: --lp-toy-r6; }
  .econ-landing .toy .i7 { position-anchor: --lp-toy-r7; }
  .econ-landing .toy .run { position-anchor: --lp-toy-run; }
  .econ-landing .toy .v, .econ-landing .toy .run { top: anchor(top); bottom: anchor(bottom); left: anchor(left); height: auto; scroll-margin-block: 8px; }
}
.econ-landing .toy .v:checked { counter-increment: lp-ans 1; }
.econ-landing .toy .i0.o0:checked, .econ-landing .toy .i1.o0:checked, .econ-landing .toy .i2.o0:checked, .econ-landing .toy .i3.o1:checked,
.econ-landing .toy .i4.o0:checked, .econ-landing .toy .i5.o1:checked, .econ-landing .toy .i6.o1:checked, .econ-landing .toy .i7.o1:checked { counter-increment: lp-ans 1 lp-ok 1; }
.econ-landing .toy .toyhd { display: flex; align-items: center; gap: 8px; padding: 2px 4px 12px; }
.econ-landing .toy .toyhd b { font-size: 15px; font-weight: 600; letter-spacing: -.01em; }
.econ-landing .toy .toyq { font-size: 13.5px; line-height: 1.55; color: var(--d-text-2); padding: 0 4px 12px; }

.econ-landing .toy .tb { border: 1px solid var(--d-border); border-radius: 10px; overflow: hidden; background: var(--d-surface); }
.econ-landing .toy .r { display: grid; grid-template-columns: 80px 30px 30px 30px minmax(0,1fr) 16px; align-items: center; gap: 8px; height: 31px; padding: 0 12px; border-top: 1px solid var(--d-border); }
.econ-landing .toy .r.hd { height: 28px; background: var(--d-surface-2); border-top: 0; color: var(--d-text-2); font-size: 12px; font-weight: 500; }
.econ-landing .toy .r .cs { font-family: var(--d-f-mono); font-size: 11.5px; letter-spacing: -.02em; color: var(--d-text-3); font-variant-numeric: slashed-zero; }
.econ-landing .toy .r .ab { font-family: var(--d-f-mono); font-size: 13px; text-align: center; color: var(--d-text-2); font-variant-numeric: slashed-zero; }
.econ-landing .toy .r.hd .ab, .econ-landing .toy .r.hd .yh { color: var(--d-text-2); font-family: var(--d-f-sans); font-size: 12px; }
.econ-landing .toy .r .yh { justify-self: end; padding-right: 22px; }
.econ-landing .toy .rad { justify-self: end; display: inline-flex; border: 1px solid var(--d-border-strong); border-radius: 6px; overflow: hidden; background: var(--d-surface); }
.econ-landing .toy .rad > * { width: 31px; height: 23px; display: grid; place-items: center; margin: 0; font-family: var(--d-f-mono); font-size: 12.5px; font-weight: 500; color: var(--d-text-3); cursor: pointer; user-select: none; transition: background .12s var(--d-ease), color .12s var(--d-ease); }
.econ-landing .toy .rad > * + * { border-left: 1px solid var(--d-border-strong); }
.econ-landing .toy .rad > *:hover { background: var(--d-surface-2); color: var(--d-ink); }
.econ-landing .toy .mk { justify-self: center; font-size: 13px; font-weight: 700; line-height: 1; }
/* the resting dot is a real character, so it takes text contrast, not
   border contrast; its empty alt keeps it out of what a screen reader
   says, while a graded ✓ or ✗ (set through --g) is still read */
.econ-landing .toy .mk::after { content: var(--g, "·" / ""); color: var(--gc, var(--d-text-3)); }
.econ-landing .toy .r.hd .mk { --g: ""; }

/* selected segment — the live sibling chain */
.econ-landing .toy .i0.o0:checked ~ .tb .r0 .l0, .econ-landing .toy .i1.o0:checked ~ .tb .r1 .l0, .econ-landing .toy .i2.o0:checked ~ .tb .r2 .l0, .econ-landing .toy .i3.o0:checked ~ .tb .r3 .l0,
.econ-landing .toy .i4.o0:checked ~ .tb .r4 .l0, .econ-landing .toy .i5.o0:checked ~ .tb .r5 .l0, .econ-landing .toy .i6.o0:checked ~ .tb .r6 .l0, .econ-landing .toy .i7.o0:checked ~ .tb .r7 .l0,
.econ-landing .toy .i0.o1:checked ~ .tb .r0 .l1, .econ-landing .toy .i1.o1:checked ~ .tb .r1 .l1, .econ-landing .toy .i2.o1:checked ~ .tb .r2 .l1, .econ-landing .toy .i3.o1:checked ~ .tb .r3 .l1,
.econ-landing .toy .i4.o1:checked ~ .tb .r4 .l1, .econ-landing .toy .i5.o1:checked ~ .tb .r5 .l1, .econ-landing .toy .i6.o1:checked ~ .tb .r6 .l1, .econ-landing .toy .i7.o1:checked ~ .tb .r7 .l1 {
  background: var(--d-accent); color: var(--d-on-accent); forced-color-adjust: none;
}
/* Windows High Contrast paints every segment alike. The chosen one takes
   the system's selection colours instead, and forced-color-adjust above
   keeps the text backplate from covering its digit. */
@media (forced-colors: active) {
  .econ-landing .toy .rad { --d-accent: Highlight; --d-on-accent: HighlightText; }
}
/* keyboard focus travels the hidden radios — surface it on the visible segment */
.econ-landing .toy .i0:focus-visible ~ .tb .r0 .rad, .econ-landing .toy .i1:focus-visible ~ .tb .r1 .rad, .econ-landing .toy .i2:focus-visible ~ .tb .r2 .rad, .econ-landing .toy .i3:focus-visible ~ .tb .r3 .rad,
.econ-landing .toy .i4:focus-visible ~ .tb .r4 .rad, .econ-landing .toy .i5:focus-visible ~ .tb .r5 .rad, .econ-landing .toy .i6:focus-visible ~ .tb .r6 .rad, .econ-landing .toy .i7:focus-visible ~ .tb .r7 .rad {
  border-color: var(--d-accent); box-shadow: 0 0 0 3px var(--d-focus); outline: 2px solid transparent; outline-offset: 0;
}
/* graded marks — correct rows */
.econ-landing .toy .i0.o0:checked ~ .run:checked ~ .tb .r0, .econ-landing .toy .i1.o0:checked ~ .run:checked ~ .tb .r1,
.econ-landing .toy .i2.o0:checked ~ .run:checked ~ .tb .r2, .econ-landing .toy .i3.o1:checked ~ .run:checked ~ .tb .r3,
.econ-landing .toy .i4.o0:checked ~ .run:checked ~ .tb .r4, .econ-landing .toy .i5.o1:checked ~ .run:checked ~ .tb .r5,
.econ-landing .toy .i6.o1:checked ~ .run:checked ~ .tb .r6, .econ-landing .toy .i7.o1:checked ~ .run:checked ~ .tb .r7 { --g: "✓"; --gc: var(--d-ok-text); }
/* graded marks — wrong rows */
.econ-landing .toy .i0.o1:checked ~ .run:checked ~ .tb .r0, .econ-landing .toy .i1.o1:checked ~ .run:checked ~ .tb .r1,
.econ-landing .toy .i2.o1:checked ~ .run:checked ~ .tb .r2, .econ-landing .toy .i3.o0:checked ~ .run:checked ~ .tb .r3,
.econ-landing .toy .i4.o1:checked ~ .run:checked ~ .tb .r4, .econ-landing .toy .i5.o0:checked ~ .run:checked ~ .tb .r5,
.econ-landing .toy .i6.o0:checked ~ .run:checked ~ .tb .r6, .econ-landing .toy .i7.o0:checked ~ .run:checked ~ .tb .r7 { --g: "✗"; --gc: var(--d-bad-text); background: var(--d-bad-soft); }
/* graded → the table takes no pointer input. The hidden radios stay
   focusable, so a keyboard user can still change an answer; the marks and
   the verdict follow it live, and nothing is sent anywhere. */
.econ-landing .toy .run:checked ~ .tb { pointer-events: none; }
.econ-landing .toy .run:checked ~ .tb .rad { border-color: var(--d-border); }

.econ-landing .toy .tf { display: flex; align-items: center; gap: 10px; margin-top: 12px; }
.econ-landing .toy .tf .cnt { font-size: 13px; color: var(--d-text-2); font-variant-numeric: tabular-nums; white-space: nowrap; }
.econ-landing .toy .tf .cnt b { color: var(--d-ink); font-weight: 600; }
.econ-landing .toy .cnt.live b::before { content: counter(lp-ans); }
.econ-landing .toy .tf .lbtn { flex: 1; }
.econ-landing .toy .toyfoot { display: flex; gap: 7px; align-items: flex-start; font-size: 12px; line-height: 1.5; color: var(--d-text-3); margin-top: 11px; padding: 0 2px; }
.econ-landing .toy .toyfoot svg.ic { width: 13px; height: 13px; margin-top: 2px; }
/* The 채점 row STAYS once graded — it holds the focused control (the clipped
   .run checkbox). Removing it would drop keyboard and screen-reader users back
   to the top of the document with nothing announced. It is restyled instead:
   the button steps down from the saturated blue and relabels itself 다시 풀기,
   which is what pressing it now does (it unchecks .run and reopens the table). */
.econ-landing .toy .run:checked ~ .tf .lbtn { background: var(--d-surface); border-color: var(--d-border-strong); color: var(--d-ink); box-shadow: var(--d-shadow-1); }
.econ-landing .toy .run:checked ~ .tf .lbtn svg.ic { display: none; }
.econ-landing .toy .tf .t-edit { display: none; }
.econ-landing .toy .run:checked ~ .tf .t-run { display: none; }
.econ-landing .toy .run:checked ~ .tf .t-edit { display: inline; }
/* Space on the clipped checkbox grades the toy with zero JavaScript — so the
   ring has to be visible on the button that stands in for it. (The
   signed-in blue 채점 below outranks this rule, so it draws its own.) */
.econ-landing .toy .run:focus-visible ~ .tf [data-toy-run] { border-color: var(--d-accent); box-shadow: 0 0 0 3px var(--d-focus); outline: 2px solid transparent; outline-offset: 2px; }
.econ-landing .toy .run:checked ~ .toyfoot { order: 2; margin-top: 12px; }

/* verdict panel — a small copy of the problem page's verdict card
   (problem.css .result: top strip, 40px disc, state chip, N / M line);
   change the two together. .vwrap is a live region that is always
   rendered and stays empty until graded, so the card that appears inside
   it is announced: a region that itself goes from display: none to block
   announces nothing. */
.econ-landing .toy .vwrap > .vp { margin-top: 12px; }
.econ-landing .toy .vp { position: relative; overflow: hidden; border: 1px solid var(--vb); border-radius: 10px; background: var(--vs); padding: 15px 14px 13px; }
/* only the live toy's own verdict rises in */
.econ-landing .toy .run:checked ~ .vwrap .vp { animation: lp-rise .34s var(--d-ease) both; }
.econ-landing .toy .vp::before { content: ""; position: absolute; left: 0; right: 0; top: 0; height: 3px; background: var(--vc); }
.econ-landing .toy .vp .vhead { display: flex; align-items: center; gap: 11px; margin-bottom: 10px; }
.econ-landing .toy .vp .ico { width: 40px; height: 40px; border-radius: 50%; background: var(--vc); color: var(--von); display: grid; place-items: center; flex: none; }
.econ-landing .toy .vp .ico svg.ic { width: 20px; height: 20px; stroke-width: 2.6; }
.econ-landing .toy .vp h3 { font-size: 19px; font-weight: 600; letter-spacing: -.015em; line-height: 1.25; font-variant-numeric: tabular-nums; }
.econ-landing .toy .vp p { font-size: 13.5px; line-height: 1.55; color: var(--d-text-2); margin-top: 5px; }
.econ-landing .toy .vp .vact { display: flex; gap: 8px; margin-top: 13px; }
.econ-landing .toy .vp .vact > * { flex: 1; }
.econ-landing .toy .vp.pass { --vc: var(--d-ok); --von: var(--d-on-ok); --vb: var(--d-ok-line); --vs: var(--d-ok-soft); --vt: var(--d-ok-text); }
.econ-landing .toy .vp.partial { --vc: var(--d-warn); --von: var(--d-on-warn); --vb: var(--d-warn-line); --vs: var(--d-warn-soft); --vt: var(--d-warn-text); }
.econ-landing .toy .vp.fail { --vc: var(--d-bad); --von: var(--d-on-bad); --vb: var(--d-bad-line); --vs: var(--d-bad-soft); --vt: var(--d-bad-text); }
/* the chip sits on a tinted panel, so it takes the surface as its ground */
.econ-landing .toy .vp .vhead .chip { height: 26px; padding: 0 10px; font-size: 13.5px; font-weight: 600; background: var(--d-surface); color: var(--vt); border: 1px solid var(--vb); }
/* the check is drawn at rest; only the live toy's own verdict animates it in */
.econ-landing .toy .run:checked ~ .vwrap .vp.pass .ico polyline { stroke-dasharray: 24; animation: lp-draw .4s var(--d-ease); }
.econ-landing .toy .okn.live::before { content: counter(lp-ok); }
/* Graded, the toy answers the way the problem page does: 부분 통과 by
   default, 전체 통과 when all eight rows are right, and 실패 when none
   is (view.js gives a graded 0 / M its is-fail tier). */
.econ-landing .toy .vwrap .vp { display: none; }
.econ-landing .toy .run:checked ~ .vwrap .vp.partial { display: block; }
.econ-landing .toy .i0.o0:not(:checked) ~ .i1.o0:not(:checked) ~ .i2.o0:not(:checked) ~ .i3.o1:not(:checked) ~ .i4.o0:not(:checked) ~ .i5.o1:not(:checked) ~ .i6.o1:not(:checked) ~ .i7.o1:not(:checked) ~ .run:checked ~ .vwrap .vp.partial { display: none; }
.econ-landing .toy .i0.o0:not(:checked) ~ .i1.o0:not(:checked) ~ .i2.o0:not(:checked) ~ .i3.o1:not(:checked) ~ .i4.o0:not(:checked) ~ .i5.o1:not(:checked) ~ .i6.o1:not(:checked) ~ .i7.o1:not(:checked) ~ .run:checked ~ .vwrap .vp.fail { display: block; }
.econ-landing .toy .i0.o0:checked ~ .i1.o0:checked ~ .i2.o0:checked ~ .i3.o1:checked ~ .i4.o0:checked ~ .i5.o1:checked ~ .i6.o1:checked ~ .i7.o1:checked ~ .run:checked ~ .vwrap .vp.partial { display: none; }
.econ-landing .toy .i0.o0:checked ~ .i1.o0:checked ~ .i2.o0:checked ~ .i3.o1:checked ~ .i4.o0:checked ~ .i5.o1:checked ~ .i6.o1:checked ~ .i7.o1:checked ~ .run:checked ~ .vwrap .vp.pass { display: block; }

/* One saturated blue per view. Signed out, it is 로그인, so the toy
   grades on a keyline button. Signed in before the start the toy is the
   only thing left to do, so its 채점 takes the blue — until it has been
   pressed, when the graded rule above (.run:checked ~ .tf .lbtn) steps it
   down to 다시 풀기. The blue outranks the toy's focus rule, so it states
   its own ring, one class stronger so source order cannot undo it: the
   solid ring every filled button on this page takes. */
.econ-landing .toy .tf .lbtn.primary { background: var(--d-surface); border-color: var(--d-border-strong); color: var(--d-ink); box-shadow: var(--d-shadow-1); }
.econ-landing .toy .tf .lbtn.primary:hover { background: var(--d-surface-2); }
.econ-landing [data-auth="in"][data-phase="before"] .toy .run:not(:checked) ~ .tf .lbtn.primary { background: var(--d-accent); border-color: var(--d-accent); color: var(--d-on-accent); box-shadow: 0 1px 2px rgba(16,24,40,.12); }
.econ-landing [data-auth="in"][data-phase="before"] .toy .run:not(:checked) ~ .tf .lbtn.primary:hover { background: var(--d-accent-hover); border-color: var(--d-accent-hover); }
.econ-landing [data-auth="in"][data-phase="before"] .toy .run:not(:checked):focus-visible ~ .tf [data-toy-run].lbtn.primary { box-shadow: 0 0 0 2px var(--d-surface), 0 0 0 4px var(--d-accent); }
/* the pass card's way out: signed out it points at 로그인 (keyline — the
   hero already holds the blue); signed in there is nowhere to point yet */
.econ-landing .toy .po { display: none; }
.econ-landing [data-auth="out"] .toy .po, .econ-landing .stage:not([data-auth]) .toy .po { display: inline-flex; }

/* ── 1366×768: the first screen must land without scrolling ─────── */
@media (min-width: 1200px) and (max-height: 800px) {
  .econ-landing .band { padding-block: 16px; }
  .econ-landing .topline { margin-bottom: 14px; }
  .econ-landing .ruler-wrap { margin-top: 14px; }
  .econ-landing .rail { margin-top: 16px; padding-top: 14px; }
  .econ-landing .body { padding-top: 14px; }
  .econ-landing .sech { margin: 20px 0 10px; }
  .econ-landing .strip > div { padding: 11px 20px; }
  .econ-landing .step { padding: 13px 16px 15px; }
  .econ-landing .toy .r { height: 29px; }
}
/* 시작 전 wears the 지금 badge on its end cap, so from 768px, where CTFd's
   container turns 720px wide, the cap widens to 96px; the phone rail keeps
   52px caps. */
@media (min-width: 768px) {
  .econ-landing .segs { grid-template-columns: 96px var(--rounds) 72px; }
}
/* 휴식 is ten minutes of a 160-minute rail. From 992px up its bar holds
   휴식 ✓, and while it is the current segment "지금 휴식" runs back into
   the room round 1's caption leaves free: right-aligned, but ending 10px
   short of its bar's end, because the 8px track gap alone runs it into
   2라운드's caption. Narrower, the bar cannot hold even 휴식 ✓, so the
   rail shows only the current caption, as the phone rail does; the
   right-hand two hang theirs off their bar's right edge so it grows
   leftwards and stays on screen (at 360px 2라운드's otherwise runs past
   the edge). 준비 모드 and 일정 설정 필요 have no current segment to
   caption, so the narrow rail is left out there. Before the first phase
   arrives it keeps its place, unpainted, so the phase that does arrive
   moves nothing below it. */
@media (min-width: 992px) {
  .econ-landing [data-phase="break"] .seg:nth-child(3) .cap { overflow: visible; text-align: right; margin-left: -64px; padding-right: 10px; }
}
/* CTFd's container stays 720px wide up to 991.98px, which leaves the
   clock about 200px beside a 430px toy, so the toy goes under it. */
@media (max-width: 991.98px) {
  .econ-landing .seg .cap { display: var(--seg-cap, none); overflow: visible; white-space: normal; width: max-content; max-width: 190px; }
  .econ-landing .segs > li:nth-child(n+4) { display: flex; flex-direction: column; align-items: flex-end; }
  .econ-landing .segs > li:nth-child(n+4) .track { align-self: stretch; }
  .econ-landing [data-phase="open"] .rail,
  .econ-landing [data-phase="misconfigured"] .rail { display: none; }
  .econ-landing .stage:not([data-phase]) .rail { visibility: hidden; }
  .econ-landing [data-phase="before"] .hero { grid-template-columns: minmax(0, 1fr); gap: 24px; }
}
/* Without JavaScript no phase ever arrives, so the place held for the
   rail above would stay blank for good. */
@media (max-width: 991.98px) and (scripting: none) {
  .econ-landing .rail { display: none; }
}
/* From 768 to 991.98px the stacked toy stops at 480px, so each row's Y
   stays near its inputs. Under 768px everything in the column runs full
   width, 로그인 included, and the toy goes with it. */
@media (min-width: 768px) and (max-width: 991.98px) {
  .econ-landing .toycol { max-width: 480px; }
}
/* ── phone ────────────────────────────────────────────────────────── */
/* This tier follows CTFd's .container, not the device: the container is
   540px wide from 576 to 767.98px (Bootstrap's md step) and the window's
   width below that, so under 768px the page has a phone's width to work
   in whatever the window is (a 1366 laptop snapped to half, or at 200%
   zoom, is 683px). */
@media (max-width: 767.98px) {
  .econ-landing .wrap { padding-inline: 16px; }
  .econ-landing .band { padding-block: 16px; }
  .econ-landing .body { padding-top: 16px; padding-bottom: 20px; }
  .econ-landing .hero { grid-template-columns: minmax(0, 1fr); gap: 18px; align-items: start; --lclock: clamp(46px, 15cqi, 64px); }
  .econ-landing .lede { font-size: 13.5px; margin-bottom: 14px; }
  .econ-landing .au-inline .lbtn.xl { width: 100%; min-width: 0; }
  .econ-landing .topline { flex-wrap: wrap; row-gap: 8px; font-size: 12px; margin-bottom: 12px; }
  .econ-landing .strip { grid-template-columns: repeat(2, 1fr); }
  .econ-landing .strip > div:nth-child(3) { border-left: 0; border-top: 1px solid var(--d-border); }
  .econ-landing .strip > div:nth-child(4) { border-top: 1px solid var(--d-border); }
  .econ-landing .steps, .econ-landing .facts, .econ-landing .two { grid-template-columns: minmax(0, 1fr); }
  .econ-landing .facts > div { border-left: 0; }
  .econ-landing .facts > div:nth-child(n+2) { border-top: 1px solid var(--d-border); }
  .econ-landing .segs { grid-template-columns: 52px var(--rounds) 52px; gap: 0 4px; }
  .econ-landing .seg .cap { font-size: 12px; }
}
/* Under 576px the window itself is the container, and the topline keeps
   only the wordmark and the chip. From 576px (a laptop at 200% zoom) the
   540px container has room for the product line, wrapped. */
@media (max-width: 575.98px) {
  .econ-landing .topline .prod, .econ-landing .topline .sep, .econ-landing .topline .camp { display: none; }
}
/* the narrow toy: the case column and the input columns give way first */
@media (max-width: 520px) {
  .econ-landing .toy .r { grid-template-columns: 64px 22px 22px 22px minmax(0,1fr) 14px; gap: 6px; padding: 0 9px; }
}

/* ── motion ───────────────────────────────────────────────────────── */
@keyframes lp-pulse { 0% { opacity: .7; transform: scale(.85); } 70% { opacity: 0; transform: scale(1.9); } 100% { opacity: 0; transform: scale(1.9); } }
@keyframes lp-fill { from { width: 0; } }
@keyframes lp-rise { from { opacity: 0; transform: translateY(7px); } to { opacity: 1; transform: none; } }
@keyframes lp-draw { from { stroke-dashoffset: 24; } to { stroke-dashoffset: 0; } }
/* Only movement stops. Animations and transitions go, and the press nudge
   with them, but a transform that is a state stays: the open disclosure
   keeps its flipped chevron. */
@media (prefers-reduced-motion: reduce) {
  .econ-landing *, .econ-landing *::before, .econ-landing *::after { animation: none !important; transition: none !important; }
  .econ-landing .lbtn:active { transform: none; }
  .econ-landing .chip i.live::after { display: none; }
}
</style>

<div class="econ-landing" id="econ-landing" lang="ko">
  <div class="stage">
    <p class="vh" id="econ-landing-status" role="status" aria-live="polite"></p>
    <div class="band">
      <div class="wrap">
        <div class="topline">
          <h1 class="wordmark"><i></i>E-CON 논설 <span class="prod">디지털 논리회로 설계 자동채점</span></h1>
          <span class="sep" aria-hidden="true">/</span>
          <span class="camp">SNU SENS · 2026 하계 공학 캠프</span>
          <span class="grow"></span>
          <span class="chip ph pending">연결 중</span>
          <span class="chip ph before"><svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>시작 전</span>
          <span class="chip accent ph round1"><i class="live"></i>1라운드 진행 중 · 70분</span>
          <span class="chip warn ph round1 tail"><i class="live"></i>1라운드 · 곧 마감</span>
          <span class="chip ph break"><svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><path d="M10 4H6v16h4zM18 4h-4v16h4z"/></svg>휴식 시간</span>
          <span class="chip accent ph round2"><i class="live"></i>2라운드 진행 중 · 80분</span>
          <span class="chip warn ph round2 tail"><i class="live"></i>2라운드 · 곧 마감</span>
          <span class="chip ph finished"><svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><polyline points="16 9 11 15 8 12"/></svg>온라인 라운드 종료</span>
          <span class="chip ph open"><svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><path d="M12 3l9 4.5-9 4.5-9-4.5z"/><path d="M3 12l9 4.5 9-4.5"/></svg>준비 모드</span>
          <span class="chip bad ph misconfigured"><svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/></svg>일정 설정 필요</span>
        </div>

        <div class="hero">
          <div class="clockcol">
            <div class="clabel" id="econ-landing-clabel">
              <span class="ph pending">대회 시간</span>
              <span class="ph before">1라운드 시작까지 남은 시간</span>
              <span class="ph round1">1라운드 남은 시간</span>
              <span class="ph break">2라운드 시작까지 남은 시간</span>
              <span class="ph round2">2라운드 남은 시간</span>
              <span class="ph finished">온라인 채점 종료</span>
              <span class="ph open">준비 모드 · 상시 개방</span>
              <span class="ph misconfigured">일정 설정에 오류가 있습니다</span>
            </div>
            <div class="clock" id="econ-landing-clock" role="timer" aria-labelledby="econ-landing-clabel"><span class="hh" data-econ-clock="hh"></span><span class="mm" data-econ-clock="mm">--</span><span class="ss" data-econ-clock="ss">:--</span></div>
            <h2 class="bhead">기다리는 동안, 채점기를 눌러보세요.</h2>
            <div class="au-inline">
              <p class="lede">1라운드는 <b>70분 동안 8문제&nbsp;·&nbsp;35점</b>입니다. 시작 시간이 되면 이 화면이 스스로 바뀝니다. 맛보기 표에서 답을 고르고 <b>채점</b>을 누르면 결과가 어떻게 나오는지 미리 볼 수 있습니다.</p>
              <div class="au out">
                <a class="lbtn primary xl" href="/login">로그인<svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6"/></svg></a>
                <div class="hint">계정은 캠프 운영진에게 받습니다. 조 계정 하나로 함께 제출합니다.</div>
              </div>
              <div class="au in wait">
                <div class="quiet"><svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg><span><b><span data-econ-user></span>로그인되어 있습니다.</b> 1라운드가 시작되면 도전 과제로 가는 버튼이 생깁니다.</span></div>
              </div>
            </div>
            <div class="ruler-wrap">
              <div class="ruler" aria-hidden="true"><i class="base"></i><i class="el"></i></div>
              <div class="rlabels"><span>0분</span><span><b data-econ-clock="elapsed"></b> · 눈금 1칸 1분</span><span data-econ-clock="total"><span class="ph round1">70분</span><span class="ph round2">80분</span></span></div>
            </div>
            <div class="rnote ph break">휴식 시간 · 2라운드를 준비하세요.</div>
            <div class="rnote ph open">준비 모드 · 대회 시작 시각이 설정되지 않았습니다.</div>
            <div class="rnote ph misconfigured">일정 설정 필요 · 운영진에게 알려주세요.</div>
          </div>

          <div class="toycol">
            <form class="toy">
              <div class="toyhd"><b>맛보기 · 다수결 회로</b><span class="grow"></span><span class="chip mono">3입력 · 8케이스</span></div>
              <div class="toyq">입력 <code>A</code> <code>B</code> <code>C</code> 중 <code>1</code>이 <b>두 개 이상</b>이면 <code>Y=1</code>, 아니면 <code>Y=0</code>입니다. 각 줄의 <code>Y</code>를 고르세요. 앞의 두 줄은 예시로 채워두었습니다.</div>
              <input class="v i0 o0" type="radio" name="econ-toy-q0" id="econ-toy-q0a" checked aria-label="case_000 · A=0 B=0 C=0 · Y를 0으로">
              <input class="v i0 o1" type="radio" name="econ-toy-q0" id="econ-toy-q0b" aria-label="case_000 · A=0 B=0 C=0 · Y를 1로">
              <input class="v i1 o0" type="radio" name="econ-toy-q1" id="econ-toy-q1a" checked aria-label="case_001 · A=0 B=0 C=1 · Y를 0으로">
              <input class="v i1 o1" type="radio" name="econ-toy-q1" id="econ-toy-q1b" aria-label="case_001 · A=0 B=0 C=1 · Y를 1로">
              <input class="v i2 o0" type="radio" name="econ-toy-q2" id="econ-toy-q2a" aria-label="case_002 · A=0 B=1 C=0 · Y를 0으로">
              <input class="v i2 o1" type="radio" name="econ-toy-q2" id="econ-toy-q2b" aria-label="case_002 · A=0 B=1 C=0 · Y를 1로">
              <input class="v i3 o0" type="radio" name="econ-toy-q3" id="econ-toy-q3a" aria-label="case_003 · A=0 B=1 C=1 · Y를 0으로">
              <input class="v i3 o1" type="radio" name="econ-toy-q3" id="econ-toy-q3b" aria-label="case_003 · A=0 B=1 C=1 · Y를 1로">
              <input class="v i4 o0" type="radio" name="econ-toy-q4" id="econ-toy-q4a" aria-label="case_004 · A=1 B=0 C=0 · Y를 0으로">
              <input class="v i4 o1" type="radio" name="econ-toy-q4" id="econ-toy-q4b" aria-label="case_004 · A=1 B=0 C=0 · Y를 1로">
              <input class="v i5 o0" type="radio" name="econ-toy-q5" id="econ-toy-q5a" aria-label="case_005 · A=1 B=0 C=1 · Y를 0으로">
              <input class="v i5 o1" type="radio" name="econ-toy-q5" id="econ-toy-q5b" aria-label="case_005 · A=1 B=0 C=1 · Y를 1로">
              <input class="v i6 o0" type="radio" name="econ-toy-q6" id="econ-toy-q6a" aria-label="case_006 · A=1 B=1 C=0 · Y를 0으로">
              <input class="v i6 o1" type="radio" name="econ-toy-q6" id="econ-toy-q6b" aria-label="case_006 · A=1 B=1 C=0 · Y를 1로">
              <input class="v i7 o0" type="radio" name="econ-toy-q7" id="econ-toy-q7a" aria-label="case_007 · A=1 B=1 C=1 · Y를 0으로">
              <input class="v i7 o1" type="radio" name="econ-toy-q7" id="econ-toy-q7b" aria-label="case_007 · A=1 B=1 C=1 · Y를 1로">
              <input class="run" type="checkbox" id="econ-toy-run" aria-label="채점하기">
              <div class="tb">
                <div class="r hd"><span class="cs">테스트</span><span class="ab">A</span><span class="ab">B</span><span class="ab">C</span><span class="yh">Y</span><span class="mk"></span></div>
                <div class="r r0"><span class="cs">case_000</span><span class="ab">0</span><span class="ab">0</span><span class="ab">0</span><span class="rad" aria-hidden="true"><label class="l0" for="econ-toy-q0a">0</label><label class="l1" for="econ-toy-q0b">1</label></span><span class="mk"></span></div>
                <div class="r r1"><span class="cs">case_001</span><span class="ab">0</span><span class="ab">0</span><span class="ab">1</span><span class="rad" aria-hidden="true"><label class="l0" for="econ-toy-q1a">0</label><label class="l1" for="econ-toy-q1b">1</label></span><span class="mk"></span></div>
                <div class="r r2"><span class="cs">case_002</span><span class="ab">0</span><span class="ab">1</span><span class="ab">0</span><span class="rad" aria-hidden="true"><label class="l0" for="econ-toy-q2a">0</label><label class="l1" for="econ-toy-q2b">1</label></span><span class="mk"></span></div>
                <div class="r r3"><span class="cs">case_003</span><span class="ab">0</span><span class="ab">1</span><span class="ab">1</span><span class="rad" aria-hidden="true"><label class="l0" for="econ-toy-q3a">0</label><label class="l1" for="econ-toy-q3b">1</label></span><span class="mk"></span></div>
                <div class="r r4"><span class="cs">case_004</span><span class="ab">1</span><span class="ab">0</span><span class="ab">0</span><span class="rad" aria-hidden="true"><label class="l0" for="econ-toy-q4a">0</label><label class="l1" for="econ-toy-q4b">1</label></span><span class="mk"></span></div>
                <div class="r r5"><span class="cs">case_005</span><span class="ab">1</span><span class="ab">0</span><span class="ab">1</span><span class="rad" aria-hidden="true"><label class="l0" for="econ-toy-q5a">0</label><label class="l1" for="econ-toy-q5b">1</label></span><span class="mk"></span></div>
                <div class="r r6"><span class="cs">case_006</span><span class="ab">1</span><span class="ab">1</span><span class="ab">0</span><span class="rad" aria-hidden="true"><label class="l0" for="econ-toy-q6a">0</label><label class="l1" for="econ-toy-q6b">1</label></span><span class="mk"></span></div>
                <div class="r r7"><span class="cs">case_007</span><span class="ab">1</span><span class="ab">1</span><span class="ab">1</span><span class="rad" aria-hidden="true"><label class="l0" for="econ-toy-q7a">0</label><label class="l1" for="econ-toy-q7b">1</label></span><span class="mk"></span></div>
              </div>
              <div class="tf">
                <span class="cnt live">답변 <b></b> / 8</span>
                <label class="lbtn primary" for="econ-toy-run" data-toy-run><svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><path d="M8 5.5v13a1 1 0 0 0 1.5.9l10-6.5a1 1 0 0 0 0-1.7l-10-6.5A1 1 0 0 0 8 5.5z" fill="currentColor" stroke="none"/></svg><span class="t-run">채점</span><span class="t-edit">다시 풀기</span></label>
              </div>
              <div class="toyfoot"><svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg><span>이 표는 브라우저 안에서 CSS만으로 채점됩니다. 서버로 아무것도 보내지 않고, 대회 문제도 아닙니다.</span></div>
              <div class="vwrap" role="status">
                <div class="vp partial">
                  <div class="vhead"><span class="ico"><svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><path d="M12 5v9M12 18.5h.01"/></svg></span><span class="chip warn">부분 통과</span></div>
                  <h3><span class="okn live"></span> / 8 테스트케이스 통과</h3>
                  <p>여기서는 틀린 줄에 <b>✗</b>가 붙고, 고르지 않은 줄도 통과로 치지 않습니다. 대회의 회로 문제는 통과한 개수만 알려주고, 어느 케이스가 틀렸는지는 알려주지 않습니다. 모두 통과해야 점수가 붙고, 라운드가 열려 있는 동안에는 몇 번이든 다시 제출할 수 있습니다.</p>
                  <div class="vact"><button class="lbtn sm" type="reset">처음부터</button></div>
                </div>
                <div class="vp fail">
                  <div class="vhead"><span class="ico"><svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg></span><span class="chip bad">실패</span></div>
                  <h3>0 / 8 테스트케이스 통과</h3>
                  <p>여기서는 틀린 줄에 <b>✗</b>가 붙고, 고르지 않은 줄도 통과로 치지 않습니다. 대회의 회로 문제는 통과한 개수만 알려주고, 어느 케이스가 틀렸는지는 알려주지 않습니다. 모두 통과해야 점수가 붙고, 라운드가 열려 있는 동안에는 몇 번이든 다시 제출할 수 있습니다.</p>
                  <div class="vact"><button class="lbtn sm" type="reset">처음부터</button></div>
                </div>
                <div class="vp pass">
                  <div class="vhead"><span class="ico"><svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg></span><span class="chip ok">전체 통과</span></div>
                  <h3>8 / 8 테스트케이스 통과</h3>
                  <p>대회의 회로 문제는 <b>여러분이 그린 회로</b>를 숨겨진 테스트로 돌려, 통과한 개수만 알려줍니다.</p>
                  <div class="vact"><a class="lbtn sm po" href="/login">로그인하고 기다리기</a><button class="lbtn sm" type="reset">처음부터</button></div>
                </div>
              </div>
            </form>
          </div>

          <div class="actcol">
            <p class="lede ph pending">대회 시간을 불러오는 중입니다. 이 화면이 계속되면 새로 고침해 보세요.</p>
            <p class="lede ph round1">지금 <b>1라운드가 진행 중</b>입니다. 제출 폴더를 올리면 잠시 뒤 <b>N&nbsp;/&nbsp;M 테스트케이스 통과</b>로 답합니다.</p>
            <p class="lede ph break">1라운드가 닫혔습니다. <b>2라운드는 80분 동안 7문제&nbsp;·&nbsp;45점</b>입니다. 쉬는 동안 받은 점수를 확인하세요.</p>
            <p class="lede ph round2">지금 <b>2라운드가 진행 중</b>입니다. 반가산기부터 <span class="nw">7-segment</span> 출력기까지 7문제&nbsp;·&nbsp;45점입니다.</p>
            <p class="lede ph round1 tail">1라운드가 <b>5분 안에 마감</b>됩니다. 아직 제출하지 않은 회로가 있다면 지금 올리세요.</p>
            <p class="lede ph round2 tail">2라운드가 <b>5분 안에 마감</b>됩니다. 아직 제출하지 않은 회로가 있다면 지금 올리세요.</p>
            <p class="lede ph finished">온라인 라운드는 끝났습니다. 받은 점수는 <b>내 점수</b>에서 확인할 수 있습니다.</p>
            <p class="lede ph open">운영 검토를 위해 두 라운드가 모두 열려 있습니다. 참가자용 상태가 아닙니다.</p>
            <p class="lede ph misconfigured">대회 일정 설정에 오류가 있어 남은 시간을 계산할 수 없습니다. 문제는 열리지 않습니다.</p>
            <div class="au out">
              <a class="lbtn primary xl wide" href="/login">로그인<svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6"/></svg></a>
              <div class="hint">계정은 캠프 운영진에게 받습니다. 조 계정 하나로 함께 제출합니다.</div>
            </div>
            <div class="au in go">
              <a class="lbtn primary xl wide" href="/challenges">도전 과제로<svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6"/></svg></a>
              <div class="hint">답안 .dig 파일과 부품 .dig 파일을 한 폴더에 담아 제출합니다.</div>
            </div>
            <div class="au in score">
              <a class="lbtn primary xl wide" href="/my-score">내 점수 보기<svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6"/></svg></a>
              <div class="hint">라운드가 닫혀도 받은 점수는 온라인 총점에 계속 남습니다.</div>
            </div>
            <div class="au in none">
              <div class="quiet"><svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/></svg><span><b>새로 고침하지 않아도 됩니다.</b> 설정이 바로잡히면 이 화면이 스스로 바뀝니다.</span></div>
            </div>
          </div>
        </div>

        <div class="rail">
          <ol class="segs">
            <li class="seg"><div class="track"><i></i></div><span class="cap"><b class="here">지금</b>시작 전</span></li>
            <li class="seg"><div class="track"><i></i></div><span class="cap"><b class="here">지금</b>1라운드 · 70분 · 8문제 35점</span></li>
            <li class="seg"><div class="track"><i></i></div><span class="cap"><b class="here">지금</b>휴식</span></li>
            <li class="seg"><div class="track"><i></i></div><span class="cap"><b class="here">지금</b>2라운드 · 80분 · 7문제 45점</span></li>
            <li class="seg"><div class="track"><i></i></div><span class="cap"><b class="here">지금</b>종료</span></li>
          </ol>
        </div>
      </div>
    </div>

    <div class="wrap body">
      <div class="strip">
        <div><small>현재 단계</small><b><span class="ph pending">연결 중</span><span class="ph before">시작 전</span><span class="ph round1">1라운드</span><span class="ph break">휴식 시간</span><span class="ph round2">2라운드</span><span class="ph finished">종료</span><span class="ph open">준비 모드</span><span class="ph misconfigured">설정 필요</span></b></div>
        <div><small><span class="ph pending">라운드</span><span class="ph before">다음 라운드</span><span class="ph round1">이 라운드</span><span class="ph break">다음 라운드</span><span class="ph round2">이 라운드</span><span class="ph finished">온라인 라운드</span><span class="ph open">라운드</span><span class="ph misconfigured">라운드</span></small><b><span class="ph pending">1&nbsp;·&nbsp;2라운드</span><span class="ph before">8문제 <span class="den">· 35점</span></span><span class="ph round1">8문제 <span class="den">· 35점</span></span><span class="ph break">7문제 <span class="den">· 45점</span></span><span class="ph round2">7문제 <span class="den">· 45점</span></span><span class="ph finished">1&nbsp;·&nbsp;2라운드 <span class="den">· 종료</span></span><span class="ph open">1&nbsp;·&nbsp;2라운드 <span class="den">· 개방</span></span><span class="ph misconfigured">1&nbsp;·&nbsp;2라운드 <span class="den">· 미개방</span></span></b></div>
        <div><small>온라인 전체</small><b>15문제 <span class="den">· 80점</span></b></div>
        <div><small>파일 한도</small><b>256 <span class="den">KB / 파일</span></b></div>
      </div>

      <div class="sech"><h2>여기서 하는 일</h2><span>설계 → 업로드 → 채점</span></div>
      <div class="steps">
        <div class="step">
          <div class="sh"><svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M8 9h3v6H8zM11 12h5M16 10v4"/></svg><b>회로를 설계합니다</b><span class="n">01</span></div>
          <p>Digital 시뮬레이터에서 게이트를 놓고 선을 잇습니다. 조합논리 회로만 사용합니다 — 클럭도 플립플롭도 없습니다.</p>
        </div>
        <div class="step">
          <div class="sh"><svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M12 11v6M9 14h6"/></svg><b>폴더를 통째로 올립니다</b><span class="n">02</span></div>
          <p>답안 <code>.dig</code> 파일과 직접 만든 부품 <code>.dig</code> 파일을 한 폴더에 담아 그대로 제출합니다. 폴더 이름은 자유입니다.</p>
        </div>
        <div class="step">
          <div class="sh"><svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg><b>채점 결과를 받습니다</b><span class="n">03</span></div>
          <p><code>Digital.jar v0.31</code>이 숨겨진 테스트를 돌리고 <b class="nw">N&nbsp;/&nbsp;M 테스트케이스 통과</b>로 답합니다. 회로 문제는 라운드가 열려 있는 동안 다시 제출할 수 있습니다.</p>
        </div>
      </div>

      <div class="sech"><h2>제출과 점수</h2><span>제출 전에 알아 둘 두 가지</span></div>
      <div class="two">
        <div class="panel">
          <div class="ch"><svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg><b>제출 폴더</b><span class="chip">폴더 1개</span></div>
          <p>답안 <code>.dig</code>와 직접 만든 부품 <code>.dig</code>를 한 폴더에 담아 통째로 올립니다.</p>
          <div class="fig">
            <div class="figcap"><span>제출 폴더의 예</span><span>5.9 KB · 2개 .dig 파일</span></div>
            <div class="tree">
              <div><span class="ln">우리조_1-2번_제출/</span><em>← 폴더 이름은 자유</em></div>
              <div><span class="ln">├─ <b>1-2번_전가산기(Full Adder)만들기.dig</b></span><em>← 답안</em></div>
              <div><span class="ln">└─ <b>1-1번_반가산기(Half Adder)만들기.dig</b></span><em>← 사용한 부품</em></div>
            </div>
          </div>
          <p>답안 파일은 받은 이름 그대로 두세요. 채점기는 그 이름으로 답안을 찾습니다.</p>
          <div class="lim"><b>한도</b> 파일 하나 256 KB · 한 폴더에 <span class="mono">.dig</span> 32개 · 폴더 전체 1 MB</div>
        </div>
        <div class="panel">
          <div class="ch"><svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><path d="M12 20V10M18 20V4M6 20v-4"/></svg><b>점수</b><span class="chip">80점 만점</span></div>
          <table class="tbl">
            <tr><th>라운드</th><th>시간</th><th>문제</th><th>배점</th></tr>
            <tr><td>1라운드</td><td>70분</td><td>8문제</td><td>35점</td></tr>
            <tr><td>2라운드</td><td>80분</td><td>7문제</td><td>45점</td></tr>
            <tr><td>온라인 합계</td><td>150분</td><td>15문제</td><td>80점</td></tr>
          </table>
          <div class="rule">점수는 <b>전체 통과에만</b> 붙습니다. 부분 통과는 0점이므로, 남은 시간에는 반쯤 맞은 회로를 끝까지 고치는 편이 낫습니다.</div>
          <div class="lim"><b>배점</b> 1라운드 2+2+3+3+2+3+10+10 · 2라운드 6+6+8+2+8+10+5. 한 번 받은 점수는 라운드가 닫힌 뒤에도 총점에 남습니다.</div>
        </div>
      </div>

      <div class="sech"><h2>무엇이 채점을 깨나</h2><span>회로가 맞아도 통과하지 못하는 경우</span></div>
      <div class="panel">
        <ul class="fails">
          <li><span class="x"><svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg></span><span><b>부품 <code>.dig</code> 누락</b> — 직접 만든 부품을 함께 올리지 않으면 회로가 열리지 않습니다.</span></li>
          <li><span class="x"><svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg></span><span><b>답안 파일 이름 변경</b> — 폴더 안에서 이 문제의 답안을 찾지 못합니다.</span></li>
          <li><span class="x"><svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg></span><span><b>단자 이름 변경</b> — <code>Y</code>를 <code>out</code>으로 바꾸면 테스트가 출력을 찾지 못합니다.</span></li>
          <li><span class="x"><svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg></span><span><b>입력이 3개 이상인 게이트</b> — AND·OR·NAND·NOR·XOR·XNOR는 모든 문제에서 2입력만 쓸 수 있습니다. 채점기는 고른 폴더의 <code>.dig</code>를 모두 검사하므로, 다른 문제의 파일에 하나만 있어도 거부됩니다.</span></li>
          <li><span class="x"><svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg></span><span><b>형식·한도</b> — 폴더에는 <code>.dig</code> 파일만, 하위 폴더 없이 담습니다. 파일 하나 256 KB, 한 폴더에 <code>.dig</code> 32개 · 1 MB를 넘기면 올라가지 않습니다.</span></li>
          <li><span class="x"><svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg></span><span><b>회로가 너무 크거나 멈춤</b> — 채점은 45초에서 끊깁니다.</span></li>
        </ul>
        <details class="raw" open><summary>문제 화면에 실제로 뜨는 문장<svg class="ic" aria-hidden="true" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg></summary>
          <div class="t"><span><i>결과 카드</i>회로에서 사용한 부품 파일을 찾을 수 없습니다. 필요한 부품 .dig 파일이 같은 라운드 폴더 안에 있는지 확인한 뒤 폴더를 다시 선택해 제출해주세요.</span><span><i>폴더를 고를 때</i>선택한 폴더에 이 문제의 답안 파일(3번_입력이3개인AND게이트.dig)이 없습니다.</span><span><i>결과 카드</i>문제에서 정한 입력·출력 단자 이름을 회로에서 찾을 수 없습니다. 단자 이름(Label)을 문제지와 똑같이 맞춰주세요.</span><span><i>결과 카드</i>입력이 2개보다 많은 논리 게이트는 사용할 수 없습니다.</span><span><i>결과 카드</i>채점 시간이 초과되었습니다 (회로가 너무 크거나 멈춰 있을 수 있어요). 회로를 점검한 뒤 다시 제출해주세요.</span></div>
        </details>
      </div>

      <div class="sech"><h2>그 밖의 규칙</h2><span>참가 방식과 제출 기회</span></div>
      <dl class="facts">
        <div><dt>참가</dt><dd>4개 조 · 조당 약 3명<small>조 계정 하나로 함께 제출합니다</small></dd></div>
        <div><dt>진리표 문제</dt><dd>#01 · 제출 기회 1회<small>한 번 제출하면 답안을 바꿀 수 없습니다</small></dd></div>
        <div><dt>다시 제출</dt><dd>회로 문제는 라운드가 열려 있는 동안 몇 번이든<small>틀려도 감점은 없습니다</small></dd></div>
      </dl>

      <div class="foot">
        <span class="mono">CTFd 3.8.5 · Digital.jar v0.31</span>
        <span>순위표는 공개하지 않습니다. 점수는 도전 과제와 내 점수 화면에서 확인합니다.</span>
        <span class="end">SNU SENS 2026 하계 공학 캠프 · E-CON 논설</span>
      </div>
    </div>
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
        # "configuration as code" pattern as the configs block above, so an
        # edit made in CTFd's admin Pages screen lasts only until the next
        # boot. HTML format: INDEX_CONTENT is a <style> block plus indented
        # markup that CTFd must serve verbatim, not run through markdown.
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
