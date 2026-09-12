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
  // How much the grader tells a mentee is a camp-workflow decision rather than
  // a styling one, so it stays off until the organiser makes it. While this is
  // false the grading state a mentee sees is the spinner and one line: the
  // testbench console below (#econ-grading-bench, and the .s3-bench-* rules in
  // problem.css that dress it as Direction B's inset terminal), the concept
  // replay and the post-grade checklist are all built and styled but never
  // rendered. Flipping this to true is the only thing they are waiting on.
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

    // A submission the endpoint refused (round closed, attempt already used)
    // was never graded, so there is no bench run to replay.
    const rejected =
      (data && data.status) === "unavailable" || (data && data.status) === "locked";

    // If the bench isn't the active view (간단히) or motion is reduced, skip the
    // animation entirely and go straight to the result.
    if (rejected || !benchDetailOn() || prefersReducedMotion || !term || !bar) {
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

  // `paths` holds the folder-relative path of each file, parallel to `fileList`.
  // The directory picker puts that on the File itself; a folder drop cannot, so
  // the drop path supplies it separately.
  function selectFiles(fileList, paths) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    const submitRoot = root();
    const expected = (submitRoot && submitRoot.dataset.answerFilename) || "";
    const folderLabel = "submission";
    if (files.length > 32) {
      showFileError(folderLabel + " \ud3f4\ub354\uc758 .dig \ud30c\uc77c\uc740 \ucd5c\ub300 32\uac1c\uae4c\uc9c0 \uc81c\ucd9c\ud560 \uc218 \uc788\uc2b5\ub2c8\ub2e4.");
      return;
    }

    let totalSize = 0;
    for (const file of files) {
      if (!file.name.toLowerCase().endsWith(".dig")) {
        showFileError(folderLabel + " \ud3f4\ub354\uc5d0\ub294 .dig \ud30c\uc77c\ub9cc \ud3ec\ud568\ud574\uc57c \ud569\ub2c8\ub2e4.");
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

    const isSingleFolder = files.every((file, index) => {
      const relative = (paths && paths[index]) || file.webkitRelativePath || "";
      const parts = String(relative).replace(/\\/g, "/").split("/");
      return parts.length === 2 && parts[0];
    });
    if (!isSingleFolder) {
      showFileError("폴더 하나만 선택해주세요. 하위 폴더는 제출할 수 없습니다.");
      return;
    }

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

  // ---------- Folder drop ----------
  //
  // A dropped File carries an empty webkitRelativePath, so the folder shape is
  // only visible through DataTransferItem.webkitGetAsEntry. The entry list must
  // be read synchronously — the drop event's items are cleared as soon as the
  // handler yields — and what comes back is validated against exactly the
  // limits the directory picker path enforces.

  function readDirectory(entry, limit) {
    const reader = entry.createReader();
    const all = [];
    return new Promise((resolve, reject) => {
      // readEntries returns the directory in batches and signals the end with
      // an empty batch; one call is not enough.
      const step = () => {
        reader.readEntries((batch) => {
          all.push.apply(all, batch);
          // Stop one entry past the limit so a mis-dropped folder (Downloads,
          // Desktop) fails on the count check instead of stalling the tab on
          // thousands of entries. A folder that could still pass is read to
          // the end, so subfolder detection never sees a truncated list.
          if (all.length > limit) {
            resolve(all.slice(0, limit + 1));
            return;
          }
          if (!batch.length) {
            resolve(all);
            return;
          }
          step();
        }, reject);
      };
      step();
    });
  }

  function fileFromEntry(entry) {
    return new Promise((resolve, reject) => entry.file(resolve, reject));
  }

  async function selectDroppedFolder(dataTransfer) {
    const items = (dataTransfer && dataTransfer.items) || [];
    const roots = [];
    for (const item of Array.from(items)) {
      if (typeof item.webkitGetAsEntry !== "function") break;
      const entry = item.webkitGetAsEntry();
      if (entry) roots.push(entry);
    }
    if (!roots.length) {
      showFileError("폴더를 인식하지 못했습니다. 영역을 클릭해 제출 폴더를 선택해주세요.");
      return;
    }
    if (roots.length > 1 || !roots[0].isDirectory) {
      showFileError("제출 폴더 하나만 끌어다 놓아주세요.");
      return;
    }

    let files, paths;
    try {
      // 32 is the per-folder file limit selectFiles enforces below.
      const entries = await readDirectory(roots[0], 32);
      if (!entries.length) {
        showFileError("선택한 폴더가 비어 있습니다.");
        return;
      }
      if (!entries.every((entry) => entry.isFile)) {
        showFileError("폴더 하나만 선택해주세요. 하위 폴더는 제출할 수 없습니다.");
        return;
      }
      files = await Promise.all(entries.map(fileFromEntry));
      paths = entries.map((entry) => roots[0].name + "/" + entry.name);
    } catch (error) {
      showFileError("폴더를 읽지 못했습니다. 영역을 클릭해 제출 폴더를 선택해주세요.");
      return;
    }
    selectFiles(files, paths);
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
  // The warning triangle marks 채점 오류 — the judge could not run. A partial
  // pass is a graded answer, so it gets its own quieter mark instead: the two
  // states must never look like the same thing.
  const ICON_ERROR = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/></svg>';
  const ICON_PARTIAL = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v9"/><path d="M12 18.5h.01"/></svg>';
  const ICON_FAIL = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
  const ICON_CLOSED = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></svg>';
  const ICON_LOCK = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="10" width="16" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>';
  const ICON_ARROW = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14"/><path d="M13 6l6 6-6 6"/></svg>';

  // The Korean word beside the disc. Every verdict carries icon + word +
  // number, so a mentee never has to read the state out of the colour alone.
  const STATE_WORDS = {
    "is-pass": "전체 통과",
    "is-partial": "부분 통과",
    "is-fail": "실패",
    "is-error": "채점 오류",
    "is-notice": "안내",
  };

  // The state chip and the title/subtitle column are identical across all five
  // verdicts — only the words and the disc change — so the skeleton is built
  // once here. `subtitleText` is server text and is escaped; everything else is
  // a literal from this file.
  function headHtml(klass, icon, titleHtml, subtitleText) {
    return (
      '<div class="head">' +
        '<div class="marker">' + icon + "</div>" +
        '<div class="text">' +
          '<span class="state-chip">' + STATE_WORDS[klass] + "</span>" +
          '<div class="title">' + titleHtml + "</div>" +
          (subtitleText
            ? '<div class="subtitle">' + escapeHtml(subtitleText) + "</div>"
            : "") +
        "</div>" +
      "</div>"
    );
  }

  // After a full pass the mentee's next move is the next problem, so that link
  // is the card's primary action. The page shell carries the URL; an absent or
  // empty data-next-url means this was the last problem of the round, and the
  // problem list is the honest destination instead.
  function nextProblemLink() {
    const page = document.querySelector(".ep-page");
    const url = (page && page.dataset.nextUrl) || "";
    const link = document.createElement("a");
    link.id = "econ-next-problem";
    link.href = url || "/challenges";
    link.innerHTML = url ? "다음 문제" + ICON_ARROW : "전체 문제";
    return link;
  }

  // The template ships 다시 제출하기 as a sibling of the card; the verdict
  // design puts it in an action row inside it, beside the next-problem link.
  // The real node is moved (never copied) so the click binding from bindUI
  // survives, and it has to be re-appended on every render because setting
  // card.innerHTML detaches whatever was there before.
  function renderActions(card, resubmitBtn, isPass) {
    const actions = document.createElement("div");
    actions.className = "result-actions";
    if (isPass) actions.appendChild(nextProblemLink());
    if (resubmitBtn) actions.appendChild(resubmitBtn);
    card.appendChild(actions);
  }

  function renderResult(data) {
    const card = $("#econ-result");
    if (!card) return;
    // Held before any innerHTML write, which detaches the button from the card
    // it was moved into by the previous render.
    const resubmitBtn = document.getElementById("econ-resubmit");
    const status = (data && data.status) || "incorrect";
    const parsed = parseResult(data && data.message);
    const head = parsed.head;

    // The round is closed, or the single truth-table attempt is already spent.
    // Nothing was graded: show the server's explanation on a calm card and stop
    // before any of the pass/partial/fail tiering below.
    if (status === "unavailable" || status === "locked") {
      const locked = status === "locked";
      card.className = "result is-notice";
      card.innerHTML = headHtml(
        "is-notice",
        locked ? ICON_LOCK : ICON_CLOSED,
        locked ? "이미 제출한 문제입니다" : "지금은 제출할 수 없습니다",
        head ||
          (locked
            ? "이 문제는 한 번만 제출할 수 있습니다."
            : "라운드가 열리면 다시 제출해주세요.")
      );
      renderActions(card, resubmitBtn, false);
      setState("result");
      return;
    }

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

    // A run that never produced a verdict is not a wrong answer, and must not
    // wear the failure skin: "the judge could not run" would read as "your
    // circuit is wrong". The endpoint marks it either with a non-verdict status
    // or — for a run that evaluated no case at all — with a 0/0 count. A real
    // 0/M stays a failure and keeps its bar.
    const graderError =
      status === "error" ||
      status === "invalid" ||
      status === "rejected" ||
      total === 0;

    let klass, icon, titleHtml, subtitleText, showBar = false, pct = 0;

    if (graderError) {
      klass = "is-error";
      icon = ICON_ERROR;
      titleHtml = "채점 오류";
      subtitleText = head || "제출 파일을 확인해주세요.";
    } else if (status === "correct" && passed != null && total != null) {
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
      icon = ICON_PARTIAL;
      titleHtml =
        '<span class="count">' + passed + " / " + total + "</span> 테스트케이스 통과";
      subtitleText = "조금만 더 다듬어보세요.";
      showBar = true;
      pct = (passed / total) * 100;
    } else if (passed === 0 && total != null && total > 0) {
      klass = "is-fail";
      icon = ICON_FAIL;
      titleHtml =
        '<span class="count">0 / ' + total + "</span> 테스트케이스 통과";
      subtitleText = "회로 동작을 다시 점검해보세요.";
      showBar = true;
      pct = 0;
    } else {
      // No counts came back at all: a refused upload, a grader fault, or the
      // network handler above. Nothing was graded, so this is an error too —
      // only the wording is kept as it was.
      klass = "is-error";
      icon = ICON_ERROR;
      titleHtml = "채점 실패";
      subtitleText = head || "";
    }

    card.className = "result " + klass;
    card.innerHTML =
      headHtml(klass, icon, titleHtml, subtitleText) +
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

    // On a full pass 다음 문제 leads and 다시 제출하기 steps back to a quiet
    // link; on every other verdict 다시 제출하기 is the only action, and stays
    // the primary button.
    renderActions(card, resubmitBtn, klass === "is-pass");

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
      selectDroppedFolder(e.dataTransfer);
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
      const relativeName = file.webkitRelativePath || "submission/" + file.name;
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
