// Disable CTFd's default text-flag submission path.
CTFd._internal.challenge.data = undefined;
CTFd._internal.challenge.renderer = null;
CTFd._internal.challenge.preRender = function () {};
CTFd._internal.challenge.render = null;
CTFd._internal.challenge.postRender = function () {};

(function () {
  "use strict";

  const MAX_BYTES = 256 * 1024;

  const $ = (sel, root) => (root || document).querySelector(sel);

  function root() {
    return document.getElementById("econ-submit-root");
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

  function selectFile(file) {
    if (!file) return;
    const name = file.name || "";
    if (!name.toLowerCase().endsWith(".dig")) {
      showFileError(".dig 파일만 업로드할 수 있습니다.");
      return;
    }
    if (file.size === 0) {
      showFileError("빈 파일입니다.");
      return;
    }
    if (file.size > MAX_BYTES) {
      showFileError(
        "파일이 너무 큽니다 (" +
          humanSize(file.size) +
          " > 256 KB 한도)."
      );
      return;
    }
    // Sync the chosen file into the hidden <input type="file"> so
    // submitChallenge can read it from the standard place.
    const input = $("#challenge-file");
    if (input) {
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
    }
    clearFileError();
    $("#econ-file-name").textContent = name;
    $("#econ-file-size").textContent = humanSize(file.size);
    setState("ready");
  }

  function clearSelection() {
    const input = $("#challenge-file");
    if (input) input.value = "";
    clearFileError();
    setState("empty");
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
    const { head, detail, passed, total } = parseResult(data && data.message);

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
      (detail
        ? '<details><summary>상세 보기</summary><pre>' + escapeHtml(detail) + "</pre></details>"
        : "");

    setState("result");

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
      const f = e.target.files && e.target.files[0];
      if (f) selectFile(f);
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
      const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) selectFile(f);
    });

    if (clearBtn) clearBtn.addEventListener("click", clearSelection);
    if (resubmitBtn) resubmitBtn.addEventListener("click", clearSelection);

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

  // ---------- Submit (called by the modal's submit button) ----------

  CTFd.pages.challenge.submitChallenge = async function (challenge_id, _submission) {
    const input = document.getElementById("challenge-file");
    if (!input || !input.files || !input.files.length) {
      return {
        data: {
          status: "incorrect",
          message: ".dig 파일을 먼저 선택해주세요.",
        },
      };
    }

    setState("grading");

    const fd = new FormData();
    fd.append("file", input.files[0]);

    let result;
    try {
      const r = await fetch(
        "/api/v1/digital/challenges/" + challenge_id + "/attempt",
        { method: "POST", body: fd, credentials: "same-origin" }
      );
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

    renderResult(result.data || {});
    return result;
  };
})();
