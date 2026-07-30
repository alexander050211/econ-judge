// Disable CTFd's default text-flag path when this script is loaded inside the
// legacy challenge modal. The standalone problem page has no challenge runtime.
var econHasChallengeRuntime =
  window.CTFd &&
  CTFd._internal &&
  CTFd._internal.challenge &&
  CTFd.pages &&
  CTFd.pages.challenge;

if (econHasChallengeRuntime) {
  CTFd._internal.challenge.data = undefined;
  CTFd._internal.challenge.renderer = null;
  CTFd._internal.challenge.preRender = function () {};
  CTFd._internal.challenge.render = null;
  CTFd._internal.challenge.postRender = function () {};
}

(function () {
  "use strict";

  const MAX_BYTES = 256 * 1024;
  // Keep the concept replay and post-grade checklist available, but dormant
  // until the camp workflow is ready to use detailed submission feedback.
  const DETAILED_GRADING_FEEDBACK = false;

  const $ = (sel, root) => (root || document).querySelector(sel);

  function root() {
    return document.getElementById("econ-submit-root");
  }

  function csrfNonce() {
    return (
      (window.init && window.init.csrfNonce) ||
      (window.CTFd && CTFd.config && CTFd.config.csrfNonce) ||
      ""
    );
  }

  function setStandaloneSubmitDisabled(disabled) {
    const r = root();
    if (!r || r.dataset.mode !== "page") return;
    const submit = document.getElementById("challenge-submit");
    if (submit) submit.disabled = disabled;
  }

  function setState(name) {
    const r = root();
    if (!r) return;
    r.querySelectorAll("[data-state]").forEach((el) => {
      const match = el.dataset.state === name;
      el.hidden = !match;
    });
    // CTFd's stock submit button lives outside our root in the parent
    // template. Hide it once we're grading or showing the result panel
    // (our "다시 제출하기" button handles the result-state action). Restore
    // it when we go back to empty / ready so the next submission can fire.
    const ctfdSubmit = document.getElementById("challenge-submit");
    if (ctfdSubmit) {
      const ctfdCol = ctfdSubmit.closest(".key-submit") || ctfdSubmit;
      const hide = name === "grading" || name === "result";
      ctfdCol.style.display = hide ? "none" : "";
    }
  }

  function humanSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1024 / 1024).toFixed(2) + " MB";
  }

  // ---------- Testbench (grading-state instrument log) ----------
  //
  // There is no streaming from the grader — the submit POST blocks and returns
  // one JSON result. The bench is therefore an HONEST post-result replay: while
  // the POST is in flight we show an indeterminate bar; once the result lands we
  // reveal the challenge's concept manifest one line at a time, paced to the
  // REAL grading duration (a fast 200ms grade just flashes complete). The lines
  // come from the server's `concepts` array (educational aspects of the
  // challenge), never per-case pass/fail data.

  const BENCH_PREF_KEY = "econ_bench_detail"; // "1" = 자세히, "0" = 간단히
  const prefersReducedMotion =
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function benchDetailOn() {
    if (!DETAILED_GRADING_FEEDBACK) return false;
    try {
      return localStorage.getItem(BENCH_PREF_KEY) !== "0"; // default 자세히
    } catch (e) {
      return true;
    }
  }
  function setBenchDetail(on) {
    try {
      localStorage.setItem(BENCH_PREF_KEY, on ? "1" : "0");
    } catch (e) {}
  }

  // Bench runtime state, so a fast result can cancel an in-flight replay.
  const bench = { timers: [], cancelled: false };

  function benchClearTimers() {
    bench.timers.forEach((t) => clearTimeout(t));
    bench.timers = [];
  }

  function applyBenchMode() {
    const on = benchDetailOn();
    const simple = $("#econ-grading-simple");
    const detail = $("#econ-grading-bench");
    const toggle = $("#econ-bench-toggle");
    if (simple) simple.hidden = on;
    if (detail) detail.hidden = !on;
    if (toggle) {
      toggle.hidden = !DETAILED_GRADING_FEEDBACK;
      const toolbar = toggle.closest(".grading-toolbar");
      if (toolbar) toolbar.hidden = !DETAILED_GRADING_FEEDBACK;
      toggle.textContent = on ? "간단히 보기" : "자세히 보기";
      toggle.setAttribute("aria-pressed", on ? "true" : "false");
    }
  }

  // Enter the grading state: reset the bench to an indeterminate "running" look.
  function benchStart() {
    benchClearTimers();
    bench.cancelled = false;
    applyBenchMode();
    const bar = $("#econ-bench-bar");
    const term = $("#econ-bench-term");
    if (bar) {
      bar.className = "s3-bench-bar-i is-working is-indeterminate";
      bar.style.width = "";
    }
    if (term) {
      term.innerHTML =
        '<div class="s3-bench-line">' +
        '<span class="s3-bench-prompt">$</span>' +
        '<span class="s3-bench-label s3-bench-run">회로 컴파일 · 시뮬레이션 준비 중</span>' +
        '<span class="s3-bench-cursor" aria-hidden="true"></span>' +
        "</div>";
    }
  }

  // Replay the concept manifest after the result resolves, then hand off to
  // `done()` (which renders the result panel). `elapsedMs` is the real grading
  // time so the animation never outruns or lags reality by much.
  function benchReplay(data, elapsedMs, done) {
    const term = $("#econ-bench-term");
    const bar = $("#econ-bench-bar");
    const allPassed =
      (data && data.status) === "correct" ||
      (data && data.total > 0 && data.passed === data.total);
    const concepts =
      (data && Array.isArray(data.concepts) && data.concepts.length
        ? data.concepts
        : ["회로 동작"]);

    // If the bench isn't the active view (간단히) or motion is reduced, skip the
    // animation entirely and go straight to the result.
    if (!benchDetailOn() || prefersReducedMotion || !term || !bar) {
      done();
      return;
    }

    bar.className = "s3-bench-bar-i is-working";
    // Pace lines to the real duration: total replay ~= min(elapsed, cap),
    // floored so it's perceptible, split across the concept lines.
    const budget = Math.max(360, Math.min(elapsedMs || 0, 1400));
    const step = Math.max(120, Math.round(budget / (concepts.length + 1)));

    term.innerHTML = "";
    let i = 0;

    const addLine = () => {
      // A stale replay (superseded by a newer submit) still completes the
      // handoff so the awaiting promise can never hang the modal.
      if (bench.cancelled) { done(); return; }
      if (i < concepts.length) {
        const label = concepts[i];
        const verdict = allPassed
          ? '<span class="s3-bench-ok">✓ 통과</span>'
          : '<span class="s3-bench-run">검증</span>';
        const line = document.createElement("div");
        line.className = "s3-bench-line";
        line.innerHTML =
          '<span class="s3-bench-prompt">$</span>' +
          '<span class="s3-bench-label"></span>' +
          verdict;
        // label via textContent — never trust server strings as HTML
        line.querySelector(".s3-bench-label").textContent = label;
        term.appendChild(line);
        bar.style.width = Math.round(((i + 1) / (concepts.length + 1)) * 100) + "%";
        i += 1;
        bench.timers.push(setTimeout(addLine, step));
      } else {
        // Summary line. Tier the tone to match the result panel: a partial
        // pass (0 < passed < total) reads as amber/encouraging (not a hard
        // red ✗), so the bench and the result card tell the same story. Red
        // ✗ is reserved for a genuine 0/N. Counts are Number()-coerced so a
        // non-numeric value can never reach innerHTML.
        const p = Number(data.passed) || 0;
        const t = Number(data.total) || 0;
        const sum = document.createElement("div");
        sum.className = "s3-bench-line";
        let verdict;
        if (allPassed) {
          verdict = '<span class="s3-bench-ok">✓ 전체 통과</span>';
        } else if (p > 0) {
          verdict = '<span class="s3-bench-warn">' + p + " / " + t + " 통과</span>";
        } else {
          verdict = '<span class="s3-bench-fail">✗ ' + p + " / " + t + " 통과</span>";
        }
        sum.innerHTML =
          '<span class="s3-bench-prompt">$</span>' +
          '<span class="s3-bench-label s3-bench-sum">testbench</span>' +
          verdict;
        term.appendChild(sum);
        bar.style.width = "100%";
        // Brief beat on the completed bench, then the result panel.
        bench.timers.push(setTimeout(done, allPassed ? 420 : 620));
      }
    };
    addLine();
  }

  function showFileError(msg) {
    const el = $("#econ-file-error");
    if (!el) return;
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(el._t);
    el._t = setTimeout(() => {
      el.hidden = true;
    }, 4500);
  }

  function clearFileError() {
    const el = $("#econ-file-error");
    if (el) el.hidden = true;
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = String(str == null ? "" : str);
    return div.innerHTML;
  }

  function selectFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    if (files.length > 32) {
      showFileError("starters 폴더의 .dig 파일은 최대 32개까지 제출할 수 있습니다.");
      return;
    }

    let totalSize = 0;
    for (const file of files) {
      if (!file.name.toLowerCase().endsWith(".dig")) {
        showFileError("starters 폴더에는 .dig 파일만 포함해야 합니다.");
        return;
      }
      if (file.size === 0) {
        showFileError(file.name + " 파일이 비어 있습니다.");
        return;
      }
      if (file.size > MAX_BYTES) {
        showFileError(file.name + " 파일이 너무 큽니다.");
        return;
      }
      totalSize += file.size;
    }
    if (totalSize > 1024 * 1024) {
      showFileError("제출 폴더의 전체 파일 용량은 1 MB 이하여야 합니다.");
      return;
    }

    const expected = (root() && root().dataset.answerFilename) || "";
    const answer = expected
      ? files.find((file) => file.name === expected)
      : files[0];
    if (!answer) {
      showFileError("선택한 폴더에 이 문제의 답안 파일(" + expected + ")이 없습니다.");
      return;
    }

    const input = $("#challenge-file");
    if (input) {
      const dt = new DataTransfer();
      files.forEach((file) => dt.items.add(file));
      input.files = dt.files;
    }
    clearFileError();
    $("#econ-file-name").textContent = answer.name;
    $("#econ-file-size").textContent =
      humanSize(totalSize) + " · " + files.length + "개 .dig 파일";
    setState("ready");
    setStandaloneSubmitDisabled(false);
  }

  function clearSelection() {
    const input = $("#challenge-file");
    if (input) input.value = "";
    clearFileError();
    setState("empty");
    setStandaloneSubmitDisabled(true);
  }

  // ---------- Result parsing ----------

  function parseResult(message) {
    const m = String(message || "");
    const lines = m.split("\n");
    const head = (lines[0] || "").trim();
    const detail = lines.slice(1).join("\n").trim();

    // Patterns from the grader / endpoint:
    //   "All N testcases passed."
    //   "K/N testcases passed."
    let passed = null;
    let total = null;
    let mm = head.match(/^All\s+(\d+)\s+testcases\s+passed/i);
    if (mm) {
      passed = parseInt(mm[1], 10);
      total = passed;
    } else {
      mm = head.match(/^(\d+)\s*\/\s*(\d+)/);
      if (mm) {
        passed = parseInt(mm[1], 10);
        total = parseInt(mm[2], 10);
      }
    }
    return { head, detail, passed, total };
  }

  const ICON_PASS = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
  const ICON_WARN = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/></svg>';
  const ICON_FAIL = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';

  function renderResult(data) {
    const card = $("#econ-result");
    if (!card) return;
    const status = (data && data.status) || "incorrect";
    const parsed = parseResult(data && data.message);
    const head = parsed.head;
    // Prefer the structured count when the endpoint provides it; fall back to
    // the parsed message (older payloads / error states with no counts).
    const passed = (data && typeof data.passed === "number") ? data.passed : parsed.passed;
    const total = (data && typeof data.total === "number") ? data.total : parsed.total;
    // Directional checklist replaces the old raw grader detail. Shown only on a
    // non-passing graded result; one hint per concept, never a case index or
    // input vector. Accepts the new `hints` array, falling back to a legacy
    // single `hint` string for forward/backward compatibility.
    const hints = DETAILED_GRADING_FEEDBACK
      ? data && Array.isArray(data.hints)
        ? data.hints.filter((h) => typeof h === "string" && h)
        : data && typeof data.hint === "string" && data.hint
        ? [data.hint]
        : []
      : [];

    let klass, icon, titleHtml, subtitleText, showBar = false, pct = 0;

    if (status === "correct" && passed != null && total != null) {
      klass = "is-pass";
      icon = ICON_PASS;
      titleHtml =
        '<span class="count">' + passed + " / " + total + "</span> 테스트케이스 통과";
      subtitleText = "완벽한 회로입니다.";
      showBar = true;
      pct = 100;
    } else if (status === "correct") {
      klass = "is-pass";
      icon = ICON_PASS;
      titleHtml = "정답";
      subtitleText = "완벽한 회로입니다.";
    } else if (passed != null && total != null && total > 0 && passed > 0) {
      klass = "is-partial";
      icon = ICON_WARN;
      titleHtml =
        '<span class="count">' + passed + " / " + total + "</span> 테스트케이스 통과";
      subtitleText = "조금만 더 다듬어보세요.";
      showBar = true;
      pct = (passed / total) * 100;
    } else if (total === 0 || (passed === 0 && total === 0)) {
      klass = "is-fail";
      icon = ICON_FAIL;
      titleHtml = "채점 오류";
      subtitleText = head || "제출 파일을 확인해주세요.";
    } else if (passed === 0 && total != null && total > 0) {
      klass = "is-fail";
      icon = ICON_FAIL;
      titleHtml =
        '<span class="count">0 / ' + total + "</span> 테스트케이스 통과";
      subtitleText = "회로 동작을 다시 점검해보세요.";
      showBar = true;
      pct = 0;
    } else {
      klass = "is-fail";
      icon = ICON_FAIL;
      titleHtml = "채점 실패";
      subtitleText = head || "";
    }

    card.className = "result " + klass;
    card.innerHTML =
      '<div class="head">' +
        '<div class="marker">' + icon + "</div>" +
        '<div class="text">' +
          '<div class="title">' + titleHtml + "</div>" +
          (subtitleText
            ? '<div class="subtitle">' + escapeHtml(subtitleText) + "</div>"
            : "") +
        "</div>" +
      "</div>" +
      (showBar
        ? '<div class="bar"><i style="width: 0%"></i></div>'
        : "") +
      (hints.length
        ? '<div class="hints">' +
            '<div class="hints-title">확인해 볼 점</div>' +
            '<ul class="hints-list"></ul>' +
          "</div>"
        : "");

    // hint items set via textContent (never trust server strings as HTML)
    if (hints.length) {
      const ul = card.querySelector(".hints-list");
      if (ul) {
        hints.forEach((h) => {
          const li = document.createElement("li");
          li.innerHTML = '<span class="hint-mark" aria-hidden="true">↳</span><span class="hint-text"></span>';
          li.querySelector(".hint-text").textContent = h;
          ul.appendChild(li);
        });
      }
    }

    setState("result");

    if (status === "correct") {
      const pageStatus = document.getElementById("econ-problem-status");
      if (pageStatus) {
        pageStatus.classList.add("is-solved");
        pageStatus.innerHTML =
          '<i class="fa-solid fa-check" aria-hidden="true"></i><span>완료</span>';
      }
    }

    if (showBar) {
      requestAnimationFrame(() => {
        const bar = card.querySelector(".bar > i");
        if (bar) bar.style.width = pct.toFixed(2) + "%";
      });
    }
  }

  // ---------- Wiring ----------

  function bindUI() {
    const r = root();
    if (!r || r.dataset.bound === "1") return;

    const dz = $("#econ-dropzone", r);
    const input = $("#challenge-file", r);
    const clearBtn = $("#econ-file-clear", r);
    const resubmitBtn = $("#econ-resubmit", r);

    if (!dz || !input) return;

    input.addEventListener("change", (e) => {
      selectFiles(e.target.files);
    });

    ["dragenter", "dragover"].forEach((ev) => {
      dz.addEventListener(ev, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dz.classList.add("is-active");
      });
    });
    ["dragleave", "dragend", "drop"].forEach((ev) => {
      dz.addEventListener(ev, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dz.classList.remove("is-active");
      });
    });
    dz.addEventListener("drop", (e) => {
      const files = e.dataTransfer && e.dataTransfer.files;
      selectFiles(files);
    });

    if (clearBtn) clearBtn.addEventListener("click", clearSelection);
    if (resubmitBtn) resubmitBtn.addEventListener("click", clearSelection);

    if (r.dataset.mode === "page") {
      const submitBtn = document.getElementById("challenge-submit");
      if (submitBtn && submitBtn.dataset.bound !== "1") {
        submitBtn.addEventListener("click", async () => {
          const challengeInput = document.getElementById("challenge-id");
          const challengeId = Number(challengeInput && challengeInput.value);
          const selected = input.files && input.files[0];
          if (!selected) {
            showFileError(".dig 파일을 먼저 선택해주세요.");
            return;
          }
          if (!Number.isInteger(challengeId) || challengeId < 1) {
            showFileError("문제 정보를 확인할 수 없습니다. 페이지를 새로고침해주세요.");
            return;
          }
          setStandaloneSubmitDisabled(true);
          await submitChallenge(challengeId);
        });
        submitBtn.dataset.bound = "1";
      }
      setStandaloneSubmitDisabled(!(input.files && input.files.length));
    }

    const benchToggle = $("#econ-bench-toggle", r);
    if (benchToggle) {
      benchToggle.addEventListener("click", () => {
        setBenchDetail(!benchDetailOn());
        applyBenchMode();
      });
    }
    applyBenchMode();

    r.dataset.bound = "1";
  }

  // CTFd's challenge modal lazily injects view.html when the challenge card
  // is opened, so we have to bind whenever the root appears. MutationObserver
  // covers both initial render and modal reopens.
  function watch() {
    bindUI();
    const obs = new MutationObserver(() => bindUI());
    obs.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", watch);
  } else {
    watch();
  }

  // ---------- Submit (called by the modal or standalone page button) ----------

  async function submitChallenge(challenge_id, _submission) {
    const input = document.getElementById("challenge-file");
    if (!input || !input.files || !input.files.length) {
      showFileError(".dig 파일을 먼저 선택해주세요.");
      return {
        data: {
          status: "incorrect",
          message: ".dig 파일을 먼저 선택해주세요.",
        },
      };
    }

    setState("grading");
    benchStart();
    const t0 = (window.performance && performance.now) ? performance.now() : Date.now();

    const fd = new FormData();
    Array.from(input.files).forEach((file) => {
      const relativeName = file.webkitRelativePath || file.name;
      fd.append("files", file, relativeName);
    });
    const nonce = csrfNonce();
    if (nonce) fd.append("nonce", nonce);

    let result;
    try {
      const r = await fetch(
        "/api/v1/digital/challenges/" + challenge_id + "/attempt",
        {
          method: "POST",
          body: fd,
          credentials: "same-origin",
          headers: nonce ? { "CSRF-Token": nonce } : {},
        }
      );
      if (!r.ok) {
        throw new Error("HTTP " + r.status);
      }
      result = await r.json();
    } catch (e) {
      result = {
        data: {
          status: "incorrect",
          message:
            "네트워크 오류로 채점 결과를 받지 못했습니다.\n" +
            (e && e.message ? e.message : ""),
        },
      };
    }

    const t1 = (window.performance && performance.now) ? performance.now() : Date.now();
    const data = result.data || {};

    // Replay the concept manifest paced to the real grading time, THEN render
    // the result panel. benchReplay short-circuits to done() immediately when
    // 간단히 mode or reduced-motion is active.
    await new Promise((resolve) => {
      benchReplay(data, t1 - t0, () => {
        renderResult(data);
        resolve();
      });
    });

    return result;
  }

  if (econHasChallengeRuntime) {
    CTFd.pages.challenge.submitChallenge = submitChallenge;
  }
})();
