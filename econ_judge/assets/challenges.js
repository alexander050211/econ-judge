/* s2 challenges list — JS-injected layer over CTFd's stock /challenges page.
 *
 * Why JS-inject instead of overriding the Jinja template:
 *   CTFd's stock challenges.html loads challenges.<hash>.js, which defines
 *   the Alpine `ChallengeBoard` component used by the challenge modal. If we
 *   override the template we lose that script (we'd have to hardcode the
 *   Vite content hash, which changes on CTFd updates). Keeping the stock
 *   template loaded preserves the modal flow unchanged; this script just
 *   adds the s2 markup and hides the stock category grid.
 *
 *   Rows navigate to econ-judge's stable /problems/<id> routes. The stock
 *   Alpine modal remains in the DOM for CTFd compatibility but is not used by
 *   the participant-facing flow.
 *
 * Gated by window.location.pathname === '/challenges' so it's safe to load
 * the script on every page via THEME_HEADER_CSS. */
(function () {
  "use strict";

  if (window.location.pathname !== "/challenges") return;

  const CATEGORY_ORDER = {
    "1라운드": { idx: 0, label: "1라운드", sub: "70분 · 35점 · 8문제" },
    "2라운드": { idx: 1, label: "2라운드", sub: "80분 · 45점 · 7문제" },
  };

  /* The phases where a round is actually running. Anything else — including a
     phase name a newer server invents — gets the standalone banner, which is
     the safe side: the board never claims a round is live on its own. */
  const RUNNING_PHASES = ["round1", "round2"];

  function phaseText(phase) {
    return {
      before: "시작 전 · 문제는 시작 시간에 열립니다.",
      round1: "1라운드 진행 중 · 70분",
      break: "휴식 시간 · 2라운드를 준비하세요.",
      round2: "2라운드 진행 중 · 80분",
      finished: "온라인 라운드가 종료되었습니다.",
      open: "준비 모드 · 운영 검토를 위해 두 라운드가 모두 열려 있습니다.",
      misconfigured: "일정 설정이 필요합니다.",
    }[phase] || "현재 라운드 상태를 확인하는 중입니다.";
  }

  /* Why the locked round says something different from the banner: the banner
     explains the clock, this explains what the greyed table in front of you
     is waiting for. */
  function lockedNote(phase) {
    return {
      before: "시작 시간에 열립니다",
      round1: "1라운드가 끝나면 열립니다",
      break: "휴식 시간에는 제출할 수 없습니다",
      round2: "2라운드 종료 후 다시 열립니다",
      finished: "제출이 마감되었습니다",
    }[phase] || "지금은 제출할 수 없습니다";
  }

  /* Inject our CSS + markup container once. We hide the stock jumbotron
     (the "챌린지" h1 strip) and the stock category grid+spinner, but keep
     the Alpine modal `#challenge-window` intact. */
  function injectShell() {
    if (document.getElementById("s2-style")) return;

    const style = document.createElement("style");
    style.id = "s2-style";
    style.textContent = `
/* Hide CTFd's stock jumbotron + category grid + loading spinner on the
   challenges page. The Alpine modal #challenge-window stays untouched. */
body[data-route="challenges"] main > .jumbotron,
main > .jumbotron:has(+ .container [x-data="ChallengeBoard"]),
[x-data="ChallengeBoard"] [x-show="loaded"],
[x-data="ChallengeBoard"] [x-show="!loaded"] {
  display: none !important;
}
/* Fallback selector for browsers without :has() — use a JS-applied class. */
.s2-hide-stock { display: none !important; }

/* ─── s2 board ───
   Cool page, one white hairline card per round, 48px rows. Salience rule:
   the board carries exactly one filled blue button (.s2-row-next, the first
   unsolved problem of the open round) so "what do I do next" is one glance.
   Every colour is a shared --d-* token, so CTFd's dark toggle swaps this
   screen with the rest of the theme. */
.s2-wrap {
  padding: 24px 0 40px;
  display: flex;
  flex-direction: column;
  gap: 20px;
  font-family: var(--d-f-sans);
  color: var(--d-ink);
}
.s2-wrap *, .s2-wrap *::before, .s2-wrap *::after { box-sizing: border-box; }
.s2-wrap svg { width: 14px; height: 14px; flex: none; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }

.s2-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 24px; }
.s2-head-l { min-width: 0; }
.s2-h1 {
  margin: 0;
  font-size: 28px;
  font-weight: 600;
  line-height: 1.2;
  letter-spacing: -0.015em;
  color: var(--d-ink);
}
/* Korean leads and the English word trails it as a quiet gloss — B drops the
   uppercase mono kickers the old board used above every heading. */
.s2-h1-en {
  margin-left: 10px;
  font-size: 13px;
  font-weight: 400;
  letter-spacing: 0;
  color: var(--d-text-3);
  vertical-align: 2px;
}
.s2-head-sub { margin: 6px 0 0; font-size: 14px; line-height: 1.5; color: var(--d-text-2); }
.s2-head-sub b { font-weight: 500; color: var(--d-ink); }
/* The phase line lives here while a round runs and in #s2-round-status
   otherwise — render() shows exactly one of the two, so the board never
   prints the same sentence twice. (The navbar countdown is round-ui.js's.) */
.s2-head-phase[hidden] { display: none; }
.s2-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 36px;
  padding: 0 14px;
  border: 1px solid var(--d-border-strong);
  border-radius: 6px;
  background: var(--d-surface);
  color: var(--d-ink);
  box-shadow: var(--d-shadow-1);
  font-family: inherit;
  font-size: 14px;
  font-weight: 500;
  white-space: nowrap;
  text-decoration: none;
  transition: background 0.15s var(--d-ease), border-color 0.15s var(--d-ease);
}
.s2-btn:hover { background: var(--d-surface-2); color: var(--d-ink); text-decoration: none; }
.s2-btn:focus-visible { outline: none; border-color: var(--d-accent); box-shadow: 0 0 0 3px var(--d-focus); }

.s2-progress {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  background: var(--d-surface);
  border: 1px solid var(--d-border);
  border-radius: 10px;
  box-shadow: var(--d-shadow-1);
}
.s2-prog-cell { padding: 14px 20px; border-left: 1px solid var(--d-border); min-width: 0; }
.s2-prog-cell:first-child { border-left: 0; }
.s2-prog-label { display: block; font-size: 12.5px; color: var(--d-text-2); margin-bottom: 4px; }
.s2-prog-val {
  display: block;
  font-size: 22px;
  font-weight: 600;
  line-height: 1.2;
  letter-spacing: -0.01em;
  color: var(--d-ink);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* The amber phosphor glow THEME_HEADER_CSS puts on dark-mode hero numbers is
   Direction D's CRT conceit; B has no amber on this screen. */
:root[data-bs-theme="dark"] .s2-prog-val { text-shadow: none; }
.s2-prog-mut { color: var(--d-text-3); font-weight: 500; }
.s2-prog-row { display: flex; align-items: center; gap: 10px; height: 26px; }
.s2-prog-track {
  flex: 1;
  height: 6px;
  background: var(--d-surface-2);
  border-radius: 3px;
  overflow: hidden;
}
.s2-prog-fill {
  display: block;
  height: 100%;
  background: var(--d-accent);
  border-radius: inherit;
  transition: width 0.22s var(--d-ease);
}
.s2-prog-bar-meta { font-size: 14px; font-weight: 600; color: var(--d-ink); font-variant-numeric: tabular-nums; }

/* Standalone banner: only for the phases where no round is running. */
.s2-round-status {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 40px;
  padding: 9px 14px;
  border-radius: 8px;
  background: var(--d-accent-soft);
  color: var(--d-accent-text);
  font-size: 13.5px;
  font-weight: 500;
}
.s2-round-status svg { width: 16px; height: 16px; }
.s2-round-status-quiet { background: var(--d-surface-2); color: var(--d-text-2); }
.s2-round-status-alert { background: var(--d-bad-soft); color: var(--d-bad-text); }
.s2-round-status[hidden] { display: none; }

.s2-sections { display: flex; flex-direction: column; gap: 28px; }
.s2-cat { display: flex; flex-direction: column; gap: 12px; }
.s2-cat-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.s2-cat-title { margin: 0; font-size: 18px; font-weight: 600; letter-spacing: -0.01em; color: var(--d-ink); }
.s2-cat-sub { font-size: 13.5px; color: var(--d-text-2); }
.s2-cat-lock { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: var(--d-text-2); }
.s2-cat-lock svg { width: 14px; height: 14px; }
.s2-cat-stat { margin-left: auto; font-size: 13.5px; color: var(--d-text-2); font-variant-numeric: tabular-nums; }
.s2-cat-frac { color: var(--d-ink); font-weight: 500; }

.s2-tbl {
  width: 100%;
  table-layout: fixed;
  border-collapse: separate;
  border-spacing: 0;
  background: var(--d-surface);
  border: 1px solid var(--d-border);
  border-radius: 10px;
  box-shadow: var(--d-shadow-1);
  overflow: hidden;
}
.s2-row { transition: background 0.15s var(--d-ease); cursor: pointer; }
.s2-row:hover { background: var(--d-surface-2); }
.s2-row:focus-visible { outline: 2px solid var(--d-accent); outline-offset: -2px; }
.s2-row td {
  height: 48px;
  padding: 0 6px;
  vertical-align: middle;
  font-size: 14.5px;
  color: var(--d-ink);
}
.s2-row td:first-child { padding-left: 16px; }
.s2-row td:last-child { padding-right: 14px; }
.s2-row + .s2-row td { border-top: 1px solid var(--d-border); }
.s2-td-id { width: 52px; }
.s2-td-pts { width: 72px; text-align: right; }
.s2-td-status { width: 118px; }
.s2-td-action { width: 116px; text-align: right; }
.s2-id-badge { font-family: var(--d-f-mono); font-size: 12.5px; letter-spacing: -0.01em; color: var(--d-text-3); }
.s2-name { font-size: 14.5px; font-weight: 500; color: var(--d-ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.s2-problem-link { color: inherit; text-decoration: none; }
.s2-problem-link:hover { color: inherit; text-decoration: none; }
.s2-pts { font-size: 13px; color: var(--d-text-2); font-variant-numeric: tabular-nums; }
.s2-pts-u { font-size: 13px; color: var(--d-text-2); margin-left: 3px; }
.s2-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 24px;
  padding: 0 8px;
  border-radius: 6px;
  background: var(--d-surface-2);
  color: var(--d-text-2);
  font-size: 12.5px;
  font-weight: 500;
  white-space: nowrap;
}
.s2-chip svg { width: 13px; height: 13px; stroke-width: 2.4; }
.s2-chip-pass { background: var(--d-ok-soft); color: var(--d-ok-text); }
.s2-action {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 5px;
  font-size: 13.5px;
  font-weight: 500;
  color: var(--d-text-2);
  white-space: nowrap;
  text-decoration: none;
  transition: color 0.15s var(--d-ease), background 0.15s var(--d-ease), border-color 0.15s var(--d-ease);
}
.s2-action:hover { color: var(--d-ink); text-decoration: none; }
.s2-action svg { width: 14px; height: 14px; }
/* Unsolved rows get a hairline button; the one next row (below) fills it in. */
.s2-action-btn {
  justify-content: center;
  height: 30px;
  padding: 0 10px;
  border: 1px solid var(--d-border-strong);
  border-radius: 6px;
  background: var(--d-surface);
  color: var(--d-ink);
}
.s2-action-btn:hover { background: var(--d-surface-2); color: var(--d-ink); }
.s2-action:focus-visible { outline: none; border-radius: 6px; box-shadow: 0 0 0 3px var(--d-focus); }

/* The single next problem: left rail, faint accent tint, the only filled
   button on the board. The chip flips to the card colour so it still reads
   against the tint. */
.s2-row-next { background: var(--d-accent-soft); }
.s2-row-next:hover { background: var(--d-accent-soft); }
.s2-row-next td:first-child { box-shadow: inset 3px 0 0 var(--d-accent); }
.s2-row-next .s2-name { font-weight: 600; }
.s2-row-next .s2-chip { background: var(--d-surface); color: var(--d-accent-text); }
.s2-row-next .s2-action-btn {
  background: var(--d-accent);
  border-color: var(--d-accent);
  color: var(--d-on-accent);
}
.s2-row-next .s2-action-btn:hover { background: var(--d-accent-hover); border-color: var(--d-accent-hover); color: var(--d-on-accent); }

/* A locked round is recoloured, never dimmed with opacity: mentees read these
   names to plan the next round, so every one of them has to stay legible. */
.s2-tbl-locked { background: var(--d-surface-2); box-shadow: none; }
.s2-tbl-locked .s2-row { cursor: default; }
.s2-tbl-locked .s2-row:hover { background: transparent; }
/* Every locked cell stops at --d-text-2, including the quiet #NN and points:
   this card's ground is --d-surface-2, and --d-text-3 only clears 4.5:1 on
   --d-surface. */
.s2-tbl-locked .s2-name, .s2-tbl-locked .s2-action, .s2-tbl-locked .s2-chip,
.s2-tbl-locked .s2-id-badge, .s2-tbl-locked .s2-pts, .s2-tbl-locked .s2-pts-u { color: var(--d-text-2); }
.s2-tbl-locked .s2-chip { background: transparent; border: 1px solid var(--d-border-strong); }

.s2-loading, .s2-empty, .s2-error {
  padding: 24px;
  text-align: center;
  font-size: 14px;
  color: var(--d-text-2);
  background: var(--d-surface);
  border: 1px solid var(--d-border);
  border-radius: 10px;
}
.s2-error { background: var(--d-bad-soft); border-color: var(--d-bad-line); color: var(--d-bad-text); }

/* 1366×768: nav 56 + 16 + head 61 + strip 68 + round head 26 + 8 rows × 44
   ≈ 613 — the whole of round 1 stays above the fold on a school laptop. */
@media (min-width: 1200px) and (max-height: 800px) {
  .s2-wrap { padding-top: 16px; gap: 14px; }
  .s2-head-sub { margin-top: 4px; }
  .s2-prog-cell { padding: 11px 20px; }
  .s2-sections { gap: 20px; }
  .s2-cat { gap: 10px; }
  .s2-row td { height: 44px; }
}
/* Phone: the whole row is the link, so the points and the action button give
   up their columns to the problem name — the status chip is what a mentee
   scans for on a small screen. */
@media (max-width: 720px) {
  .s2-progress { grid-template-columns: 1fr 1fr; }
  .s2-prog-cell:nth-child(3) { border-left: 0; }
  .s2-prog-cell:nth-child(n+3) { border-top: 1px solid var(--d-border); }
  .s2-head { flex-direction: column; align-items: flex-start; gap: 14px; }
  .s2-h1 { font-size: 24px; }
  .s2-td-id { width: 44px; }
  .s2-td-status { width: 104px; }
  .s2-td-pts, .s2-td-action { display: none; }
  .s2-row td:first-child { padding-left: 12px; }
  .s2-row .s2-td-status { padding-right: 12px; }
}
@media (prefers-reduced-motion: reduce) {
  .s2-wrap *, .s2-wrap *::before, .s2-wrap *::after { transition: none !important; animation: none !important; }
}
`;
    document.head.appendChild(style);

    /* Hide the stock jumbotron + grid via a class (covers older browsers
       lacking :has() support). Find the elements and apply directly. */
    document.querySelectorAll("main > .jumbotron").forEach(el => {
      el.classList.add("s2-hide-stock");
    });
    const board = document.querySelector('[x-data="ChallengeBoard"]');
    if (board) {
      board.querySelectorAll('[x-show="loaded"], [x-show="!loaded"]').forEach(el => {
        el.classList.add("s2-hide-stock");
      });
    }

    /* Build the s2 container and insert at the top of the OUTER .container
       (the direct child of <main>, sibling of .jumbotron). The jumbotron also
       wraps a .container internally, but we hide the jumbotron — if we
       inserted into that inner container, our s2 markup would inherit the
       hidden parent. `main > .container` is unambiguous. */
    const container = document.querySelector("main > .container");
    if (!container) return;

    const root = document.createElement("div");
    root.className = "s2-wrap";
    root.innerHTML = `
      <header class="s2-head">
        <div class="s2-head-l">
          <h1 class="s2-h1">도전 과제<span class="s2-h1-en">Challenges</span></h1>
          <p class="s2-head-sub">
            총 <span id="s2-total-count">—</span>개의 온라인 논리설계 과제<span
              class="s2-head-phase" id="s2-head-phase" hidden> · <b id="s2-round-status-text"></b></span>
          </p>
        </div>
        <div class="s2-head-r">
          <a class="s2-btn" href="/my-score">내 점수 보기</a>
        </div>
      </header>

      <section class="s2-progress">
        <div class="s2-prog-cell">
          <div class="s2-prog-label">우리 조</div>
          <div class="s2-prog-val" id="s2-team-name">—</div>
        </div>
        <div class="s2-prog-cell">
          <div class="s2-prog-label">해결 / 총 과제</div>
          <div class="s2-prog-val">
            <span id="s2-solved">—</span><span class="s2-prog-mut"> / <span id="s2-total">—</span></span>
          </div>
        </div>
        <div class="s2-prog-cell">
          <div class="s2-prog-label">획득 / 만점</div>
          <div class="s2-prog-val">
            <span id="s2-score">—</span><span class="s2-prog-mut"> / <span id="s2-total-pts">—</span></span>
          </div>
        </div>
        <div class="s2-prog-cell s2-prog-cell-bar">
          <div class="s2-prog-label">진행도</div>
          <div class="s2-prog-row">
            <div class="s2-prog-track">
              <div class="s2-prog-fill" id="s2-prog-fill" style="width:0%"></div>
            </div>
            <div class="s2-prog-bar-meta" id="s2-prog-pct">0%</div>
          </div>
        </div>
      </section>

      <div class="s2-round-status s2-round-status-quiet" id="s2-round-status" hidden></div>
      <div class="s2-sections" id="s2-sections">
        <div class="s2-loading">도전 과제를 불러오는 중…</div>
      </div>
    `;
    container.insertBefore(root, container.firstChild);
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function setText(id, txt) {
    const el = document.getElementById(id);
    if (el) el.textContent = txt;
  }

  /* All icons are 24-unit strokes; the size comes from the CSS of whatever
     wraps them (chip 13, action 14, banner 16), so none carry a width here. */
  function svgIcon(body) {
    return '<svg viewBox="0 0 24 24" aria-hidden="true">' + body + '</svg>';
  }

  const ICON = {
    check: '<polyline points="20 6 9 17 4 12"/>',
    circle: '<circle cx="12" cy="12" r="7"/>',
    lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    pause: '<path d="M10 4H6v16h4zM18 4h-4v16h4z"/>',
    done: '<circle cx="12" cy="12" r="9"/><polyline points="16 9 11 15 8 12"/>',
    layers: '<path d="M12 3l9 4.5-9 4.5-9-4.5z"/><path d="M3 12l9 4.5 9-4.5M3 16.5 12 21l9-4.5"/>',
    alert: '<path d="M12 3.4 2.7 20.6h18.6z"/><path d="M12 10v4M12 17.4v.01"/>',
  };

  /* Every state carries a glyph and a Korean word, never colour alone. */
  function statusChip(state) {
    if (state === "pass") {
      return '<span class="s2-chip s2-chip-pass">' + svgIcon(ICON.check) + '전체 통과</span>';
    }
    if (state === "locked") {
      return '<span class="s2-chip">' + svgIcon(ICON.lock) + '잠김</span>';
    }
    /* /api/v1/challenges carries no per-problem attempt count, so an unsolved
       problem is 미시작 whether or not the team has already tried it. */
    return '<span class="s2-chip">' + svgIcon(ICON.circle) + '미시작</span>';
  }

  function bannerIcon(phase) {
    return svgIcon({
      before: ICON.clock,
      break: ICON.pause,
      finished: ICON.done,
      open: ICON.layers,
      misconfigured: ICON.alert,
    }[phase] || ICON.clock);
  }

  function bannerTone(phase) {
    if (phase === "misconfigured") return "s2-round-status-alert";
    /* 휴식 시간 is the one banner that asks for something (get ready), so it
       keeps the accent ground; the rest are just state. */
    return phase === "break" ? "" : "s2-round-status-quiet";
  }

  /* opts: { locked, paused, nextId, phase }. locked means the server is not
     listing this round's problems yet, so the rows are inert; paused means
     they are readable but closed for submission (휴식/종료). nextId marks the
     single row that gets the board's only filled button. */
  function renderRow(c, opts) {
    const solved = !!c.solved_by_me;
    const isNext = opts.nextId !== null && c.id === opts.nextId;
    const problemUrl = "/problems/" + encodeURIComponent(c.id);
    let cls = "s2-row " + (solved ? "s2-row-pass" : "s2-row-todo");
    if (isNext) cls += " s2-row-next";

    /* A locked round's rows are inert: no link, no tab stop, and no
       data-problem-url for the delegated handler in render() to act on. */
    const openAttrs = opts.locked
      ? ' aria-disabled="true"'
      : ' data-problem-url="' + problemUrl + '" tabindex="0" role="link"'
        + ' aria-label="' + esc(c.name + " 문제 페이지 열기") + '"';

    const name = opts.locked
      ? esc(c.name)
      : '<a class="s2-problem-link" href="' + problemUrl + '">' + esc(c.name) + '</a>';

    let action;
    if (opts.locked) {
      action = '<span class="s2-action">대기 중</span>';
    } else if (solved) {
      action = '<a class="s2-action" href="' + problemUrl + '">다시 보기' + svgIcon(ICON.arrow) + '</a>';
    } else {
      /* 풀어보기 promises a submission, so a paused round says 문제 보기. */
      action = '<a class="s2-action s2-action-btn" href="' + problemUrl + '">'
        + (opts.paused ? "문제 보기" : "풀어보기")
        + (isNext ? svgIcon(ICON.arrow) : "") + '</a>';
    }

    /* A solved problem stays 전체 통과 in every phase — a closed round does
       not undo the team's work. */
    const state = solved ? "pass" : (opts.locked ? "locked" : "todo");

    return ''
      + '<tr class="' + cls + '" data-challenge-id="' + c.id + '"' + openAttrs + '>'
      +   '<td class="s2-td-id"><span class="s2-id-badge">#' + esc(String(c.id).padStart(2, "0")) + '</span></td>'
      +   '<td class="s2-td-name"><div class="s2-name">' + name + '</div></td>'
      +   '<td class="s2-td-pts">'
      +     '<span class="s2-pts">' + esc(c.value) + '</span><span class="s2-pts-u">pt</span>'
      +   '</td>'
      +   '<td class="s2-td-status">' + statusChip(state) + '</td>'
      +   '<td class="s2-td-action">' + action + '</td>'
      + '</tr>';
  }

  function renderCategory(cat, info, items, opts) {
    const passed = items.filter(i => i.solved_by_me).length;
    const total = items.length;
    const gotPts = items.reduce((a, i) => a + (i.solved_by_me ? (i.value || 0) : 0), 0);
    const totalPts = items.reduce((a, i) => a + (i.value || 0), 0);
    const lock = opts.locked || opts.paused
      ? '<span class="s2-cat-lock">' + svgIcon(ICON.lock) + esc(lockedNote(opts.phase)) + '</span>'
      : '';
    return ''
      + '<section class="s2-cat" data-cat="' + esc(cat) + '">'
      +   '<header class="s2-cat-head">'
      +     '<h2 class="s2-cat-title">' + esc(info.label) + '</h2>'
      +     '<span class="s2-cat-sub">' + esc(info.sub) + '</span>'
      +     lock
      +     '<span class="s2-cat-stat">'
      +       '해결 <span class="s2-cat-frac">' + passed + ' / ' + total + '</span>'
      +       ' · 점수 <span class="s2-cat-frac">' + gotPts + ' / ' + totalPts + '</span>'
      +     '</span>'
      +   '</header>'
      +   '<table class="s2-tbl' + (opts.locked ? ' s2-tbl-locked' : '') + '">'
      +     '<tbody>' + items.map(i => renderRow(i, opts)).join("") + '</tbody>'
      +   '</table>'
      + '</section>';
  }

  function render(challenges, user, competition) {
    const groups = {};
    challenges.forEach(c => {
      const k = c.category || "기타";
      (groups[k] = groups[k] || []).push(c);
    });

    const sortedCats = Object.keys(groups).sort((a, b) => {
      const ai = (CATEGORY_ORDER[a] && CATEGORY_ORDER[a].idx) != null ? CATEGORY_ORDER[a].idx : 99;
      const bi = (CATEGORY_ORDER[b] && CATEGORY_ORDER[b].idx) != null ? CATEGORY_ORDER[b].idx : 99;
      return ai - bi;
    });

    const totalCount = challenges.length;
    const solvedCount = challenges.filter(c => c.solved_by_me).length;
    const totalPts = challenges.reduce((a, c) => a + (c.value || 0), 0);
    const gotPts = challenges.reduce((a, c) => a + (c.solved_by_me ? (c.value || 0) : 0), 0);
    const pct = totalPts > 0 ? Math.round(gotPts / totalPts * 100) : 0;

    setText("s2-total-count", totalCount);
    const phase = (competition && competition.phase) || "";
    const currentPhaseText = phaseText(phase);
    const running = RUNNING_PHASES.indexOf(phase) !== -1;

    /* One sentence, one place: the page header carries it while a round runs,
       the banner carries it otherwise. Printing both cost ~90px and pushed
       the last two round-1 rows below the fold on a 1366×768 laptop. */
    setText("s2-round-status-text", currentPhaseText);
    const headPhase = document.getElementById("s2-head-phase");
    if (headPhase) headPhase.hidden = !running;
    const banner = document.getElementById("s2-round-status");
    if (banner) {
      banner.className = ("s2-round-status " + bannerTone(phase)).trim();
      banner.innerHTML = bannerIcon(phase) + "<span>" + esc(currentPhaseText) + "</span>";
      banner.hidden = running;
    }

    setText("s2-team-name", user && user.name ? user.name : "—");
    setText("s2-solved", solvedCount);
    setText("s2-total", totalCount);
    setText("s2-score", gotPts);
    setText("s2-total-pts", totalPts);
    setText("s2-prog-pct", pct + "%");
    const fill = document.getElementById("s2-prog-fill");
    if (fill) fill.style.width = pct + "%";

    const root = document.getElementById("s2-sections");
    if (!root) return;

    /* Two different closed states. A round the server is not listing yet is
       locked (inert rows); a round it lists with submissions off is paused
       (readable, but nothing to start). visible_challenge_ids is missing only
       when the competition endpoint failed, and then we fall back to open — a
       network blip must not lock a live contest's board.

       Note that in a real round only an admin, a rehearsal ("open") board or a
       finished contest ever reaches the locked branch: sync_challenge_states()
       sets every challenge outside the running phase to "hidden", and
       /api/v1/challenges?view=user does not return hidden challenges to a
       mentee — so for a mentee the other round drops off the board instead of
       locking. Rendering it locked would mean building it from
       competition.rounds, which the board already fetches. */
    const submissionsOpen = competition && typeof competition.submissions_open === "boolean"
      ? competition.submissions_open
      : true;
    const visibleIds = competition && Array.isArray(competition.visible_challenge_ids)
      ? competition.visible_challenge_ids
      : null;

    let html = "";
    let nextTaken = false;
    sortedCats.forEach(cat => {
      const info = CATEGORY_ORDER[cat] || { label: cat, sub: "" };
      const items = groups[cat].slice().sort((a, b) => a.id - b.id);
      const locked = visibleIds !== null && !items.some(i => visibleIds.indexOf(i.id) !== -1);
      const paused = !locked && !submissionsOpen;
      /* Exactly one row on the whole board gets the filled button: the first
         unsolved problem of the first round that is open right now. */
      let nextId = null;
      if (!locked && !paused && !nextTaken) {
        const next = items.filter(i => !i.solved_by_me)[0];
        if (next) {
          nextId = next.id;
          nextTaken = true;
        }
      }
      html += renderCategory(cat, info, items, {
        locked: locked,
        paused: paused,
        nextId: nextId,
        phase: phase,
      });
    });
    root.innerHTML = html || '<div class="s2-empty">지금 열려 있는 도전 과제가 없습니다.</div>';

    root.querySelectorAll(".s2-row").forEach(row => {
      const navigate = () => {
        const url = row.getAttribute("data-problem-url");
        if (url) window.location.assign(url);
      };
      row.addEventListener("click", (event) => {
        if (event.target.closest("a")) return;
        navigate();
      });
      row.addEventListener("keydown", (event) => {
        if (event.target.closest("a")) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          navigate();
        }
      });
    });
  }

  function showError(msg) {
    const root = document.getElementById("s2-sections");
    if (root) root.innerHTML = '<div class="s2-error">' + esc(msg) + '</div>';
  }

  let loading = false;
  let pendingRefresh = false;
  let knownPhase = null;

  function requestRefresh() {
    if (loading) { pendingRefresh = true; return; }
    load();
  }

  function observeCompetition(data) {
    const phase = data && data.phase;
    if (!phase) return;
    const changed = knownPhase !== null && knownPhase !== phase;
    knownPhase = phase;
    if (changed) requestRefresh();
  }

  async function pollCompetitionPhase() {
    try {
      const response = await fetch("/api/v1/digital/competition", { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) return;
      const payload = await response.json();
      observeCompetition(payload.data || {});
    } catch (_error) {
      // The main load path will retry on the next transition/event.
    }
  }

  async function load() {
    if (loading) return;
    loading = true;
    injectShell();
    try {
      const [chRes, userRes, competitionRes] = await Promise.all([
        fetch("/api/v1/challenges?view=user", { credentials: "same-origin", cache: "no-store" }),
        fetch("/api/v1/users/me", { credentials: "same-origin" }),
        fetch("/api/v1/digital/competition", { credentials: "same-origin" }),
      ]);
      if (chRes.redirected || chRes.status === 401) {
        showError("로그인이 필요합니다. 페이지를 새로고침하여 다시 로그인하세요.");
        return;
      }
      if (!chRes.ok) {
        showError("도전 과제를 불러오지 못했습니다. (HTTP " + chRes.status + ")");
        return;
      }
      const chJson = await chRes.json();
      const userJson = userRes.ok ? await userRes.json() : {};
      const competitionJson = competitionRes.ok ? await competitionRes.json() : {};
      const competition = (competitionJson && competitionJson.data) || {};
      render(chJson.data || [], (userJson && userJson.data) || {}, competition);
      observeCompetition(competition);
    } catch (e) {
      showError("도전 과제를 불러오지 못했습니다: " + (e && e.message ? e.message : "unknown"));
    }
    finally {
      loading = false;
      if (pendingRefresh) {
        pendingRefresh = false;
        load();
      }
    }
  }

  window.addEventListener("econ:competition-change", (event) => {
    observeCompetition(event.detail || {});
  });
  // round-ui.js polls the same endpoint and broadcasts the event above, so this
  // is only a safety net for the pages where that script fails to load.
  setInterval(pollCompetitionPhase, 30000);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", load);
  } else {
    load();
  }
})();
