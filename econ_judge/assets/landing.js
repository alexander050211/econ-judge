/* The front page's live status. INDEX_CONTENT serves its one .stage bare — no
   data-phase, no data-auth, no inline style — and that bare first paint is a
   designed state (연결 중, --:--, and 로그인 for a signed-out visitor; a
   signed-in one gets no action), so nothing on the page moves until this
   script does. It writes the stage's data-auth / data-phase / data-tail,
   the --el / --ticks / --major the ruler and the rail read, and the digits of
   #econ-landing-clock; the CSS picks every sentence and button from those.
   The only words it writes itself are the ones CSS cannot pick: the team's
   name, and a one-line note in #econ-landing-status when the phase changes,
   because the clock is a quiet role=timer and a screen reader would otherwise
   never hear a round open or close. It also gives the truth-table toy's
   stand-in 채점 button the keyboard manners a checkbox lacks.
   THEME_HEADER_CSS loads it on every page; without #econ-landing it does
   nothing beyond exposing its pure helpers for the tests. */
(function () {
  "use strict";

  const PHASES = ["before", "round1", "break", "round2", "finished", "open", "misconfigured"];
  // What each phase counts down to, and the span its progress is measured over.
  const TARGETS = {
    before: "round1_starts_at",
    round1: "round1_ends_at",
    break: "round2_starts_at",
    round2: "round2_ends_at",
  };
  const SPANS = {
    round1: ["round1_starts_at", "round1_ends_at"],
    break: ["round1_ends_at", "round2_starts_at"],
    round2: ["round2_starts_at", "round2_ends_at"],
  };
  // Said once when the page moves into a phase, and once when a round enters
  // its last five minutes — the two moments the chip changes under a reader.
  // A page can first read a round's phase already inside its tail (every
  // rehearsal round opens there), so TAIL must be true for any time left up
  // to five minutes; it is the tail lede's own sentence.
  const NEWS = {
    before: "1라운드 시작 전입니다.",
    round1: "1라운드가 시작되었습니다.",
    break: "휴식 시간입니다. 2라운드는 곧 시작됩니다.",
    round2: "2라운드가 시작되었습니다.",
    finished: "온라인 라운드가 끝났습니다.",
    open: "준비 모드입니다. 두 라운드가 모두 열려 있습니다.",
    misconfigured: "대회 일정 설정에 오류가 있습니다.",
  };
  const TAIL = {
    round1: "1라운드가 5분 안에 마감됩니다.",
    round2: "2라운드가 5분 안에 마감됩니다.",
  };

  const pad = (part) => String(part).padStart(2, "0");

  /* Everything the page shows, from one payload and one instant. It touches no
     DOM, so the whole state table can be checked in node. */
  function compute(payload, nowMs, isSignedIn) {
    const data = payload && typeof payload === "object" ? payload : {};
    const phase = PHASES.includes(data.phase) ? data.phase : null;
    const targetKey = TARGETS[phase];
    const targetMs = targetKey ? Date.parse(data[targetKey]) : NaN;
    // round-ui.js's formula to the letter, so this clock and the navbar pill
    // are never a second apart.
    const remaining = Number.isFinite(targetMs) ? Math.max(0, Math.ceil((targetMs - nowMs) / 1000)) : null;
    // Only a round ends in a deadline. A start is not one, so before and break
    // count down without ever turning amber or red.
    const round = phase === "round1" || phase === "round2";
    const deadline = round && remaining !== null;
    const view = {
      auth: isSignedIn ? "in" : "out",
      phase,
      remaining,
      target: remaining === null ? null : data[targetKey],
      tail: deadline && remaining <= 300,
      warn: deadline && remaining <= 300 && remaining > 60,
      crit: deadline && remaining <= 60,
      done: phase === "finished",
      long: false,
      hh: "", mm: "--", ss: ":--",
      el: phase === "finished" ? "100.0%" : "0.0%",
      ticks: null, major: null, elapsed: null, total: null,
    };
    if (view.done) {
      view.mm = "00";
      view.ss = ":00";
    } else if (remaining !== null) {
      // The hours group stays empty under an hour instead of showing a
      // constant 00:, and a start set days ahead grows it to three digits.
      const hours = Math.floor(remaining / 3600);
      view.hh = hours >= 1 ? `${hours}:` : "";
      view.mm = pad(Math.floor((remaining % 3600) / 60));
      view.ss = `:${pad(remaining % 60)}`;
      view.long = hours >= 100;
    }
    const span = SPANS[phase];
    const from = span ? Date.parse(data[span[0]]) : NaN;
    const to = span ? Date.parse(data[span[1]]) : NaN;
    if (to > from) {
      // Clamped, because this machine's clock and the server's disagree by a
      // little at every phase boundary.
      const elapsedMs = Math.min(to - from, Math.max(0, nowMs - from));
      view.el = `${((elapsedMs / (to - from)) * 100).toFixed(1)}%`;
      if (round) {
        // From the timestamps, never the 70 / 80 constants: rehearsal mode runs
        // two-minute rounds, and the ruler has to follow.
        const ticks = Math.max(1, Math.round((to - from) / 60000));
        view.ticks = ticks;
        view.major = Math.max(1, Math.round(ticks / 10));
        view.elapsed = `경과 ${Math.floor(elapsedMs / 60000)}분`;
        view.total = `${ticks}분`;
      }
    }
    return view;
  }

  /* The note for one render, given the phase and tail the last render with a
     phase showed. The first render is never news — whoever opened the page is
     already reading it. A render without a phase says nothing and forgets
     nothing, so one payload the page cannot read neither re-announces the
     phase it returns to nor swallows the one it moves on to. A tail that
     lifts (the round was extended) and comes back is news again. */
  function announcement(previous, view) {
    if (!previous || !view || !view.phase) return "";
    if (view.phase !== previous.phase) return view.tail ? `${NEWS[view.phase]} ${TAIL[view.phase]}` : NEWS[view.phase];
    return view.tail && !previous.tail ? TAIL[view.phase] : "";
  }

  /* The signed-in note reads "2조로 로그인되어 있습니다.", so the name takes
     로 after a vowel or ㄹ and 으로 after any other final consonant. A trailing
     digit is read in Korean (0, 3, 6 and the 십/백/천 ending 10, 100, 1000 take
     으로); anything else gets the neutral (으)로 rather than a guess. The
     trailing space is the only one between the name and the sentence: the
     page adds none after the slot (tests/landing_page_test.py pins that). */
  function userLabel(name) {
    const text = typeof name === "string" ? name.trim() : "";
    if (!text) return "";
    const last = text.charAt(text.length - 1);
    const code = text.charCodeAt(text.length - 1);
    let particle = "(으)로";
    if (code >= 0xac00 && code <= 0xd7a3) {
      const final = (code - 0xac00) % 28;
      particle = final === 0 || final === 8 ? "로" : "으로";
    } else if (last >= "0" && last <= "9") {
      particle = "036".includes(last) ? "으로" : "로";
    }
    return `${text}${particle} `;
  }

  /* The truth-table toy grades with CSS alone: its 채점 is a <label> for a
     visually hidden checkbox. That checkbox answers Space but not Enter, keeps
     one accessible name whatever the label now says, and a 처음부터
     (type=reset) sits inside the verdict it hides, taking keyboard focus down
     to <body> with it. These mends are all the toy asks of JavaScript;
     without them it still grades. */
  function mendToy() {
    const run = document.getElementById("econ-toy-run");
    const form = run && run.form;
    if (!form) return;
    const name = () => {
      const next = run.checked ? "다시 풀기" : "채점하기";
      if (run.getAttribute("aria-label") !== next) run.setAttribute("aria-label", next);
    };
    // A browser that restores form state on Back may hand the page a box
    // already checked.
    name();
    run.addEventListener("change", name);
    run.addEventListener("keydown", (event) => {
      // Held down, Enter repeats; mid-composition it belongs to the IME.
      if (event.key !== "Enter" || event.repeat || event.isComposing) return;
      // Firefox would otherwise take Enter as a request to submit the form.
      event.preventDefault();
      run.click();
    });
    // The toy sends nothing anywhere, whatever key a browser reads as submit.
    form.addEventListener("submit", (event) => { event.preventDefault(); });
    form.addEventListener("reset", () => {
      // `reset` fires before the controls are reset, so which answer row 0
      // holds, and the box's new name, are only known a frame later.
      const from = document.activeElement;
      const held = Boolean(from && form.contains(from));
      let keyboard = true;
      try { keyboard = held && from.matches(":focus-visible"); } catch (_error) { /* no :focus-visible: scroll, as for keys */ }
      const later = (fn) => (typeof window.requestAnimationFrame === "function" ? window.requestAnimationFrame(fn) : setTimeout(fn, 0));
      later(() => {
        name();
        if (!held) return;
        const first = ["econ-toy-q0a", "econ-toy-q0b"].map((id) => document.getElementById(id)).find((radio) => radio && radio.checked);
        // Focus goes back to the row the reset starts over at. A keyboard
        // user is scrolled to it; a pointer user's page stays where it is.
        if (first) first.focus({ preventScroll: !keyboard });
      });
    });
  }

  function start() {
    const root = document.getElementById("econ-landing");
    if (!root) return;
    const stage = root.querySelector(".stage");
    const clock = document.getElementById("econ-landing-clock");
    const status = document.getElementById("econ-landing-status");
    // INDEX_CONTENT serves one of each slot. Every match is written, so a
    // second copy added to the page later needs nothing here.
    const all = (selector) => Array.from(root.querySelectorAll(selector));
    const SLOTS = ["hh", "mm", "ss", "elapsed", "total"];
    const slot = {};
    SLOTS.forEach((name) => { slot[name] = all(`[data-econ-clock="${name}"]`); });
    const init = window.init || {};
    const signedIn = Number(init.userId) > 0;
    let current = null;
    let seen = null;
    // The poll this script runs only when round-ui.js's is missing. issued /
    // applied order its replies, as round-ui.js's own do; spentOn, backoff
    // and lastAsked pace its asks at zero.
    let ownPoll = false;
    let issued = 0;
    let applied = 0;
    let spentOn = null;
    let backoff = 0;
    let lastAsked = 0;

    /* Each writer compares with the live DOM first, so a tick that changes
       nothing touches nothing: no style recalculation four times a second, and
       no chatter for a screen reader sitting on the clock. */
    function attr(name, value) {
      if (!stage) return;
      if (value === null) {
        if (stage.hasAttribute(name)) stage.removeAttribute(name);
      } else if (stage.getAttribute(name) !== value) {
        stage.setAttribute(name, value);
      }
    }
    function prop(name, value) {
      if (!stage) return;
      const next = value === null ? "" : String(value);
      if (stage.style.getPropertyValue(name) === next) return;
      if (next) stage.style.setProperty(name, next);
      else stage.style.removeProperty(name);
    }
    function text(elements, value) {
      if (value === null) return;
      elements.forEach((element) => {
        if (element.textContent !== value) element.textContent = value;
      });
    }
    function flag(name, on) {
      if (clock && clock.classList.contains(name) !== on) clock.classList.toggle(name, on);
    }

    function render(view) {
      attr("data-auth", view.auth);
      attr("data-phase", view.phase);
      attr("data-tail", view.tail ? "1" : null);
      prop("--el", view.el);
      prop("--ticks", view.ticks);
      prop("--major", view.major);
      SLOTS.forEach((name) => text(slot[name], view[name]));
      flag("is-warn", view.warn);
      flag("is-crit", view.crit);
      flag("done", view.done);
      flag("is-long", view.long);
      if (!view.phase) return;
      const news = announcement(seen, view);
      seen = { phase: view.phase, tail: view.tail };
      if (news && status) status.textContent = news;
    }

    function tick() {
      if (!current) return;
      const now = Date.now();
      const view = compute(current, now, signedIn);
      render(view);
      // At zero the server is about to change phase. round-ui.js's tick asks
      // on every 250 ms while its pill reads zero, so with its poll there is
      // nothing to add: a second request in the same tick could only race
      // its own. Polling alone, ask once for this target, and since a clock a
      // little ahead of the server's gets the old phase back and would sit on
      // 00:00 until the next 15 s poll, again 1, 2, 4, 8 s later, then never
      // further apart than that poll.
      const key = view.target && `${view.phase} ${view.target}`;
      if (!ownPoll || view.remaining !== 0 || !key) return;
      if (key !== spentOn) {
        spentOn = key;
        backoff = 1000;
        fetchCompetition();
      } else if (!document.hidden && now - lastAsked >= backoff) {
        backoff = Math.min(backoff * 2, 15000);
        fetchCompetition();
      }
    }

    // Anything but a payload object leaves the last good display standing.
    function accept(payload) {
      if (!payload || typeof payload !== "object") return;
      current = payload;
      tick();
    }

    // round-ui.js's rule for the same race: each request takes a number, and
    // an answer older than the one on screen is dropped.
    async function fetchCompetition() {
      const turn = ++issued;
      lastAsked = Date.now();
      try {
        const response = await fetch("/api/v1/digital/competition", { credentials: "same-origin", cache: "no-store" });
        if (!response.ok) return;
        const body = await response.json();
        if (turn < applied) return;
        applied = turn;
        accept(body.data);
      } catch (_error) {
        // Keep the last good display; if nothing ever arrives, 연결 중 stays.
      }
    }

    // Auth is written first, before any payload; the phase waits for the
    // server. The first paint can still come before this line runs, and
    // INDEX_CONTENT's navbar rule keeps 로그인 off it for a signed-in team.
    attr("data-auth", signedIn ? "in" : "out");
    const label = signedIn ? userLabel(init.userName) : "";
    if (label) text(all("[data-econ-user]"), label);
    mendToy();

    const shared = window.econRoundState;
    if (shared && typeof shared.subscribe === "function") {
      // round-ui.js already polls this endpoint for the navbar pill; a second
      // poll from the same page would only double the traffic.
      shared.subscribe(accept);
    } else {
      // No shared poll (round-ui.js failed to load, or found no navbar): run
      // one on round-ui.js's own cadence.
      ownPoll = true;
      fetchCompetition();
      setInterval(() => { if (!document.hidden) fetchCompetition(); }, 15000);
      document.addEventListener("visibilitychange", () => { if (!document.hidden) fetchCompetition(); });
    }
    // Every 250 ms, as the navbar pill does. Ticking on while the tab is hidden
    // means the clock is already right when the viewer looks back.
    setInterval(tick, 250);
  }

  window.econLanding = { compute, announcement, userLabel };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
