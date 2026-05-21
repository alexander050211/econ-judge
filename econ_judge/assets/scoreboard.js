// E-CON 논설 ICPC scoreboard — redesigned for projection on BK Hall screen.
// Mounts below CTFd's stock table on /scoreboard only.
// Auto-refreshes every 15s when visible; pauses while hidden.
// Data contracts:
//   GET /api/v1/challenges        → { data: [{id, name, category, value}, ...] }
//   GET /api/v1/scoreboard/top/100 → { data: { 1: {name, score, solves:[{challenge_id, date}]}, … } }

(function () {
  "use strict";
  if (location.pathname !== "/scoreboard") return;

  // ─── Constants ────────────────────────────────────────────────────────────
  const REFRESH_MS = 15000;
  const ROOT_ID    = "econ-icpc-board";
  const DARK_PARAM = new URLSearchParams(location.search).has("dark");

  // ─── Auto-refresh (same contract as before) ───────────────────────────────
  let refreshTimer = setTimeout(reloadPage, REFRESH_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      clearTimeout(refreshTimer);
      refreshTimer = null;
    } else if (!refreshTimer) {
      refreshTimer = setTimeout(reloadPage, REFRESH_MS);
    }
  });
  function reloadPage() { location.reload(); }

  // ─── Boot ─────────────────────────────────────────────────────────────────
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  function boot() {
    injectStyles();
    render().catch((e) => console.warn("econ-judge ICPC board:", e));
  }

  // ─── Data layer (unchanged contracts) ────────────────────────────────────
  async function render() {
    const [chalsResp, boardResp] = await Promise.all([
      fetch("/api/v1/challenges",          { credentials: "same-origin" }),
      fetch("/api/v1/scoreboard/top/100",  { credentials: "same-origin" }),
    ]);
    if (!chalsResp.ok || !boardResp.ok) return;

    const chals = ((await chalsResp.json()).data || [])
      .slice()
      .sort((a, b) => a.id - b.id);
    const board = ((await boardResp.json()).data) || {};
    const teams = Object.values(board)
      .slice()
      .sort((a, b) => (b.score || 0) - (a.score || 0));

    if (!chals.length || !teams.length) return;

    // Contest start = earliest solve across all teams.
    let earliest = null;
    for (const t of teams) {
      for (const s of t.solves || []) {
        const d = new Date(s.date);
        if (!earliest || d < earliest) earliest = d;
      }
    }
    const contestStart = earliest || new Date();

    // First-solver per challenge.
    const firstSolverByCid = new Map();
    for (const c of chals) {
      let bestTeam = null, bestDate = null;
      for (const t of teams) {
        for (const s of t.solves || []) {
          if (s.challenge_id !== c.id) continue;
          const d = new Date(s.date);
          if (!bestDate || d < bestDate) { bestDate = d; bestTeam = t.name; }
        }
      }
      if (bestTeam) firstSolverByCid.set(c.id, bestTeam);
    }

    // Per-team solve map.
    for (const t of teams) {
      t._byCid = new Map();
      for (const s of t.solves || []) {
        t._byCid.set(s.challenge_id, new Date(s.date));
      }
    }

    const mount = ensureMount();
    mount.innerHTML = buildHtml(chals, teams, contestStart, firstSolverByCid);

    // Animate newly-painted cells after mount.
    requestAnimationFrame(() => animateIn(mount));
  }

  // ─── HTML builder ─────────────────────────────────────────────────────────
  function buildHtml(chals, teams, contestStart, firstSolverByCid) {
    // Group challenges by category for the two-row header.
    const cats = [];
    const catMap = new Map();
    for (const c of chals) {
      if (!catMap.has(c.category)) {
        catMap.set(c.category, []);
        cats.push(c.category);
      }
      catMap.get(c.category).push(c);
    }

    // Header category spans.
    const catHeaderCells = cats.map((cat) => {
      const span = catMap.get(cat).length;
      return `<th class="h-cat" colspan="${span}">${esc(cat)}</th>`;
    }).join("");

    // Problem number sub-row — use 1-based sequential index within category.
    let probIdx = 0;
    const probHeaderCells = chals.map((c) => {
      probIdx++;
      return `<th class="h-prob" title="${esc(c.name)} · ${c.value}pt">${probIdx}</th>`;
    }).join("");

    const colgroup = `<colgroup>
      <col class="c-rank">
      <col class="c-team">
      <col class="c-solved">
      <col class="c-score">
      ${chals.map(() => '<col class="c-prob">').join("")}
    </colgroup>`;

    const thead = `<thead>
      <tr class="tr-cats">
        <th rowspan="2" class="h-rank">#</th>
        <th rowspan="2" class="h-team">팀</th>
        <th rowspan="2" class="h-solved">통과</th>
        <th rowspan="2" class="h-score">점수</th>
        ${catHeaderCells}
      </tr>
      <tr class="tr-probs">
        ${probHeaderCells}
      </tr>
    </thead>`;

    const MEDALS = ["🥇", "🥈", "🥉"];
    const RANK_CLASSES = ["rank-gold", "rank-silver", "rank-bronze"];

    const rows = teams.map((t, i) => {
      const rank      = i + 1;
      const medal     = MEDALS[i] || null;
      const rankCls   = RANK_CLASSES[i] || "";
      const solveCount = (t.solves || []).length;
      const isTop3    = rank <= 3;

      const rankCell = medal
        ? `<td class="rank ${rankCls}"><span class="medal-emoji">${medal}</span></td>`
        : `<td class="rank"><span class="rank-num">${rank}</span></td>`;

      const cells = chals.map((c) => {
        const d = t._byCid.get(c.id);
        if (!d) return '<td class="cell cell-empty"><span class="dot">·</span></td>';
        const minutes = Math.max(0, Math.round((d - contestStart) / 60000));
        const isFirst = firstSolverByCid.get(c.id) === t.name;
        if (isFirst) {
          return (
            `<td class="cell cell-first" data-new="1">` +
              `<div class="cell-inner">` +
                `<span class="fb-star" aria-hidden="true">★</span>` +
                `<span class="time">${minutes}</span>` +
              `</div>` +
            `</td>`
          );
        }
        return (
          `<td class="cell cell-solved" data-new="1">` +
            `<div class="cell-inner">` +
              `<span class="time">${minutes}</span>` +
            `</div>` +
          `</td>`
        );
      }).join("");

      const rowCls = isTop3 ? `top3 top3-${rank}` : "";
      return (
        `<tr class="${rowCls}">` +
          rankCell +
          `<td class="team">${esc(t.name)}</td>` +
          `<td class="solved">${solveCount}</td>` +
          `<td class="score">${t.score || 0}</td>` +
          cells +
        `</tr>`
      );
    }).join("");

    // Live countdown ticker (seconds until next refresh).
    const tickerHtml = `<span class="ticker" id="esb-ticker">15</span>`;

    return `
      <header class="esb-head">
        <div class="esb-head-left">
          <span class="esb-badge">ICPC</span>
          <h2 class="esb-title">도전 현황</h2>
        </div>
        <div class="esb-head-right">
          <span class="esb-live-dot" aria-hidden="true"></span>
          <span class="esb-meta">실시간 갱신 · ${tickerHtml}초</span>
          <button class="esb-fullscreen-btn" onclick="(function(){var el=document.getElementById('${ROOT_ID}');el.classList.toggle('esb-fullscreen');})()" title="전체화면 토글" aria-label="전체화면">⛶</button>
        </div>
      </header>

      <div class="esb-wrap">
        <table class="esb-table">
          ${colgroup}
          ${thead}
          <tbody>${rows}</tbody>
        </table>
      </div>

      <footer class="esb-foot">
        <span class="legend">
          <span class="swatch sw-first" aria-hidden="true">★</span>
          <span>최초 통과 (First Blood)</span>
        </span>
        <span class="legend">
          <span class="swatch sw-solved" aria-hidden="true"></span>
          <span>통과 · 셀 숫자 = 시작 후 경과 분</span>
        </span>
        <span class="legend">
          <span class="swatch sw-empty" aria-hidden="true">·</span>
          <span>미제출</span>
        </span>
      </footer>
    `;
  }

  // ─── Post-render animation ────────────────────────────────────────────────
  function animateIn(mount) {
    // Stagger-fade all solved/first cells in.
    const cells = mount.querySelectorAll(".cell-solved, .cell-first");
    cells.forEach((el, i) => {
      el.style.animationDelay = `${Math.min(i * 18, 600)}ms`;
      el.classList.add("cell-fadein");
    });

    // Start the countdown ticker.
    startTicker();
  }

  function startTicker() {
    const el = document.getElementById("esb-ticker");
    if (!el) return;
    let s = Math.round(REFRESH_MS / 1000);
    el.textContent = s;
    const iv = setInterval(() => {
      s--;
      if (s <= 0) { clearInterval(iv); return; }
      if (el) el.textContent = s;
    }, 1000);
  }

  // ─── Mount ────────────────────────────────────────────────────────────────
  function ensureMount() {
    let mount = document.getElementById(ROOT_ID);
    if (mount) return mount;
    mount = document.createElement("section");
    mount.id = ROOT_ID;
    if (DARK_PARAM) mount.classList.add("esb-dark");
    const target =
      document.querySelector("main .container") ||
      document.querySelector(".container") ||
      document.body;
    target.appendChild(mount);
    return mount;
  }

  // ─── Utility ──────────────────────────────────────────────────────────────
  function esc(s) {
    const div = document.createElement("div");
    div.textContent = String(s == null ? "" : s);
    return div.innerHTML;
  }

  // ─── Styles ───────────────────────────────────────────────────────────────
  function injectStyles() {
    if (document.getElementById("econ-icpc-styles")) return;

    const css = `
/* ── Google Fonts: JetBrains Mono for numbers ─────────────────────────── */
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap');

/* ── Keyframes ─────────────────────────────────────────────────────────── */
@keyframes esb-shimmer {
  0%   { background-position: -200% center; }
  100% { background-position:  200% center; }
}
@keyframes esb-pulse-glow {
  0%, 100% { box-shadow: inset 0 0 0 2px rgba(245,168,61,0.6), 0 0 6px 1px rgba(245,168,61,0.15); }
  50%       { box-shadow: inset 0 0 0 2px rgba(245,168,61,1.0), 0 0 14px 4px rgba(245,168,61,0.45); }
}
@keyframes esb-fadein {
  from { opacity: 0; transform: scale(0.88); }
  to   { opacity: 1; transform: scale(1); }
}
@keyframes esb-live-blink {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.25; }
}
@keyframes esb-star-pop {
  0%   { transform: scale(0) rotate(-20deg); opacity: 0; }
  60%  { transform: scale(1.3) rotate(6deg); opacity: 1; }
  100% { transform: scale(1) rotate(0deg); opacity: 1; }
}

/* ── Root variables — light mode ────────────────────────────────────────── */
#${ROOT_ID} {
  /* Brand */
  --brand:       #f5a83d;
  --brand-dark:  #d69336;
  --brand-ink:   #7a5a1f;
  --brand-soft:  #fff4e0;
  --brand-glow:  rgba(245, 168, 61, 0.35);

  /* Solved cells */
  --solve-bg:    #d1fae5;
  --solve-ink:   #065f46;
  --solve-border:#6ee7b7;

  /* First-blood cells */
  --fb-bg:       #fffbeb;
  --fb-ink:      #92400e;
  --fb-border:   #f59e0b;
  --fb-star:     #d97706;
  --fb-shimmer-a:#fef3c7;
  --fb-shimmer-b:#fde68a;
  --fb-shimmer-c:#fef9c3;

  /* Empty cells */
  --empty-bg:    #f8fafc;
  --empty-ink:   #94a3b8;

  /* Page chrome */
  --ink:         #0f172a;
  --ink-2:       #334155;
  --muted:       #64748b;
  --line:        #e2e8f0;
  --line-strong: #cbd5e1;
  --surface:     #ffffff;
  --surface-2:   #f8fafc;

  /* Header row backgrounds */
  --thead-bg:    #1e1e2e;
  --thead-cat-bg:#2a2a3e;
  --thead-ink:   #e2e8f0;
  --thead-brand: #f5a83d;

  /* Rank row accents */
  --gold-bg:     linear-gradient(135deg, #fef3c7 0%, #fde68a 50%, #fcd34d 100%);
  --gold-ink:    #78350f;
  --gold-left:   #f59e0b;

  --silver-bg:   linear-gradient(135deg, #f1f5f9 0%, #e2e8f0 50%, #cbd5e1 100%);
  --silver-ink:  #334155;
  --silver-left: #94a3b8;

  --bronze-bg:   linear-gradient(135deg, #fff7ed 0%, #fed7aa 50%, #fdba74 100%);
  --bronze-ink:  #7c2d12;
  --bronze-left: #ea580c;

  /* Misc */
  --radius:      10px;
  --mono:        'JetBrains Mono', ui-monospace, 'SF Mono', 'Consolas', monospace;
  --sans:        'Pretendard Variable', Pretendard, -apple-system, BlinkMacSystemFont,
                 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif;

  font-family: var(--sans);
  color: var(--ink);
  margin: 3.5rem 0 2.5rem;
  display: block;
  contain: layout;
}

/* ── Dark mode override (class or ?dark=1) ──────────────────────────────── */
#${ROOT_ID}.esb-dark,
@media (prefers-color-scheme: dark) {
  #${ROOT_ID}:not(.esb-light) {
    --solve-bg:    rgba(16, 185, 129, 0.16);
    --solve-ink:   #6ee7b7;
    --solve-border:#065f46;

    --fb-bg:       rgba(245, 158, 11, 0.14);
    --fb-ink:      #fbbf24;
    --fb-border:   #d97706;
    --fb-star:     #fbbf24;
    --fb-shimmer-a:rgba(245,168,61,0.08);
    --fb-shimmer-b:rgba(245,168,61,0.22);
    --fb-shimmer-c:rgba(245,168,61,0.06);

    --empty-bg:    rgba(255,255,255,0.03);
    --empty-ink:   #475569;

    --ink:         #e2e8f0;
    --ink-2:       #94a3b8;
    --muted:       #64748b;
    --line:        rgba(255,255,255,0.07);
    --line-strong: rgba(255,255,255,0.12);
    --surface:     #1e1e2e;
    --surface-2:   #16161f;

    --thead-bg:    #13131c;
    --thead-cat-bg:#0f0f18;
    --thead-ink:   #94a3b8;

    --gold-bg:     linear-gradient(135deg, rgba(253,230,138,0.18) 0%, rgba(252,211,77,0.24) 100%);
    --gold-ink:    #fcd34d;
    --gold-left:   #f59e0b;

    --silver-bg:   linear-gradient(135deg, rgba(226,232,240,0.08) 0%, rgba(203,213,225,0.14) 100%);
    --silver-ink:  #cbd5e1;
    --silver-left: #64748b;

    --bronze-bg:   linear-gradient(135deg, rgba(254,215,170,0.12) 0%, rgba(253,186,116,0.18) 100%);
    --bronze-ink:  #fdba74;
    --bronze-left: #ea580c;
  }
}

/* Apply dark explicitly when the class is set (overrides media query check above) */
#${ROOT_ID}.esb-dark {
  --solve-bg:    rgba(16, 185, 129, 0.16);
  --solve-ink:   #6ee7b7;
  --solve-border:#065f46;
  --fb-bg:       rgba(245, 158, 11, 0.14);
  --fb-ink:      #fbbf24;
  --fb-border:   #d97706;
  --fb-star:     #fbbf24;
  --fb-shimmer-a:rgba(245,168,61,0.08);
  --fb-shimmer-b:rgba(245,168,61,0.22);
  --fb-shimmer-c:rgba(245,168,61,0.06);
  --empty-bg:    rgba(255,255,255,0.03);
  --empty-ink:   #475569;
  --ink:         #e2e8f0;
  --ink-2:       #94a3b8;
  --muted:       #64748b;
  --line:        rgba(255,255,255,0.07);
  --line-strong: rgba(255,255,255,0.12);
  --surface:     #1e1e2e;
  --surface-2:   #16161f;
  --thead-bg:    #13131c;
  --thead-cat-bg:#0f0f18;
  --thead-ink:   #94a3b8;
  --gold-bg:     linear-gradient(135deg, rgba(253,230,138,0.18) 0%, rgba(252,211,77,0.24) 100%);
  --gold-ink:    #fcd34d;
  --gold-left:   #f59e0b;
  --silver-bg:   linear-gradient(135deg, rgba(226,232,240,0.08) 0%, rgba(203,213,225,0.14) 100%);
  --silver-ink:  #cbd5e1;
  --silver-left: #64748b;
  --bronze-bg:   linear-gradient(135deg, rgba(254,215,170,0.12) 0%, rgba(253,186,116,0.18) 100%);
  --bronze-ink:  #fdba74;
  --bronze-left: #ea580c;
}

/* ── Fullscreen projection mode (.esb-fullscreen or double-click toggle) ── */
#${ROOT_ID}.esb-fullscreen {
  position: fixed;
  inset: 0;
  z-index: 9999;
  margin: 0;
  padding: 1.5rem 2rem 1rem;
  background: var(--surface-2);
  overflow-y: auto;
  border-radius: 0;
}
#${ROOT_ID}.esb-fullscreen .esb-table { font-size: 1.05rem; }
#${ROOT_ID}.esb-fullscreen .medal-emoji { font-size: 1.6rem; }
#${ROOT_ID}.esb-fullscreen .esb-title  { font-size: 2rem; }

/* ── Header ─────────────────────────────────────────────────────────────── */
#${ROOT_ID} .esb-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1.5rem;
  gap: 1rem;
}
#${ROOT_ID} .esb-head-left {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
#${ROOT_ID} .esb-head-right {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}
#${ROOT_ID} .esb-badge {
  display: inline-flex;
  align-items: center;
  padding: 0.22rem 0.65rem;
  font-family: var(--mono);
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.12em;
  color: var(--surface);
  background: var(--brand);
  border-radius: 5px;
  /* Subtle inner highlight */
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.25), 0 1px 3px rgba(0,0,0,0.15);
  user-select: none;
}
#${ROOT_ID} .esb-title {
  margin: 0;
  font-family: var(--sans);
  font-size: 1.55rem;
  font-weight: 800;
  letter-spacing: -0.03em;
  color: var(--ink);
}
#${ROOT_ID} .esb-live-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #22c55e;
  animation: esb-live-blink 2s ease-in-out infinite;
  flex-shrink: 0;
}
#${ROOT_ID} .esb-meta {
  font-family: var(--mono);
  font-size: 0.76rem;
  color: var(--muted);
  letter-spacing: 0.01em;
}
#${ROOT_ID} .ticker {
  font-weight: 700;
  color: var(--brand-dark);
  font-variant-numeric: tabular-nums;
}
#${ROOT_ID} .esb-fullscreen-btn {
  background: none;
  border: 1px solid var(--line-strong);
  color: var(--muted);
  border-radius: 5px;
  padding: 0.15rem 0.4rem;
  font-size: 0.9rem;
  cursor: pointer;
  line-height: 1;
  transition: border-color 0.15s, color 0.15s;
}
#${ROOT_ID} .esb-fullscreen-btn:hover {
  border-color: var(--brand);
  color: var(--brand-dark);
}

/* ── Table wrapper ───────────────────────────────────────────────────────── */
#${ROOT_ID} .esb-wrap {
  width: 100%;
  overflow-x: auto;
  border-radius: var(--radius);
  border: 1px solid var(--line-strong);
  background: var(--surface);
  /* Outer glow that makes it lift off the CTFd page */
  box-shadow:
    0 4px 6px -1px rgba(0,0,0,0.07),
    0 10px 24px -4px rgba(0,0,0,0.08),
    0 0 0 1px rgba(255,255,255,0.04);
}

/* ── Table base ──────────────────────────────────────────────────────────── */
#${ROOT_ID} .esb-table {
  border-collapse: separate;
  border-spacing: 0;
  width: 100%;
  font-family: var(--mono);
  font-variant-numeric: tabular-nums;
  font-size: 0.86rem;
}

/* Column widths */
#${ROOT_ID} .c-rank   { width: 46px; }
#${ROOT_ID} .c-team   { width: auto; }
#${ROOT_ID} .c-solved { width: 54px; }
#${ROOT_ID} .c-score  { width: 66px; }
#${ROOT_ID} .c-prob   { width: 46px; }

/* ── Category header row ─────────────────────────────────────────────────── */
#${ROOT_ID} .tr-cats th {
  background: var(--thead-bg);
  color: var(--thead-ink);
  font-family: var(--sans);
  font-size: 0.7rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  padding: 0.55rem 0.5rem 0.4rem;
  text-align: center;
  border-bottom: 1px solid rgba(255,255,255,0.06);
  white-space: nowrap;
  position: sticky;
  top: 0;
  z-index: 3;
}
#${ROOT_ID} .h-cat {
  background: var(--thead-cat-bg) !important;
  color: var(--brand) !important;
  font-weight: 700;
  letter-spacing: 0.05em;
  border-left: 1px solid rgba(255,255,255,0.06);
  text-transform: uppercase;
  font-size: 0.67rem;
}
#${ROOT_ID} .h-cat:first-child { border-left: none; }

/* Non-category cells in the first header row */
#${ROOT_ID} .h-rank,
#${ROOT_ID} .h-team,
#${ROOT_ID} .h-solved,
#${ROOT_ID} .h-score {
  text-align: center;
}
#${ROOT_ID} .h-team {
  text-align: left;
  padding-left: 1rem;
  font-family: var(--sans);
  letter-spacing: 0;
  text-transform: none;
  font-size: 0.75rem;
}

/* ── Problem number sub-row ──────────────────────────────────────────────── */
#${ROOT_ID} .tr-probs th {
  background: var(--thead-bg);
  color: rgba(148,163,184,0.7);
  font-family: var(--mono);
  font-size: 0.72rem;
  font-weight: 500;
  padding: 0.35rem 0.3rem 0.5rem;
  text-align: center;
  border-bottom: 2px solid var(--brand);
  cursor: help;
  white-space: nowrap;
  position: sticky;
  top: 32px; /* ~height of tr-cats */
  z-index: 3;
}
#${ROOT_ID} .h-prob:hover { color: var(--thead-brand); }

/* Score column gets a brand accent border on right */
#${ROOT_ID} .h-score {
  border-right: 2px solid var(--brand);
}

/* ── Body rows ───────────────────────────────────────────────────────────── */
#${ROOT_ID} tbody tr {
  transition: background 0.2s;
}
#${ROOT_ID} tbody tr:hover > td {
  background-color: rgba(245,168,61,0.04) !important;
}

/* Top-3 rows: left accent stripe */
#${ROOT_ID} tbody tr.top3 > td:first-child {
  border-left: 4px solid transparent;
}
#${ROOT_ID} tbody tr.top3-1 > td:first-child { border-left-color: var(--gold-left); }
#${ROOT_ID} tbody tr.top3-2 > td:first-child { border-left-color: var(--silver-left); }
#${ROOT_ID} tbody tr.top3-3 > td:first-child { border-left-color: var(--bronze-left); }

/* Top-3 row background tint */
#${ROOT_ID} tbody tr.top3-1 > td { background: rgba(253, 230, 138, 0.06); }
#${ROOT_ID} tbody tr.top3-2 > td { background: rgba(226, 232, 240, 0.04); }
#${ROOT_ID} tbody tr.top3-3 > td { background: rgba(254, 215, 170, 0.05); }

#${ROOT_ID} tbody td {
  padding: 0.55rem 0.4rem;
  text-align: center;
  border-bottom: 1px solid var(--line);
  white-space: nowrap;
  color: var(--ink);
}

/* ── Rank cell ────────────────────────────────────────────────────────────── */
#${ROOT_ID} td.rank {
  font-family: var(--mono);
  font-weight: 700;
  font-size: 0.92rem;
  padding: 0.3rem 0.4rem;
}
#${ROOT_ID} .medal-emoji {
  font-size: 1.35rem;
  display: block;
  line-height: 1;
  /* Prevent emoji from being tiny in some OS renderers */
  font-family: "Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", sans-serif;
}
#${ROOT_ID} .rank-num { color: var(--muted); }

/* ── Team cell ────────────────────────────────────────────────────────────── */
#${ROOT_ID} td.team {
  text-align: left;
  padding-left: 1rem;
  font-family: var(--sans);
  font-weight: 700;
  font-size: 0.97rem;
  letter-spacing: -0.015em;
  color: var(--ink);
}
#${ROOT_ID} tr.top3-1 td.team { color: var(--gold-ink); }
#${ROOT_ID} tr.top3-2 td.team { color: var(--silver-ink); }
#${ROOT_ID} tr.top3-3 td.team { color: var(--bronze-ink); }

/* ── Solved + Score cells ────────────────────────────────────────────────── */
#${ROOT_ID} td.solved {
  font-weight: 700;
  font-size: 0.95rem;
  color: var(--ink);
}
#${ROOT_ID} td.score {
  font-weight: 800;
  font-size: 1rem;
  color: var(--brand-dark);
  border-right: 2px solid var(--brand);
  background: var(--brand-soft);
  letter-spacing: -0.01em;
}
/* dark: soften the score background */
#${ROOT_ID}.esb-dark td.score { background: rgba(245,168,61,0.08); }

/* ── Problem cells — shared base ─────────────────────────────────────────── */
#${ROOT_ID} td.cell {
  padding: 0;
  height: 42px;
  vertical-align: middle;
}
#${ROOT_ID} .cell-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 1px;
}
#${ROOT_ID} .cell .time {
  font-family: var(--mono);
  font-size: 0.78rem;
  font-weight: 600;
  line-height: 1;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}

/* Empty */
#${ROOT_ID} td.cell-empty {
  background: var(--empty-bg);
}
#${ROOT_ID} .dot {
  color: var(--empty-ink);
  font-size: 1.1rem;
  line-height: 1;
  font-family: var(--sans);
}

/* Solved */
#${ROOT_ID} td.cell-solved {
  background: var(--solve-bg);
  color: var(--solve-ink);
  box-shadow: inset 1px 0 0 var(--solve-border), inset -1px 0 0 var(--solve-border);
}
#${ROOT_ID} td.cell-solved .time { color: var(--solve-ink); }

/* First-blood — the centrepiece treatment */
#${ROOT_ID} td.cell-first {
  background:
    linear-gradient(
      105deg,
      var(--fb-shimmer-a) 0%,
      var(--fb-shimmer-b) 35%,
      var(--fb-shimmer-c) 50%,
      var(--fb-shimmer-b) 65%,
      var(--fb-shimmer-a) 100%
    );
  background-size: 200% auto;
  animation: esb-shimmer 3.5s linear infinite, esb-pulse-glow 2.8s ease-in-out infinite;
  color: var(--fb-ink);
  /* Inset border so it doesn't expand the cell */
  outline: 2px solid var(--fb-border);
  outline-offset: -2px;
  position: relative;
  z-index: 1;
}
#${ROOT_ID} td.cell-first .time {
  color: var(--fb-ink);
  font-weight: 700;
}

/* First-blood star — prominent, not a corner afterthought */
#${ROOT_ID} .fb-star {
  font-family: var(--sans);
  font-size: 0.85rem;
  line-height: 1;
  color: var(--fb-star);
  /* Pop animation plays once on mount via .cell-fadein */
  display: block;
  transform-origin: center;
}

/* ── Cell fade-in animation (applied by JS after mount) ──────────────────── */
#${ROOT_ID} .cell-fadein {
  animation:
    esb-fadein 0.35s cubic-bezier(0.34, 1.56, 0.64, 1) both,
    /* first-blood cells keep their shimmer after fadein */
    esb-shimmer 3.5s linear var(--_shimmer-delay, 0.4s) infinite;
  /* re-attach pulse glow for first-blood */
}
#${ROOT_ID} td.cell-first.cell-fadein {
  animation:
    esb-fadein 0.35s cubic-bezier(0.34, 1.56, 0.64, 1) both,
    esb-shimmer 3.5s linear 0.4s infinite,
    esb-pulse-glow 2.8s ease-in-out 0.75s infinite;
}
#${ROOT_ID} td.cell-first.cell-fadein .fb-star {
  animation: esb-star-pop 0.5s cubic-bezier(0.34, 1.56, 0.64, 1) both;
}

/* ── Footer legend ────────────────────────────────────────────────────────── */
#${ROOT_ID} .esb-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 1.5rem;
  margin-top: 1rem;
  font-family: var(--sans);
  font-size: 0.76rem;
  color: var(--muted);
}
#${ROOT_ID} .legend {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
}
#${ROOT_ID} .swatch {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 18px;
  border-radius: 4px;
  font-size: 0.75rem;
  font-weight: 700;
  flex-shrink: 0;
}
#${ROOT_ID} .sw-first {
  background: var(--fb-bg);
  outline: 2px solid var(--fb-border);
  outline-offset: -2px;
  color: var(--fb-star);
  animation: esb-pulse-glow 2.8s ease-in-out infinite;
}
#${ROOT_ID} .sw-solved {
  background: var(--solve-bg);
  box-shadow: inset 1px 0 0 var(--solve-border), inset -1px 0 0 var(--solve-border);
}
#${ROOT_ID} .sw-empty {
  background: var(--empty-bg);
  color: var(--empty-ink);
  border: 1px solid var(--line-strong);
}

/* ── Responsive ──────────────────────────────────────────────────────────── */
@media (max-width: 768px) {
  #${ROOT_ID} { margin-top: 2rem; }
  #${ROOT_ID} .esb-table { font-size: 0.78rem; }
  #${ROOT_ID} .c-prob { width: 38px; }
  #${ROOT_ID} td.cell { height: 36px; }
  #${ROOT_ID} .cell .time { font-size: 0.7rem; }
  #${ROOT_ID} .fb-star { font-size: 0.75rem; }
  #${ROOT_ID} .medal-emoji { font-size: 1.1rem; }
  #${ROOT_ID} .esb-title { font-size: 1.2rem; }
}
@media (max-width: 480px) {
  #${ROOT_ID} .esb-head { flex-direction: column; align-items: flex-start; gap: 0.5rem; }
  #${ROOT_ID} .c-rank   { width: 36px; }
  #${ROOT_ID} .c-solved { width: 44px; }
  #${ROOT_ID} .c-score  { width: 54px; }
  #${ROOT_ID} .c-prob   { width: 34px; }
}
`;

    const style = document.createElement("style");
    style.id = "econ-icpc-styles";
    style.textContent = css;
    document.head.appendChild(style);
  }
})();
