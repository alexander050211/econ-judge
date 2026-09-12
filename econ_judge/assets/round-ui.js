(function () {
  "use strict";

  const path = window.location.pathname;
  if (path !== "/my-score" && path !== "/projector") return;

  function esc(value) {
    return String(value == null ? "—" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function phaseCopy(phase) {
    return {
      before: ["시작 전", "1라운드 시작을 기다리고 있습니다."],
      round1: ["1라운드 진행 중", "1라운드 문제에 제출할 수 있습니다."],
      break: ["휴식 시간", "1라운드가 종료되었습니다. 2라운드를 준비하세요."],
      round2: ["2라운드 진행 중", "2라운드 문제에 제출할 수 있습니다."],
      finished: ["온라인 라운드 종료", "온라인 채점이 종료되었습니다."],
      open: ["준비 모드", "운영 검토를 위해 두 라운드가 모두 열려 있습니다."],
      misconfigured: ["일정 설정 필요", "운영진에게 알려주세요."],
    }[phase] || ["준비 중", "현재 상태를 확인하는 중입니다."];
  }

  const STANDING_NOTE = "각 라운드 점수는 해당 문제가 닫힌 뒤에도 온라인 총점에 계속 반영됩니다.";

  /* /my-score sends `frozen` + `frozen_at`, and the projector reports the same
     cutoff as phase "project". `frozen_at` is a Unix timestamp in seconds,
     shown on the viewer's own clock so a mentee reads it as camp-room time. */
  function freezeMoment(seconds) {
    if (!seconds) return "";
    const at = new Date(seconds * 1000);
    const pad = (part) => String(part).padStart(2, "0");
    return `${at.getMonth() + 1}월 ${at.getDate()}일 ${pad(at.getHours())}:${pad(at.getMinutes())}`;
  }

  function setPhase(id, label, frozen) {
    const pill = document.getElementById(id);
    pill.textContent = frozen ? `${label} · 동결` : label;
    pill.classList.toggle("er-phase-frozen", frozen);
  }

  function addStyle() {
    if (document.getElementById("econ-round-ui-style")) return;
    const style = document.createElement("style");
    style.id = "econ-round-ui-style";
    style.textContent = `
      .er-root{max-width:720px;margin:0 auto;padding:24px 20px 28px;font-family:var(--d-f-sans);font-size:14px;line-height:1.5;color:var(--d-ink)}
      .er-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:18px}.er-kicker{font-size:13px;font-weight:500;color:var(--d-text-2);margin-bottom:6px}.er-label{display:block;font-size:12.5px;color:var(--d-text-2);margin-bottom:4px}.er-h1{font-size:28px;font-weight:600;line-height:1.2;letter-spacing:-.015em;margin:0}.er-sub{color:var(--d-text-2);margin:6px 0 0}.er-phase{display:inline-flex;align-items:center;height:26px;padding:0 10px;border-radius:6px;background:var(--d-accent-soft);color:var(--d-accent-text);font-size:12.5px;font-weight:500;white-space:nowrap}.er-phase-frozen{background:var(--d-surface-2);color:var(--d-text-2)}
      .er-overview{display:grid;grid-template-columns:1.5fr 1fr 1fr;background:var(--d-surface);border:1px solid var(--d-border);border-radius:10px;box-shadow:var(--d-shadow-1);margin:0 0 14px}.er-panel{padding:14px 20px;border-left:1px solid var(--d-border);min-width:0}.er-panel:first-child{border-left:0}.er-panel strong{display:block;font-size:22px;font-weight:600;line-height:1.2;letter-spacing:-.01em;font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.er-panel:first-child strong{font-size:26px}.er-panel strong small{font-size:15px;font-weight:500;color:var(--d-text-3)}.er-panel:first-child strong small{font-size:18px}
      .er-rounds{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}.er-round{padding:18px;border:1px solid var(--d-border);border-radius:10px;background:var(--d-surface);box-shadow:var(--d-shadow-1)}.er-round-live{border-color:var(--d-accent)}.er-round-title{display:flex;align-items:center;gap:6px;margin-bottom:12px}.er-round h2,.er-round-state{display:inline-flex;align-items:center;height:24px;padding:0 8px;border-radius:6px;background:var(--d-surface-2);color:var(--d-text-2);font-size:12.5px;font-weight:500;white-space:nowrap;margin:0}.er-round-live h2{background:var(--d-accent-soft);color:var(--d-accent-text)}.er-round-live .er-round-state{background:var(--d-ok-soft);color:var(--d-ok-text)}.er-round-state:empty{display:none}.er-round-score{display:block;font-size:24px;font-weight:600;line-height:1.2;letter-spacing:-.01em;font-variant-numeric:tabular-nums;margin:0 0 10px}.er-round-score small{font-size:17px;font-weight:500;color:var(--d-text-3)}.er-progress{height:8px;background:var(--d-surface-2);border-radius:4px;overflow:hidden}.er-progress i{display:block;height:100%;border-radius:inherit;background:var(--d-accent);transition:width .22s var(--d-ease)}.er-round-foot{display:flex;justify-content:space-between;gap:10px;margin-top:10px;color:var(--d-text-2);font-size:13px;font-variant-numeric:tabular-nums}
      .er-leader{display:flex;align-items:center;gap:10px;min-height:44px;padding:10px 14px;border:1px solid var(--d-border);border-radius:8px;background:var(--d-surface-2);color:var(--d-text-2);font-size:13.5px}.er-leader strong{margin-left:auto;text-align:right;color:var(--d-ink);font-size:15px;font-weight:600;font-variant-numeric:tabular-nums}.er-foot{display:flex;align-items:flex-start;gap:8px;margin-top:14px;color:var(--d-text-3);font-size:12.5px}.er-foot .er-ic{width:14px;height:14px;margin-top:3px;flex:none;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}.er-note{margin:0}.er-error{margin-top:14px;padding:10px 12px;border:1px solid var(--d-bad-line);border-radius:8px;background:var(--d-bad-soft);color:var(--d-bad-text);font-size:13.5px;font-weight:500}
      .er-root [hidden]{display:none}
      /* The hall projector is dark whatever the CTFd theme toggle says, so it
         redeclares B's dark set locally instead of inheriting the page's. */
      .er-projector{--d-surface:#171a20;--d-surface-2:#1f232b;--d-border:#2a2f38;--d-border-strong:#3a404b;--d-ink:#eceef2;--d-text-2:#a3aab6;--d-text-3:#8a91a1;--d-accent:#6f92ff;--d-accent-soft:#1a2440;--d-accent-text:#9db4ff;--d-ok-soft:#10281f;--d-ok-text:#5fd9a4;--d-bad-soft:#2e1717;--d-bad-text:#ff8a85;--d-bad-line:#58393c;--d-shadow-1:0 0 0 1px rgba(255,255,255,.04),0 1px 2px rgba(0,0,0,.4);max-width:none;min-height:calc(100vh - 56px);padding:24px 32px 28px;background:#0f1115;color:var(--d-ink);display:flex;flex-direction:column}.er-projector .er-head{max-width:none;border-bottom:1px solid var(--d-border);padding-bottom:16px}.er-projector .er-h1{font-size:34px}
      .er-projector-main{flex:1;display:grid;place-content:center;justify-items:center;text-align:center;gap:20px}.er-projector-main .er-label{font-size:15px;margin:0}.er-projector-score{font-size:clamp(110px,22vw,260px);font-weight:700;letter-spacing:-.045em;line-height:.9;font-variant-numeric:tabular-nums}.er-projector-score small{font-size:.2em;font-weight:500;letter-spacing:-.01em;color:var(--d-text-2)}.er-projector-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;width:min(880px,80vw)}.er-projector-stats span{padding:14px 18px;border:1px solid var(--d-border);border-radius:10px;background:var(--d-surface);box-shadow:var(--d-shadow-1);text-align:left;color:var(--d-text-2);font-size:15px}.er-projector-stats b{display:block;margin-top:4px;font-size:clamp(24px,3vw,40px);font-weight:600;line-height:1.15;letter-spacing:-.02em;font-variant-numeric:tabular-nums;color:var(--d-ink)}
      .er-matrix{flex:1;display:flex;flex-direction:column;justify-content:center;gap:20px;padding:8px 0 4px;overflow:auto}.er-matrix-table{width:100%;border-collapse:collapse;table-layout:fixed;background:var(--d-surface)}.er-matrix-table th,.er-matrix-table td{border:1px solid var(--d-border);padding:12px 14px;text-align:center;position:relative}.er-matrix-table thead th{background:var(--d-surface-2)}.er-matrix-table thead th b{display:block;font:600 clamp(20px,2.2vw,34px) var(--d-f-mono);letter-spacing:.01em;font-variant-numeric:tabular-nums;color:var(--d-ink)}.er-matrix-table thead th span{display:block;margin-top:5px;font-size:clamp(12px,1.1vw,17px);font-weight:500;color:var(--d-text-2)}.er-matrix-table .er-matrix-corner{width:22%;text-align:left;font:500 12px var(--d-f-mono);letter-spacing:.12em;color:var(--d-text-2);text-transform:uppercase}.er-matrix-table tbody th{text-align:left;font-size:clamp(17px,1.9vw,28px);font-weight:600;letter-spacing:-.02em;color:var(--d-ink)}
      /* The filled dot is deliberately neutral, not green: this grid reports
         제출/미제출 only, and a "good" colour would read as a verdict. */
      .er-cell-in{background:var(--d-surface-2)}.er-dot{display:block;width:clamp(22px,2.4vw,40px);height:clamp(22px,2.4vw,40px);margin:0 auto;border-radius:50%;border:2px solid var(--d-border-strong);box-sizing:border-box}.er-dot-in{background:var(--d-text-2);border-color:var(--d-text-2)}.er-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}.er-matrix-legend{display:flex;flex-wrap:wrap;align-items:center;gap:24px;color:var(--d-text-2);font-size:clamp(13px,1.2vw,17px);font-weight:500}.er-matrix-legend span{display:flex;align-items:center;gap:10px}.er-matrix-legend .er-dot{width:20px;height:20px;margin:0}
      @media(max-width:720px){.er-root{padding:20px 16px 24px}.er-head{flex-direction:column;align-items:flex-start}.er-h1{font-size:24px}.er-overview{grid-template-columns:repeat(2,1fr)}.er-panel:nth-child(3){grid-column:1/-1;border-left:0;border-top:1px solid var(--d-border)}.er-rounds{grid-template-columns:1fr}.er-projector{padding:20px 16px}.er-projector .er-h1{font-size:26px}.er-projector-score{font-size:96px}.er-projector-stats{grid-template-columns:1fr;width:100%}}
      @media(prefers-reduced-motion:reduce){.er-root *{transition:none!important;animation:none!important}}
    `;
    document.head.appendChild(style);
  }

  function installScoreShell() {
    const old = document.getElementById("ms-root");
    if (!old) return null;
    old.outerHTML = `<main class="er-root" id="er-score-root"><header class="er-head"><div><div class="er-kicker">SNU SENS · E-CON 논설</div><h1 class="er-h1">내 점수</h1><p class="er-sub" id="er-score-message">점수를 불러오는 중입니다.</p></div><div class="er-phase" id="er-score-phase">—</div></header><section class="er-overview"><div class="er-panel"><span class="er-label">온라인 총점</span><strong id="er-total-score">— <small>/ 80 pt</small></strong></div><div class="er-panel"><span class="er-label">해결한 문제</span><strong id="er-total-solved">— <small>/ 15</small></strong></div><div class="er-panel"><span class="er-label">우리 조</span><strong id="er-team-name">—</strong></div></section><section class="er-rounds" id="er-rounds"></section><section class="er-leader"><span>익명 선두 조</span><strong id="er-leader">—</strong></section><div class="er-foot"><svg class="er-ic" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg><p class="er-note" id="er-note">${STANDING_NOTE}</p></div><div class="er-error" id="er-error" hidden></div></main>`;
    return document.getElementById("er-score-root");
  }

  function renderScore(data) {
    const competition = data.competition || {};
    const [label, message] = phaseCopy(competition.phase);
    const frozen = Boolean(data.frozen);
    setPhase("er-score-phase", label, frozen);
    document.getElementById("er-score-message").textContent = message;
    const team = data.team || {};
    document.getElementById("er-team-name").textContent = team.name || "—";
    document.getElementById("er-total-score").innerHTML = `${team.score || 0} <small>/ ${data.total_points || 80} pt</small>`;
    document.getElementById("er-total-solved").innerHTML = `${team.solved || 0} <small>/ ${data.total_challenges || 15}</small>`;
    const active = competition.phase === "round1" ? "round1" : competition.phase === "round2" ? "round2" : "";
    const rounds = team.rounds || {};
    document.getElementById("er-rounds").innerHTML = ["round1", "round2"].map((key) => {
      const r = rounds[key] || ((data.rounds || {})[key]) || {};
      const points = r.points || (key === "round1" ? 35 : 45);
      const solved = r.solved || 0;
      const score = r.score || 0;
      const count = r.challenge_count || (key === "round1" ? 8 : 7);
      const state = active === key ? "진행 중" : competition.phase === "finished" ? "종료" : "";
      return `<article class="er-round ${active === key ? "er-round-live" : ""}"><div class="er-round-title"><h2>${esc(r.label || (key === "round1" ? "1라운드" : "2라운드"))}</h2><span class="er-round-state">${state}</span></div><div class="er-round-score">${score}<small> / ${points} pt</small></div><div class="er-progress"><i style="width:${Math.min(100, Math.round(score / points * 100))}%"></i></div><div class="er-round-foot"><span>${solved} / ${count} 문제 해결</span><span>${points} pt</span></div></article>`;
    }).join("");
    const leader = data.leader;
    // Both freeze branches are written on every poll: a page opened before the
    // freeze instant stays open across it.
    document.getElementById("er-leader").textContent = frozen ? "캠프 종료 후 공개됩니다" : leader ? `${leader.score} / ${data.total_points || 80} pt` : "아직 점수가 없습니다";
    const moment = freezeMoment(data.frozen_at);
    document.getElementById("er-note").textContent = frozen
      ? `${moment ? `이 화면의 점수는 ${moment} 기준으로 동결되었습니다.` : "이 화면의 점수는 동결되었습니다."} 이후에 맞힌 문제도 점수에 그대로 반영되며, 최종 점수는 캠프 종료 후 공개됩니다.`
      : STANDING_NOTE;
  }

  function installProjectorShell() {
    const old = document.getElementById("pj-root");
    if (!old) return null;
    old.outerHTML = `<main class="er-root er-projector" id="er-projector-root"><header class="er-head"><div><div class="er-kicker">SNU SENS · E-CON 논설 · 운영 화면</div><h1 class="er-h1" id="er-projector-title">온라인 라운드</h1><p class="er-sub" id="er-projector-message">현황을 불러오는 중입니다.</p></div><div class="er-phase" id="er-projector-phase">—</div></header><section class="er-projector-main" id="er-projector-live"><div class="er-label">익명 선두 조 · 온라인 총점</div><div class="er-projector-score" id="er-projector-score">—<small> pt</small></div><div class="er-projector-stats"><span>최근 정답 <b id="er-projector-solves">—</b></span><span>최근 제출 <b id="er-projector-submits">—</b></span><span>참여 조 <b id="er-projector-teams">—</b></span></div></section><section class="er-matrix" id="er-projector-matrix" hidden></section><div class="er-error" id="er-projector-error" hidden></div></main>`;
    return document.getElementById("er-projector-root");
  }

  function renderProjectorLive(data) {
    document.getElementById("er-projector-score").innerHTML = `${(data.leader || {}).score == null ? "—" : data.leader.score}<small> / ${data.total_points || 80} pt</small>`;
    const momentum = data.momentum || {};
    document.getElementById("er-projector-solves").textContent = momentum.new_solves == null ? "—" : momentum.new_solves;
    document.getElementById("er-projector-submits").textContent = momentum.submits == null ? "—" : momentum.submits;
    document.getElementById("er-projector-teams").textContent = momentum.active_teams == null ? "—" : `${momentum.active_teams} / ${momentum.total_teams}`;
  }

  /* After CTFd freeze the projector payload carries no leader and no momentum —
     only cols + teams. Each cell is the submission boolean; pass/fail and score
     must never reach this screen. */
  function renderProjectorMatrix(data) {
    const board = document.getElementById("er-projector-matrix");
    const cols = Array.isArray(data.cols) ? data.cols : [];
    const teams = Array.isArray(data.teams) ? data.teams : [];
    if (!cols.length || !teams.length) {
      board.innerHTML = `<p class="er-sub">아직 제출한 조가 없습니다.</p>`;
      return;
    }
    const head = cols.map((col) => `<th scope="col"><b>${esc(col.short)}</b><span>${esc(col.name)}</span></th>`).join("");
    const rows = teams.map((team) => {
      const submits = team.submits || [];
      const cells = cols.map((_col, index) => {
        const done = Boolean(submits[index]);
        return `<td class="${done ? "er-cell-in" : ""}"><i class="er-dot${done ? " er-dot-in" : ""}" aria-hidden="true"></i><span class="er-sr">${done ? "제출" : "미제출"}</span></td>`;
      }).join("");
      return `<tr><th scope="row">${esc(team.name)}</th>${cells}</tr>`;
    }).join("");
    board.innerHTML = `<table class="er-matrix-table"><thead><tr><th scope="col" class="er-matrix-corner">조</th>${head}</tr></thead><tbody>${rows}</tbody></table><div class="er-matrix-legend"><span><i class="er-dot er-dot-in" aria-hidden="true"></i>제출 완료</span><span><i class="er-dot" aria-hidden="true"></i>미제출</span><span>제출 여부만 표시합니다 · 정답 여부는 공개하지 않습니다</span></div>`;
  }

  /* Poll only while the tab is visible, and refresh once on the way back so a
     returning viewer never reads a stale round off the screen. */
  function pollWhileVisible(tick, intervalMs) {
    let timer = null;
    function start() { if (!timer) timer = setInterval(tick, intervalMs); }
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) { clearInterval(timer); timer = null; return; }
      tick();
      start();
    });
    if (!document.hidden) start();
  }

  async function scorePage() {
    if (!installScoreShell()) return;
    async function tick() {
      try {
        const response = await fetch("/api/v1/digital/my-score", {credentials:"same-origin"});
        if (response.redirected || !response.ok) throw new Error("로그인이 필요합니다.");
        renderScore((await response.json()).data || {});
      } catch (error) {
        const el = document.getElementById("er-error"); el.hidden = false; el.textContent = error.message || "점수를 불러오지 못했습니다.";
      }
    }
    await tick(); pollWhileVisible(tick, 20000);
  }

  async function projectorPage() {
    if (!installProjectorShell()) return;
    async function tick() {
      try {
        const [statusResponse, dataResponse] = await Promise.all([fetch("/api/v1/digital/competition"), fetch("/api/v1/digital/projector", {credentials:"same-origin"})]);
        const status = statusResponse.ok ? (await statusResponse.json()).data || {} : {};
        if (dataResponse.redirected || !dataResponse.ok) throw new Error("운영진 로그인으로 이 화면을 열어주세요.");
        const data = (await dataResponse.json()).data || {};
        const [label, message] = phaseCopy(status.phase);
        const project = data.phase === "project";
        document.getElementById("er-projector-title").textContent = project ? "조별 제출 현황" : label;
        document.getElementById("er-projector-message").textContent = project ? "정답 여부가 아니라 제출 여부만 표시합니다." : message;
        // The projector has no `frozen` flag of its own: the project phase IS
        // the post-freeze payload, so the pill is marked off that.
        setPhase("er-projector-phase", label, project);
        document.getElementById("er-projector-live").hidden = project;
        document.getElementById("er-projector-matrix").hidden = !project;
        if (project) renderProjectorMatrix(data); else renderProjectorLive(data);
      } catch (error) {
        const el = document.getElementById("er-projector-error"); el.hidden = false; el.textContent = error.message || "현황을 불러오지 못했습니다.";
      }
    }
    await tick(); pollWhileVisible(tick, 30000);
  }

  addStyle();
  if (path === "/my-score") scorePage(); else projectorPage();
})();

/* Participant-facing contest clock. It lives in the shared navigation header,
   broadcasts phase changes so the challenges page can refresh instantly, and
   lends its poll to the landing page's hero clock as window.econRoundState. */
(function () {
  "use strict";


  let competition = null;
  let reachedTarget = false;
  const listeners = new Set();
  // Every refresh takes a number when it asks. Replies can arrive out of
  // order — at zero the 250ms tick asks again before the last answer is in —
  // and one written before the boundary must never land after a newer one
  // and flip the phase back.
  let issued = 0;
  let applied = 0;

  function install() {
    if (document.getElementById("econ-round-countdown")) return;
    const style = document.createElement("style");
    style.textContent = `
      .navbar{position:relative}
      .econ-round-countdown{position:absolute;left:50%;top:50%;z-index:1031;transform:translate(-50%,-50%);display:inline-flex;align-items:center;gap:10px;height:38px;padding:0 12px;border:1px solid var(--d-border,#e4e7ec);border-radius:6px;background:var(--d-surface,#ffffff);box-shadow:var(--d-shadow-1,0 1px 3px rgba(16,24,40,.06));color:var(--d-ink,#1a1d23);pointer-events:none;transition:background .22s var(--d-ease,ease),border-color .22s var(--d-ease,ease),color .22s var(--d-ease,ease)}
      /* The alarm states (.is-warn/.is-crit) are styled in THEME_HEADER_CSS, which
         reaches them through #econ-round-countdown and so outranks these rules —
         including this muted label colour, which an alarm wants to inherit. */
      .econ-round-countdown-label{font:500 12px var(--d-f-ko,system-ui);color:var(--d-text-2,#5c6370);white-space:nowrap}
      /* Tabular figures plus a width that already fits HH:MM:SS, so a ticking
         second never nudges the pill sideways under the navbar's centre. */
      .econ-round-countdown-time{font:600 18px var(--d-f-sans,system-ui);font-variant-numeric:tabular-nums;letter-spacing:.01em;line-height:1;min-width:8ch;text-align:right;white-space:nowrap}
      .econ-round-countdown[data-phase="finished"]{justify-content:center;border-color:var(--d-border,#e4e7ec);background:var(--d-surface-2,#f1f3f6);color:var(--d-text-2,#5c6370);box-shadow:none}
      /* Phone: the pill stays inside the bar and gives up its label, which is
         what B does. Dropping it below the navbar instead left it floating on
         the bare page ground with no clearance above the heading — it is
         absolutely positioned, so it reserves no space and every page under it
         would have to pad its own header out of the way. */
      @media(max-width:760px){.econ-round-countdown{gap:0;height:32px;padding:0 10px}.econ-round-countdown-label{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}.econ-round-countdown-time{font-size:15px;min-width:7ch}}
      /* Narrower than ~560px the brand ("SNU SENS E-CON 논설") runs past the
         centre line and the pill paints over its tail. Stop centring it there
         and tuck it in beside the toggler instead, which clears the brand
         without truncating either of them. 68px is the toggler plus the
         container's right padding. */
      @media(max-width:560px){.econ-round-countdown{left:auto;right:68px;transform:translateY(-50%)}}
    `;
    document.head.appendChild(style);
    const clock = document.createElement("div");
    clock.id = "econ-round-countdown";
    clock.className = "econ-round-countdown";
    clock.setAttribute("role", "status");
    const navbar = document.querySelector(".navbar");
    (navbar || document.body).appendChild(clock);
  }

  function format(seconds) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remainingSeconds = seconds % 60;
    return [hours, minutes, remainingSeconds].map((part) => String(part).padStart(2, "0")).join(":");
  }

  function timerSpec(data) {
    switch (data.phase) {
      case "before": return ["1\ub77c\uc6b4\ub4dc \uc2dc\uc791\uae4c\uc9c0 \ub0a8\uc740 \uc2dc\uac04", data.round1_starts_at];
      case "round1": return ["1\ub77c\uc6b4\ub4dc \ub0a8\uc740 \uc2dc\uac04", data.round1_ends_at];
      case "break": return ["2\ub77c\uc6b4\ub4dc \uc2dc\uc791\uae4c\uc9c0 \ub0a8\uc740 \uc2dc\uac04", data.round2_starts_at];
      case "round2": return ["2\ub77c\uc6b4\ub4dc \ub0a8\uc740 \uc2dc\uac04", data.round2_ends_at];
      case "finished": return ["\ub300\ud68c \uc885\ub8cc", null];
      default: return ["", null];
    }
  }

  /* The two thresholds a mentee has to feel — five minutes and one minute — are
     decided here and dressed in THEME_HEADER_CSS. They are kept mutually
     exclusive so the pill is only ever in one alarm state, whatever order the
     two rules happen to sit in. `seconds` is null when nothing is counting. */
  function setUrgency(clock, seconds) {
    clock.classList.toggle("is-warn", seconds !== null && seconds <= 300 && seconds > 60);
    clock.classList.toggle("is-crit", seconds !== null && seconds <= 60);
  }

  function render() {
    const clock = document.getElementById("econ-round-countdown");
    if (!clock || !competition) return;
    const [label, endAt] = timerSpec(competition);
    // Clear the alarm before hiding: a phase with nothing to count would
    // otherwise keep whichever .is-warn/.is-crit skin the last tick set.
    if (!label) { setUrgency(clock, null); clock.hidden = true; return; }
    clock.hidden = false;
    clock.dataset.phase = competition.phase || "";
    if (!endAt) { setUrgency(clock, null); clock.textContent = label; return; }
    const seconds = Math.max(0, Math.ceil((Date.parse(endAt) - Date.now()) / 1000));
    clock.innerHTML = '<span class="econ-round-countdown-label"></span><strong class="econ-round-countdown-time"></strong>';
    clock.querySelector(".econ-round-countdown-label").textContent = label;
    clock.querySelector(".econ-round-countdown-time").textContent = format(seconds);
    setUrgency(clock, seconds);
    if (seconds === 0) reachedTarget = true;
  }

  /* Subscribers hear every successful refresh, not only the phase changes the
     event below carries: the landing page's own countdown needs the fresh
     timestamps. Each call is fenced, so a broken subscriber can never stop the
     navbar clock. */
  function tell(listener) {
    try { listener(competition); } catch (_error) { /* the navbar clock carries on */ }
  }

  async function refresh() {
    const turn = ++issued;
    try {
      const response = await fetch("/api/v1/digital/competition", { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) return;
      const payload = await response.json();
      if (turn < applied) return; // older than what is on screen: it lost the race
      applied = turn;
      const next = payload.data || null;
      const changed = competition && next && competition.phase !== next.phase;
      competition = next;
      reachedTarget = false;
      render();
      if (changed) window.dispatchEvent(new CustomEvent("econ:competition-change", { detail: competition }));
      if (competition) listeners.forEach(tell);
    } catch (_error) {
      // Keep the last successful display during a transient failure.
    }
  }

  function start() {
    if (!document.querySelector(".navbar")) return;
    install();
    // Published only once this poll is really running, so landing.js can
    // share it rather than start a second one — and, where it is missing,
    // knows to fetch for itself instead of waiting on a clock nobody winds.
    window.econRoundState = {
      get: () => competition,
      subscribe(listener) {
        listeners.add(listener);
        if (competition) tell(listener);
        return () => { listeners.delete(listener); };
      },
      refresh,
    };
    refresh();
    // The 250ms tick keeps ticking while hidden so the clock is already right
    // when the viewer looks back; only the network refresh pauses.
    setInterval(() => { render(); if (reachedTarget) refresh(); }, 250);
    setInterval(() => { if (!document.hidden) refresh(); }, 15000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();