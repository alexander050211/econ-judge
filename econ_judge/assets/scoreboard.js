// ICPC-style scoreboard matrix below CTFd's stock /scoreboard team table.
// Rows = teams, columns = problems, cells = solve time (minutes from contest
// start) with gold first-blood highlighting. Auto-refreshes every 15s when
// the tab is visible; pauses while hidden.
// Injected globally via theme_header but no-ops outside /scoreboard.

(function () {
  "use strict";
  if (location.pathname !== "/scoreboard") return;

  const REFRESH_MS = 15000;
  const ROOT_ID = "econ-icpc-board";

  let refreshTimer = setTimeout(reloadPage, REFRESH_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      clearTimeout(refreshTimer);
      refreshTimer = null;
    } else if (!refreshTimer) {
      refreshTimer = setTimeout(reloadPage, REFRESH_MS);
    }
  });

  function reloadPage() {
    location.reload();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  function boot() {
    injectStyles();
    render().catch((e) => console.warn("econ-judge ICPC board:", e));
  }

  async function render() {
    const [chalsResp, boardResp] = await Promise.all([
      fetch("/api/v1/challenges", { credentials: "same-origin" }),
      fetch("/api/v1/scoreboard/top/100", { credentials: "same-origin" }),
    ]);
    if (!chalsResp.ok || !boardResp.ok) return;
    const chals = ((await chalsResp.json()).data || []).slice().sort((a, b) => a.id - b.id);
    const board = ((await boardResp.json()).data) || {};
    const teams = Object.values(board).slice().sort(
      (a, b) => (b.score || 0) - (a.score || 0)
    );
    if (!chals.length || !teams.length) return;

    // Determine contest start = earliest solve across all teams.
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
          if (!bestDate || d < bestDate) {
            bestDate = d;
            bestTeam = t.name;
          }
        }
      }
      if (bestTeam) firstSolverByCid.set(c.id, bestTeam);
    }

    // Per-team solve map for cell lookup.
    for (const t of teams) {
      t._byCid = new Map();
      for (const s of t.solves || []) {
        t._byCid.set(s.challenge_id, new Date(s.date));
      }
    }

    const mount = ensureMount();
    mount.innerHTML = buildHtml(chals, teams, contestStart, firstSolverByCid);
  }

  function buildHtml(chals, teams, contestStart, firstSolverByCid) {
    const head = `
      <header class="esb-head">
        <h2>도전 현황 <span class="esb-style">ICPC</span></h2>
        <span class="esb-meta">실시간 갱신 · 15초 주기</span>
      </header>
    `;

    const colgroup = `<colgroup>
      <col class="c-rank"><col class="c-team"><col class="c-solved"><col class="c-score">
      ${chals.map(() => '<col class="c-prob">').join("")}
    </colgroup>`;

    const thead = `<thead>
      <tr>
        <th class="h-rank">#</th>
        <th class="h-team">팀</th>
        <th class="h-solved">통과</th>
        <th class="h-score">점수</th>
        ${chals.map((c) => (
          `<th class="h-prob" title="${esc(c.name)} (${c.value}pt)">${c.id}</th>`
        )).join("")}
      </tr>
    </thead>`;

    const rows = teams.map((t, i) => {
      const rank = i + 1;
      const rankClass = rank === 1 ? "gold" : rank === 2 ? "silver" : rank === 3 ? "bronze" : "";
      const solveCount = (t.solves || []).length;

      const cells = chals.map((c) => {
        const d = t._byCid.get(c.id);
        if (!d) return '<td class="cell cell-empty">·</td>';
        const minutes = Math.max(0, Math.round((d - contestStart) / 60000));
        const isFirst = firstSolverByCid.get(c.id) === t.name;
        return (
          `<td class="cell ${isFirst ? "cell-first" : "cell-solved"}">` +
            `<span class="time">${minutes}</span>` +
            (isFirst ? '<span class="star" aria-label="first solve">★</span>' : "") +
          "</td>"
        );
      }).join("");

      return (
        `<tr>` +
          `<td class="rank ${rankClass}">${rank}</td>` +
          `<td class="team">${esc(t.name)}</td>` +
          `<td class="solved">${solveCount}</td>` +
          `<td class="score">${t.score || 0}</td>` +
          cells +
        `</tr>`
      );
    }).join("");

    return head +
      `<div class="esb-wrap">` +
        `<table class="esb-table">${colgroup}${thead}<tbody>${rows}</tbody></table>` +
      `</div>` +
      `<footer class="esb-foot">` +
        `<span class="legend"><span class="swatch sw-first">★</span> 최초 통과</span>` +
        `<span class="legend"><span class="swatch sw-solved"></span> 통과 (분 = 시작 후 경과 시간)</span>` +
        `<span class="legend"><span class="swatch sw-empty">·</span> 미제출</span>` +
      `</footer>`;
  }

  function ensureMount() {
    let mount = document.getElementById(ROOT_ID);
    if (mount) return mount;
    mount = document.createElement("section");
    mount.id = ROOT_ID;
    const target =
      document.querySelector("main .container") ||
      document.querySelector(".container") ||
      document.body;
    target.appendChild(mount);
    return mount;
  }

  function esc(s) {
    const div = document.createElement("div");
    div.textContent = String(s == null ? "" : s);
    return div.innerHTML;
  }

  function injectStyles() {
    if (document.getElementById("econ-icpc-styles")) return;
    const css = `
#${ROOT_ID} {
  --b: #f5a83d;
  --b-ink: #7a5a1f;
  --b-dark: #d69336;
  --b-soft: #fff4e0;

  --solve-bg: #d1fae5;
  --solve-ink: #047857;
  --solve-line: #6ee7b7;

  --first-bg: #fef3c7;
  --first-ink: #b45309;
  --first-line: #f59e0b;

  --empty-bg: #fafafa;
  --empty-ink: #cbd5e1;

  --ink: #0f172a;
  --muted: #64748b;
  --line: #e2e8f0;
  --line-strong: #cbd5e1;

  font-family: 'Pretendard Variable', Pretendard, -apple-system, BlinkMacSystemFont,
    'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif;
  color: var(--ink);
  margin: 3rem 0 2rem;
  display: block;
}

#${ROOT_ID} .esb-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 1.25rem;
  padding-bottom: 0.75rem;
  border-bottom: 2px solid var(--b);
}
#${ROOT_ID} .esb-head h2 {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 700;
  letter-spacing: -0.02em;
}
#${ROOT_ID} .esb-style {
  display: inline-block;
  vertical-align: middle;
  margin-left: 0.5rem;
  padding: 0.18rem 0.55rem;
  font-family: ui-monospace, 'SF Mono', monospace;
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--b-ink);
  background: var(--b-soft);
  border-radius: 4px;
  border: 1px solid rgba(245, 168, 61, 0.3);
}
#${ROOT_ID} .esb-meta {
  font-family: ui-monospace, 'SF Mono', monospace;
  font-size: 0.78rem;
  color: var(--muted);
  letter-spacing: 0.02em;
}

#${ROOT_ID} .esb-wrap {
  width: 100%;
  overflow-x: auto;
  border-radius: 8px;
  border: 1px solid var(--line-strong);
  background: white;
}

#${ROOT_ID} .esb-table {
  border-collapse: separate;
  border-spacing: 0;
  width: 100%;
  font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', 'Consolas', monospace;
  font-variant-numeric: tabular-nums;
  font-size: 0.85rem;
}
#${ROOT_ID} colgroup .c-rank { width: 38px; }
#${ROOT_ID} colgroup .c-team { width: auto; }
#${ROOT_ID} colgroup .c-solved { width: 56px; }
#${ROOT_ID} colgroup .c-score { width: 60px; }
#${ROOT_ID} colgroup .c-prob { width: 44px; }

#${ROOT_ID} thead th {
  position: sticky;
  top: 0;
  background: var(--b);
  color: white;
  font-weight: 700;
  font-size: 0.72rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  padding: 0.7rem 0.4rem;
  border-bottom: 2px solid var(--b-dark);
  text-align: center;
  white-space: nowrap;
}
#${ROOT_ID} thead .h-team {
  text-align: left;
  padding-left: 0.9rem;
  font-family: 'Pretendard Variable', sans-serif;
  letter-spacing: 0;
  text-transform: none;
  font-size: 0.78rem;
}
#${ROOT_ID} thead .h-prob {
  font-family: ui-monospace, 'SF Mono', monospace;
  font-size: 0.78rem;
  letter-spacing: 0;
  cursor: help;
}

#${ROOT_ID} tbody tr { border-top: 1px solid var(--line); }
#${ROOT_ID} tbody tr:nth-child(even) { background: rgba(15, 23, 42, 0.012); }
#${ROOT_ID} tbody td {
  padding: 0.6rem 0.4rem;
  text-align: center;
  border-bottom: 1px solid var(--line);
  white-space: nowrap;
}
#${ROOT_ID} tbody td.rank {
  font-weight: 700;
  font-size: 0.95rem;
  color: var(--muted);
}
#${ROOT_ID} tbody td.rank.gold   { color: #92400e; background: linear-gradient(180deg, #fde68a 0%, #fcd34d 100%); }
#${ROOT_ID} tbody td.rank.silver { color: #475569; background: linear-gradient(180deg, #e2e8f0 0%, #cbd5e1 100%); }
#${ROOT_ID} tbody td.rank.bronze { color: #7c2d12; background: linear-gradient(180deg, #fed7aa 0%, #fdba74 100%); }
#${ROOT_ID} tbody td.team {
  text-align: left;
  padding-left: 0.9rem;
  font-family: 'Pretendard Variable', sans-serif;
  font-weight: 600;
  font-size: 0.95rem;
  color: var(--ink);
  letter-spacing: -0.01em;
}
#${ROOT_ID} tbody td.solved {
  font-weight: 700;
  color: var(--ink);
}
#${ROOT_ID} tbody td.score {
  font-weight: 700;
  color: var(--b-ink);
  background: var(--b-soft);
  border-right: 2px solid var(--b);
  font-size: 0.95rem;
}

#${ROOT_ID} tbody td.cell {
  padding: 0;
  height: 38px;
  vertical-align: middle;
  position: relative;
}
#${ROOT_ID} tbody td.cell .time {
  display: inline-block;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
#${ROOT_ID} tbody td.cell .star {
  position: absolute;
  top: 2px;
  right: 3px;
  font-size: 8px;
  color: var(--first-line);
  line-height: 1;
}
#${ROOT_ID} td.cell-empty {
  background: var(--empty-bg);
  color: var(--empty-ink);
}
#${ROOT_ID} td.cell-solved {
  background: var(--solve-bg);
  color: var(--solve-ink);
}
#${ROOT_ID} td.cell-first {
  background: var(--first-bg);
  color: var(--first-ink);
  box-shadow: inset 0 0 0 2px var(--first-line);
}
#${ROOT_ID} td.cell-first .time { font-weight: 700; }

#${ROOT_ID} .esb-foot {
  display: flex;
  flex-wrap: wrap;
  gap: 1.25rem;
  margin-top: 0.75rem;
  font-size: 0.78rem;
  color: var(--muted);
}
#${ROOT_ID} .legend {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
}
#${ROOT_ID} .swatch {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 16px;
  border-radius: 3px;
  font-size: 9px;
  border: 1px solid transparent;
}
#${ROOT_ID} .sw-first {
  background: var(--first-bg);
  box-shadow: inset 0 0 0 2px var(--first-line);
  color: var(--first-line);
  font-weight: 700;
}
#${ROOT_ID} .sw-solved {
  background: var(--solve-bg);
  border-color: var(--solve-line);
}
#${ROOT_ID} .sw-empty {
  background: var(--empty-bg);
  color: var(--empty-ink);
}

@media (max-width: 720px) {
  #${ROOT_ID} { margin-top: 2rem; }
  #${ROOT_ID} .esb-table { font-size: 0.78rem; }
  #${ROOT_ID} colgroup .c-prob { width: 38px; }
  #${ROOT_ID} tbody td.cell { height: 32px; }
}
`;
    const style = document.createElement("style");
    style.id = "econ-icpc-styles";
    style.textContent = css;
    document.head.appendChild(style);
  }
})();
