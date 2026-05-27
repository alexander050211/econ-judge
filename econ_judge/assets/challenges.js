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
 *   Row clicks fire the same `load-challenge` window CustomEvent that the
 *   Alpine wrapper listens for, so the modal opens with no glue code.
 *
 * Gated by window.location.pathname === '/challenges' so it's safe to load
 * the script on every page via THEME_HEADER_CSS. */
(function () {
  "use strict";

  if (window.location.pathname !== "/challenges") return;

  const CATEGORY_ORDER = {
    "연습": { idx: 0, label: "연습 — Practice",       sub: "On-ramp + building blocks for projects", tagCls: "" },
    "미션": { idx: 1, label: "미션 — Missions",       sub: "NOR-only construction · BCD detector",    tagCls: "d-tag-mission" },
    "Project 1": { idx: 2, label: "Project 1 — Adders & ÷3",      sub: "3-bit pipeline · X+Y → 보수 → ⌈/3⌉",  tagCls: "d-tag-p1" },
    "Project 2": { idx: 3, label: "Project 2 — Shelter assignment", sub: "Comparator · MUX · 7-segment",      tagCls: "d-tag-p2" },
  };

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

/* ─── s2 layout ─── */
.s2-wrap {
  padding: 36px 0 64px;
  display: flex;
  flex-direction: column;
  gap: 28px;
}
.s2-wrap *, .s2-wrap *::before, .s2-wrap *::after { box-sizing: border-box; }
.s2-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 32px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--d-hair);
}
.s2-head-l { display: flex; flex-direction: column; gap: 6px; }
.s2-h1 {
  font-family: var(--d-f-sans);
  font-weight: 600;
  font-size: 48px;
  letter-spacing: -0.03em;
  margin: 4px 0 0;
  color: var(--d-ink);
  line-height: 1.05;
}
.s2-h1-ko {
  font-family: var(--d-f-ko);
  font-size: 28px;
  font-weight: 500;
  color: var(--d-ink-light);
  letter-spacing: -0.015em;
  margin-left: 4px;
}
.s2-head-sub {
  font-family: var(--d-f-ko);
  font-size: 15px;
  color: var(--d-ink-light);
  line-height: 1.55;
  margin: 12px 0 0;
  max-width: 540px;
}
.s2-progress {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr 2fr;
  gap: 0;
  border: 1px solid var(--d-hair-strong);
  background: var(--d-paper-soft);
}
.s2-prog-cell {
  padding: 14px 20px 16px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  border-right: 1px solid var(--d-hair);
}
.s2-prog-cell:last-child { border-right: none; }
.s2-prog-val {
  font-family: var(--d-f-sans);
  font-weight: 600;
  font-size: 28px;
  letter-spacing: -0.025em;
  color: var(--d-ink);
  font-feature-settings: 'tnum';
  display: flex;
  align-items: baseline;
}
.s2-prog-mut {
  color: var(--d-ink-light);
  font-weight: 500;
  font-size: 18px;
  margin-left: 2px;
  letter-spacing: 0;
}
.s2-prog-cell-bar { gap: 10px; padding-top: 14px; padding-bottom: 14px; justify-content: center; }
.s2-prog-track {
  height: 8px;
  background: var(--d-paper-sunk);
  position: relative;
  overflow: hidden;
}
.s2-prog-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--d-brand-dark), var(--d-brand));
  transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
}
.s2-prog-bar-meta {
  font-family: var(--d-f-mono);
  font-size: 11px;
  color: var(--d-ink-mid);
  letter-spacing: 0.04em;
  align-self: flex-end;
}
.s2-sections { display: flex; flex-direction: column; gap: 28px; }
.s2-cat { display: flex; flex-direction: column; gap: 10px; }
.s2-cat-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 16px;
}
.s2-cat-head-l { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.s2-cat-title {
  font-family: var(--d-f-sans);
  font-weight: 600;
  font-size: 22px;
  letter-spacing: -0.02em;
  color: var(--d-ink);
  margin: 0;
}
.s2-cat-sub { font-family: var(--d-f-ko); font-size: 14px; color: var(--d-ink-light); }
.s2-cat-head-r {
  display: inline-flex;
  align-items: baseline;
  gap: 10px;
  font-family: var(--d-f-mono);
}
.s2-cat-divider { width: 1px; height: 12px; background: var(--d-hair-strong); display: inline-block; align-self: center; }
.s2-cat-frac {
  font-family: var(--d-f-sans);
  font-weight: 600;
  font-size: 18px;
  color: var(--d-ink);
  letter-spacing: -0.01em;
  font-feature-settings: 'tnum';
}
.s2-cat-frac-mut { color: var(--d-ink-light); font-weight: 500; font-size: 14px; }
.s2-tbl {
  width: 100%;
  border-collapse: collapse;
  border-top: 1.5px solid var(--d-ink);
  border-bottom: 1.5px solid var(--d-ink);
  font-family: var(--d-f-ko);
}
.s2-tbl thead th {
  font-family: var(--d-f-mono);
  font-size: 10px;
  letter-spacing: 0.14em;
  color: var(--d-ink-light);
  text-transform: uppercase;
  text-align: left;
  padding: 8px 14px;
  border-bottom: 1px solid var(--d-ink);
  font-weight: 600;
  background: var(--d-paper);
}
.s2-th-id { width: 60px; }
.s2-th-pts { width: 80px; text-align: right !important; }
.s2-th-status { width: 170px; }
.s2-th-action { width: 110px; text-align: right !important; }
.s2-row {
  border-bottom: 1px solid var(--d-hair);
  transition: background 0.12s ease;
  cursor: pointer;
}
.s2-row:last-child { border-bottom: none; }
.s2-row:hover { background: var(--d-paper-soft); }
.s2-row td {
  padding: 12px 14px;
  vertical-align: middle;
  font-size: 14.5px;
  color: var(--d-ink);
}
.s2-row-locked .s2-name { color: var(--d-ink-mid); }
.s2-id-badge {
  font-family: var(--d-f-mono);
  font-size: 12px;
  font-weight: 500;
  color: var(--d-ink-light);
  letter-spacing: 0.04em;
}
.s2-td-name { padding-right: 24px !important; }
.s2-name {
  font-family: var(--d-f-ko);
  font-size: 15px;
  font-weight: 500;
  color: var(--d-ink);
  letter-spacing: -0.005em;
  line-height: 1.4;
}
.s2-td-pts {
  text-align: right;
  font-family: var(--d-f-sans);
  font-feature-settings: 'tnum';
}
.s2-pts {
  font-weight: 600;
  font-size: 16px;
  color: var(--d-ink);
  letter-spacing: -0.01em;
}
.s2-pts-u {
  font-family: var(--d-f-mono);
  font-size: 11px;
  color: var(--d-ink-light);
  margin-left: 3px;
  letter-spacing: 0.04em;
}
.s2-td-status .d-pill { vertical-align: middle; }
.s2-td-action { text-align: right; }
.s2-action {
  font-family: var(--d-f-mono);
  font-size: 11.5px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--d-ink-light);
  display: inline-flex;
  align-items: center;
  gap: 5px;
  transition: color 0.12s ease;
}
.s2-row:hover .s2-action { color: var(--d-ink); }
.s2-row:hover .s2-action svg { transform: translateX(2px); }
.s2-action svg { transition: transform 0.12s ease; }
.s2-loading, .s2-error {
  padding: 24px;
  text-align: center;
  font-family: var(--d-f-ko);
  color: var(--d-ink-light);
  font-size: 14px;
}
.s2-error {
  background: var(--d-fail-soft);
  border: 1px solid var(--d-fail-line);
  color: var(--d-fail);
  border-radius: 6px;
}
@media (max-width: 720px) {
  .s2-progress { grid-template-columns: 1fr 1fr; }
  .s2-prog-cell { border-right: none; border-bottom: 1px solid var(--d-hair); }
  .s2-prog-cell:nth-child(odd) { border-right: 1px solid var(--d-hair); }
  .s2-prog-cell-bar { grid-column: 1 / -1; }
  .s2-h1 { font-size: 36px; }
  .s2-th-status, .s2-td-status { display: none; }
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
          <div class="d-meta">SECTION · 도전 과제</div>
          <h1 class="s2-h1">Challenges<span class="s2-h1-ko">&nbsp;도전 과제</span></h1>
          <p class="s2-head-sub">
            총 <span id="s2-total-count">—</span>개의 디지털 논리회로 과제.
            연습 → 미션 → 프로젝트 순서대로 풀어보세요.
          </p>
        </div>
        <div class="s2-head-r">
          <a class="d-btn d-btn-ghost" href="/my-score">내 점수 보기</a>
        </div>
      </header>

      <section class="s2-progress">
        <div class="s2-prog-cell">
          <div class="d-meta">우리 조</div>
          <div class="s2-prog-val" id="s2-team-name">—</div>
        </div>
        <div class="s2-prog-cell">
          <div class="d-meta">해결 / 총 과제</div>
          <div class="s2-prog-val">
            <span id="s2-solved">—</span><span class="s2-prog-mut"> / <span id="s2-total">—</span></span>
          </div>
        </div>
        <div class="s2-prog-cell">
          <div class="d-meta">획득 / 만점</div>
          <div class="s2-prog-val">
            <span id="s2-score">—</span><span class="s2-prog-mut"> / <span id="s2-total-pts">—</span></span>
          </div>
        </div>
        <div class="s2-prog-cell s2-prog-cell-bar">
          <div class="d-meta">진행도</div>
          <div class="s2-prog-track">
            <div class="s2-prog-fill" id="s2-prog-fill" style="width:0%"></div>
          </div>
          <div class="s2-prog-bar-meta" id="s2-prog-pct">0%</div>
        </div>
      </section>

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

  function pillForStatus(status) {
    if (status === "pass") {
      return '<span class="d-pill d-pill-pass"><span class="d-pill-dot"></span>전체 통과</span>';
    }
    return '<span class="d-pill d-pill-locked"><span class="d-pill-dot"></span>미시작</span>';
  }

  function arrowSvg() {
    return '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">'
      + '<path d="M5 4l4 4-4 4" stroke="currentColor" stroke-width="1.6" '
      + 'stroke-linecap="round" stroke-linejoin="round"/></svg>';
  }

  function renderRow(c) {
    const cls = c.solved_by_me ? "s2-row s2-row-pass" : "s2-row s2-row-locked";
    const actionLabel = c.solved_by_me ? "재제출" : "풀어보기";
    return ''
      + '<tr class="' + cls + '" data-challenge-id="' + c.id + '">'
      +   '<td class="s2-td-id"><span class="s2-id-badge">' + esc(String(c.id).padStart(2, "0")) + '</span></td>'
      +   '<td class="s2-td-name"><div class="s2-name">' + esc(c.name) + '</div></td>'
      +   '<td class="s2-td-pts">'
      +     '<span class="s2-pts">' + esc(c.value) + '</span><span class="s2-pts-u">pt</span>'
      +   '</td>'
      +   '<td class="s2-td-status">' + pillForStatus(c.solved_by_me ? "pass" : "locked") + '</td>'
      +   '<td class="s2-td-action"><span class="s2-action">' + actionLabel + arrowSvg() + '</span></td>'
      + '</tr>';
  }

  function renderCategory(cat, info, items) {
    const passed = items.filter(i => i.solved_by_me).length;
    const total = items.length;
    const gotPts = items.reduce((a, i) => a + (i.solved_by_me ? (i.value || 0) : 0), 0);
    const totalPts = items.reduce((a, i) => a + (i.value || 0), 0);
    const tagCls = info.tagCls ? "d-tag " + info.tagCls : "d-tag";
    return ''
      + '<section class="s2-cat" data-cat="' + esc(cat) + '">'
      +   '<header class="s2-cat-head">'
      +     '<div class="s2-cat-head-l">'
      +       '<span class="' + tagCls + '">' + esc(cat) + '</span>'
      +       '<h2 class="s2-cat-title">' + esc(info.label) + '</h2>'
      +       '<span class="s2-cat-sub">' + esc(info.sub) + '</span>'
      +     '</div>'
      +     '<div class="s2-cat-head-r">'
      +       '<span class="d-tiny">SOLVED</span>'
      +       '<span class="s2-cat-frac">' + passed + '<span class="s2-cat-frac-mut">/' + total + '</span></span>'
      +       '<span class="s2-cat-divider"></span>'
      +       '<span class="d-tiny">POINTS</span>'
      +       '<span class="s2-cat-frac">' + gotPts + '<span class="s2-cat-frac-mut">/' + totalPts + '</span></span>'
      +     '</div>'
      +   '</header>'
      +   '<table class="s2-tbl">'
      +     '<thead><tr>'
      +       '<th class="s2-th-id">#</th>'
      +       '<th>도전 과제</th>'
      +       '<th class="s2-th-pts">점수</th>'
      +       '<th class="s2-th-status">상태</th>'
      +       '<th class="s2-th-action"></th>'
      +     '</tr></thead>'
      +     '<tbody>' + items.map(renderRow).join("") + '</tbody>'
      +   '</table>'
      + '</section>';
  }

  function render(challenges, user) {
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
    let html = "";
    sortedCats.forEach(cat => {
      const info = CATEGORY_ORDER[cat] || { label: cat, sub: "", tagCls: "" };
      const items = groups[cat].slice().sort((a, b) => a.id - b.id);
      html += renderCategory(cat, info, items);
    });
    root.innerHTML = html;

    root.querySelectorAll(".s2-row").forEach(row => {
      row.addEventListener("click", () => {
        const id = parseInt(row.getAttribute("data-challenge-id"), 10);
        if (!isNaN(id)) {
          window.dispatchEvent(new CustomEvent("load-challenge", { detail: id }));
        }
      });
    });
  }

  function showError(msg) {
    const root = document.getElementById("s2-sections");
    if (root) root.innerHTML = '<div class="s2-error">' + esc(msg) + '</div>';
  }

  async function load() {
    injectShell();
    try {
      const [chRes, userRes] = await Promise.all([
        fetch("/api/v1/challenges?view=user", { credentials: "same-origin" }),
        fetch("/api/v1/users/me", { credentials: "same-origin" }),
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
      render(chJson.data || [], (userJson && userJson.data) || {});
    } catch (e) {
      showError("도전 과제를 불러오지 못했습니다: " + (e && e.message ? e.message : "unknown"));
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", load);
  } else {
    load();
  }
})();
