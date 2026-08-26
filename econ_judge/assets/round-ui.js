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
      .er-root{max-width:1120px;margin:0 auto;padding:42px 24px 56px;font-family:var(--d-f-ko);color:var(--d-ink)}
      .er-head{display:flex;justify-content:space-between;gap:24px;align-items:flex-start;border-bottom:1px solid var(--d-hair);padding-bottom:20px;margin-bottom:22px}.er-kicker,.er-label{font:600 11px var(--d-f-mono);letter-spacing:.13em;color:var(--d-ink-light);text-transform:uppercase}.er-h1{font:600 42px var(--d-f-sans);letter-spacing:-.035em;margin:7px 0}.er-sub{color:var(--d-ink-light);margin:0;line-height:1.6}.er-phase{padding:10px 14px;border:1px solid var(--d-brand-line);background:var(--d-brand-soft);border-radius:999px;white-space:nowrap;font:600 12px var(--d-f-mono);letter-spacing:.07em}.er-phase-frozen{border-color:var(--d-hair-strong);background:var(--d-paper-sunk);color:var(--d-ink-light)}.er-overview{display:grid;grid-template-columns:1.15fr 1fr 1fr;gap:12px;margin:20px 0}.er-panel{border:1px solid var(--d-hair);padding:18px;background:var(--d-paper)}.er-panel strong{display:block;font:650 38px var(--d-f-sans);letter-spacing:-.045em;margin-top:7px}.er-panel strong small{font:500 17px var(--d-f-mono);color:var(--d-ink-light)}.er-rounds{display:grid;grid-template-columns:1fr 1fr;gap:14px}.er-round{padding:22px;border:1px solid var(--d-hair-strong);background:var(--d-paper-soft)}.er-round-live{border-color:var(--d-brand);background:var(--d-brand-soft)}.er-round-title{display:flex;justify-content:space-between;gap:12px;align-items:baseline}.er-round h2{font:600 25px var(--d-f-sans);letter-spacing:-.025em;margin:0}.er-round-state{font:600 11px var(--d-f-mono);letter-spacing:.08em;color:var(--d-ink-light)}.er-round-score{font:650 52px var(--d-f-sans);letter-spacing:-.055em;margin:20px 0 8px}.er-round-score small{font:500 16px var(--d-f-mono);color:var(--d-ink-light)}.er-progress{height:7px;background:var(--d-paper-sunk);overflow:hidden}.er-progress i{display:block;height:100%;background:var(--d-brand);transition:width .35s}.er-round-foot{display:flex;justify-content:space-between;margin-top:10px;color:var(--d-ink-light);font-size:13px}.er-leader{margin-top:14px;padding:16px 18px;border:1px dashed var(--d-hair-strong);display:flex;justify-content:space-between;gap:12px}.er-note{margin-top:18px;color:var(--d-ink-light);font-size:13px}.er-error{padding:14px;border:1px solid var(--d-fail-line);background:var(--d-fail-soft);color:var(--d-fail)}
      .er-projector{max-width:none;min-height:calc(100vh - 56px);display:flex;flex-direction:column}.er-projector .er-head{max-width:none}.er-projector-main{flex:1;display:grid;place-content:center;text-align:center;gap:18px}.er-projector-score{font:700 clamp(110px,24vw,270px) var(--d-f-sans);letter-spacing:-.08em;line-height:.8}.er-projector-score small{font:500 30px var(--d-f-mono);color:var(--d-brand-dark)}.er-projector-stats{display:flex;justify-content:center;gap:32px;color:var(--d-ink-light);font-size:16px}.er-projector-stats b{color:var(--d-ink);font:650 30px var(--d-f-sans)}
      .er-root [hidden]{display:none}
      .er-matrix{flex:1;display:flex;flex-direction:column;justify-content:center;gap:22px;padding:8px 0 4px;overflow:auto}.er-matrix-table{width:100%;border-collapse:collapse;table-layout:fixed}.er-matrix-table th,.er-matrix-table td{border:1px solid var(--d-hair-strong);padding:12px 14px;text-align:center;position:relative}.er-matrix-table thead th{background:var(--d-paper-soft)}.er-matrix-table thead th b{display:block;font:700 clamp(22px,2.4vw,38px) var(--d-f-mono);letter-spacing:.02em;color:var(--d-brand-dark)}.er-matrix-table thead th span{display:block;margin-top:5px;font:500 clamp(13px,1.2vw,19px) var(--d-f-ko);color:var(--d-ink-light)}.er-matrix-table .er-matrix-corner{width:22%;text-align:left;font:600 12px var(--d-f-mono);letter-spacing:.13em;color:var(--d-ink-light);text-transform:uppercase}.er-matrix-table tbody th{text-align:left;font:650 clamp(19px,2.1vw,32px) var(--d-f-sans);letter-spacing:-.03em;color:var(--d-ink)}.er-cell-in{background:var(--d-brand-soft)}.er-dot{display:block;width:clamp(26px,2.7vw,46px);height:clamp(26px,2.7vw,46px);margin:0 auto;border-radius:50%;border:3px solid var(--d-hair-strong);box-sizing:border-box}.er-dot-in{background:var(--d-brand-dark);border-color:var(--d-brand-dark)}.er-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}.er-matrix-legend{display:flex;flex-wrap:wrap;align-items:center;gap:28px;color:var(--d-ink-light);font:500 clamp(14px,1.3vw,19px) var(--d-f-ko)}.er-matrix-legend span{display:flex;align-items:center;gap:10px}.er-matrix-legend .er-dot{width:22px;height:22px;margin:0}
      @media(max-width:720px){.er-root{padding:28px 16px}.er-head{flex-direction:column}.er-h1{font-size:34px}.er-overview,.er-rounds{grid-template-columns:1fr}.er-projector-score{font-size:120px}.er-projector-stats{gap:16px;font-size:13px}}
    `;
    document.head.appendChild(style);
  }

  function installScoreShell() {
    const old = document.getElementById("ms-root");
    if (!old) return null;
    old.outerHTML = `<main class="er-root" id="er-score-root"><header class="er-head"><div><div class="er-kicker">SNU SENS · E-CON 논설</div><h1 class="er-h1">내 점수</h1><p class="er-sub" id="er-score-message">점수를 불러오는 중입니다.</p></div><div class="er-phase" id="er-score-phase">—</div></header><section class="er-overview"><div class="er-panel"><span class="er-label">온라인 총점</span><strong id="er-total-score">— <small>/ 80 pt</small></strong></div><div class="er-panel"><span class="er-label">해결한 문제</span><strong id="er-total-solved">— <small>/ 15</small></strong></div><div class="er-panel"><span class="er-label">우리 조</span><strong id="er-team-name" style="font-size:24px">—</strong></div></section><section class="er-rounds" id="er-rounds"></section><section class="er-leader"><span>익명 선두 조</span><strong id="er-leader">—</strong></section><p class="er-note" id="er-note">${STANDING_NOTE}</p><div class="er-error" id="er-error" hidden></div></main>`;
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

/* Participant-facing contest clock. It lives in the shared navigation header
   and broadcasts phase changes so the challenges page can refresh instantly. */
(function () {
  "use strict";


  let competition = null;
  let reachedTarget = false;

  function install() {
    if (document.getElementById("econ-round-countdown")) return;
    const style = document.createElement("style");
    style.textContent = `
      .navbar{position:relative}
      .econ-round-countdown{position:absolute;left:50%;top:50%;z-index:1031;transform:translate(-50%,-50%);display:flex;align-items:center;gap:10px;min-width:250px;padding:6px 13px 7px;border:1px solid var(--d-brand-line,#e7b86b);border-radius:999px;background:var(--d-paper,#fbfaf6);box-shadow:0 3px 12px rgba(21,17,10,.08);color:var(--d-ink,#15110a);font-variant-numeric:tabular-nums;pointer-events:none}
      .econ-round-countdown[data-phase="round1"],.econ-round-countdown[data-phase="round2"]{border-color:var(--d-brand,#c98620);background:var(--d-brand-soft,#fff3dc)}
      .econ-round-countdown-label{flex:1;font:600 11px var(--d-f-ko,system-ui);letter-spacing:-.01em;white-space:nowrap}
      .econ-round-countdown-time{font:700 16px var(--d-f-mono,monospace);letter-spacing:.04em;white-space:nowrap}
      .econ-round-countdown[data-phase="finished"]{justify-content:center;min-width:140px;border-color:var(--d-hair-strong,#bcb5a7);background:var(--d-paper-soft,#f3f0e8)}
      @media(max-width:760px){.econ-round-countdown{top:calc(100% + 9px);min-width:0;padding:5px 10px;box-shadow:0 4px 14px rgba(21,17,10,.12)}.econ-round-countdown-label{font-size:10px}.econ-round-countdown-time{font-size:13px}}
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

  function render() {
    const clock = document.getElementById("econ-round-countdown");
    if (!clock || !competition) return;
    const [label, endAt] = timerSpec(competition);
    if (!label) { clock.hidden = true; return; }
    clock.hidden = false;
    clock.dataset.phase = competition.phase || "";
    if (!endAt) { clock.textContent = label; return; }
    const seconds = Math.max(0, Math.ceil((Date.parse(endAt) - Date.now()) / 1000));
    clock.innerHTML = '<span class="econ-round-countdown-label"></span><strong class="econ-round-countdown-time"></strong>';
    clock.querySelector(".econ-round-countdown-label").textContent = label;
    clock.querySelector(".econ-round-countdown-time").textContent = format(seconds);
    if (seconds === 0) reachedTarget = true;
  }

  async function refresh() {
    try {
      const response = await fetch("/api/v1/digital/competition", { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) return;
      const payload = await response.json();
      const next = payload.data || null;
      const changed = competition && next && competition.phase !== next.phase;
      competition = next;
      reachedTarget = false;
      render();
      if (changed) window.dispatchEvent(new CustomEvent("econ:competition-change", { detail: competition }));
    } catch (_error) {
      // Keep the last successful display during a transient failure.
    }
  }

  function start() {
    if (!document.querySelector(".navbar")) return;
    install();
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