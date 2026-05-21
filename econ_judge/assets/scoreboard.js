// econ-judge scoreboard augmentation:
//   1. Auto-refresh every 15s on /scoreboard only (not on challenge pages).
//   2. Per-challenge widget below the team table — first solver + solve count,
//      grouped by category, shade-coded by completion state.
// Injected globally via theme_header but no-ops outside /scoreboard.

(function () {
  "use strict";
  if (location.pathname !== "/scoreboard") return;

  const REFRESH_MS = 15000;
  const ROOT_ID = "econ-scoreboard-widget";

  // Schedule the next reload. Set up early so a failed widget render still
  // refreshes the page; cleared if user is interacting.
  let refreshTimer = setTimeout(reloadPage, REFRESH_MS);
  // Pause auto-refresh while the tab is hidden (no point burning Render
  // free-tier wake-ups when nobody's watching).
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      clearTimeout(refreshTimer);
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
    render().catch((e) =>
      console.warn("econ-judge scoreboard widget:", e)
    );
  }

  async function render() {
    const [chalsResp, boardResp] = await Promise.all([
      fetch("/api/v1/challenges", { credentials: "same-origin" }),
      fetch("/api/v1/scoreboard/top/100", { credentials: "same-origin" }),
    ]);
    if (!chalsResp.ok || !boardResp.ok) return;
    const chals = ((await chalsResp.json()).data) || [];
    const board = ((await boardResp.json()).data) || {};
    const teams = Object.values(board);
    const teamCount = teams.length;

    if (!chals.length) return;

    // Build per-challenge stats keyed by chal id
    const stats = new Map();
    for (const c of chals) {
      stats.set(c.id, {
        id: c.id,
        name: c.name,
        category: c.category || "기타",
        value: c.value,
        solvers: [],
      });
    }
    for (const team of teams) {
      for (const solve of team.solves || []) {
        const s = stats.get(solve.challenge_id);
        if (!s) continue;
        s.solvers.push({ name: team.name, date: new Date(solve.date) });
      }
    }
    for (const s of stats.values()) {
      s.solvers.sort((a, b) => a.date - b.date);
    }

    // Group by category, preserving the ordering of CATEGORY_ORDER
    const CATEGORY_ORDER = ["연습", "미션", "Project 1", "Project 2"];
    const byCat = new Map(CATEGORY_ORDER.map((c) => [c, []]));
    for (const s of stats.values()) {
      const arr = byCat.get(s.category) || (() => { const a = []; byCat.set(s.category, a); return a; })();
      arr.push(s);
    }
    for (const arr of byCat.values()) arr.sort((a, b) => a.id - b.id);

    // Mount point: append once, replace contents on subsequent renders
    let mount = document.getElementById(ROOT_ID);
    if (!mount) {
      mount = document.createElement("section");
      mount.id = ROOT_ID;
      const target =
        document.querySelector("main .container") ||
        document.querySelector(".container") ||
        document.body;
      target.appendChild(mount);
    }

    const sections = [];
    for (const [cat, items] of byCat) {
      if (!items.length) continue;
      sections.push(renderCategory(cat, items, teamCount));
    }

    mount.innerHTML =
      '<header class="esb-head">' +
        '<h2>도전 현황</h2>' +
        '<span class="esb-meta">실시간 갱신 · 15초 주기</span>' +
      '</header>' +
      sections.join("");
  }

  function renderCategory(cat, items, teamCount) {
    const cards = items.map((c) => renderCard(c, teamCount)).join("");
    return (
      '<div class="esb-cat">' +
        '<h3 class="esb-cat-title">' +
          esc(cat) +
          ' <span class="esb-cat-sub">' + items.length + '개 문항</span>' +
        '</h3>' +
        '<div class="esb-grid">' + cards + '</div>' +
      '</div>'
    );
  }

  function renderCard(c, teamCount) {
    const count = c.solvers.length;
    let stateClass = "is-unsolved";
    if (count > 0 && count >= teamCount && teamCount > 0) {
      stateClass = "is-allsolved";
    } else if (count > 0) {
      stateClass = "is-partial";
    }
    const first = c.solvers[0];
    const firstHtml = first
      ? '<div class="esb-foot">' +
          '<span class="esb-first-label">최초 통과</span>' +
          '<span class="esb-first-name">' + esc(first.name) + '</span>' +
        '</div>'
      : '<div class="esb-foot esb-foot-empty"><span class="esb-first-label">최초 통과 대기 중</span></div>';

    return (
      '<article class="esb-card ' + stateClass + '">' +
        '<div class="esb-card-top">' +
          '<span class="esb-val">' + c.value + ' <em>pt</em></span>' +
          '<span class="esb-count">' +
            '<strong>' + count + '</strong>' +
            '<span class="esb-of">/' + (teamCount || "—") + '</span>' +
          '</span>' +
        '</div>' +
        '<h4 class="esb-name">' + esc(c.name) + '</h4>' +
        firstHtml +
      '</article>'
    );
  }

  function esc(s) {
    const div = document.createElement("div");
    div.textContent = String(s == null ? "" : s);
    return div.innerHTML;
  }

  function injectStyles() {
    if (document.getElementById("econ-scoreboard-styles")) return;
    const css = `
#${ROOT_ID} {
  --esb-brand: #f5a83d;
  --esb-brand-dark: #d69336;
  --esb-brand-ink: #7a5a1f;
  --esb-brand-soft: #fff4e0;
  --esb-pass: #047857;
  --esb-pass-soft: #ecfdf5;
  --esb-pass-line: #a7f3d0;
  --esb-ink: #0f172a;
  --esb-muted: #64748b;
  --esb-line: #e5e7eb;
  --esb-surface: #ffffff;

  font-family: 'Pretendard Variable', Pretendard, -apple-system, BlinkMacSystemFont,
    'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif;
  color: var(--esb-ink);
  margin: 3rem 0 2rem;
  display: block;
}

#${ROOT_ID} .esb-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 1.5rem;
  padding-bottom: 0.75rem;
  border-bottom: 2px solid var(--esb-brand);
}
#${ROOT_ID} .esb-head h2 {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--esb-ink);
}
#${ROOT_ID} .esb-meta {
  font-size: 0.78rem;
  color: var(--esb-muted);
  letter-spacing: 0.02em;
  font-family: ui-monospace, 'SF Mono', monospace;
}

#${ROOT_ID} .esb-cat {
  margin-bottom: 2rem;
}
#${ROOT_ID} .esb-cat-title {
  display: flex;
  align-items: baseline;
  gap: 0.75rem;
  margin: 0 0 0.85rem;
  font-size: 1.05rem;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--esb-brand-ink);
}
#${ROOT_ID} .esb-cat-sub {
  font-family: ui-monospace, 'SF Mono', monospace;
  font-size: 0.72rem;
  color: var(--esb-muted);
  font-weight: 500;
  padding: 0.18rem 0.45rem;
  background: var(--esb-brand-soft);
  border-radius: 999px;
}

#${ROOT_ID} .esb-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 0.75rem;
}

#${ROOT_ID} .esb-card {
  position: relative;
  background: var(--esb-surface);
  border: 1px solid var(--esb-line);
  border-radius: 12px;
  padding: 0.85rem 1rem 0.75rem;
  transition: border-color 200ms ease, box-shadow 200ms ease;
  overflow: hidden;
}
#${ROOT_ID} .esb-card::before {
  content: '';
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  background: var(--esb-line);
  border-radius: 3px 0 0 3px;
  transition: background 200ms ease;
}
#${ROOT_ID} .esb-card.is-partial { border-color: rgba(245, 168, 61, 0.4); }
#${ROOT_ID} .esb-card.is-partial::before { background: var(--esb-brand); }
#${ROOT_ID} .esb-card.is-allsolved {
  border-color: var(--esb-pass-line);
  background: var(--esb-pass-soft);
}
#${ROOT_ID} .esb-card.is-allsolved::before { background: var(--esb-pass); }

#${ROOT_ID} .esb-card-top {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 0.4rem;
}
#${ROOT_ID} .esb-val {
  font-family: ui-monospace, 'SF Mono', monospace;
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--esb-brand-ink);
  letter-spacing: 0.01em;
}
#${ROOT_ID} .esb-val em {
  font-style: normal;
  font-weight: 400;
  color: var(--esb-muted);
  margin-left: 1px;
}
#${ROOT_ID} .esb-count {
  font-family: ui-monospace, 'SF Mono', monospace;
  font-variant-numeric: tabular-nums;
  font-size: 0.9rem;
  color: var(--esb-muted);
  line-height: 1;
}
#${ROOT_ID} .esb-count strong {
  font-size: 1.15rem;
  color: var(--esb-ink);
  font-weight: 700;
}
#${ROOT_ID} .esb-card.is-partial .esb-count strong { color: var(--esb-brand-ink); }
#${ROOT_ID} .esb-card.is-allsolved .esb-count strong { color: var(--esb-pass); }
#${ROOT_ID} .esb-of { letter-spacing: 0.02em; }

#${ROOT_ID} .esb-name {
  margin: 0 0 0.55rem;
  font-size: 0.93rem;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--esb-ink);
  line-height: 1.35;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

#${ROOT_ID} .esb-foot {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  padding-top: 0.5rem;
  border-top: 1px dashed var(--esb-line);
  font-size: 0.78rem;
}
#${ROOT_ID} .esb-foot-empty .esb-first-label { color: var(--esb-muted); font-style: italic; }
#${ROOT_ID} .esb-first-label {
  color: var(--esb-muted);
  letter-spacing: 0.01em;
  font-size: 0.72rem;
  text-transform: uppercase;
  font-weight: 600;
  letter-spacing: 0.05em;
}
#${ROOT_ID} .esb-first-name {
  font-weight: 700;
  color: var(--esb-brand-ink);
  letter-spacing: -0.01em;
  font-size: 0.88rem;
}
#${ROOT_ID} .esb-card.is-allsolved .esb-first-name { color: var(--esb-pass); }

@media (max-width: 640px) {
  #${ROOT_ID} { margin-top: 2rem; }
  #${ROOT_ID} .esb-grid { grid-template-columns: 1fr; }
}
`;
    const style = document.createElement("style");
    style.id = "econ-scoreboard-styles";
    style.textContent = css;
    document.head.appendChild(style);
  }
})();
