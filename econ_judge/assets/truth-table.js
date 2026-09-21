(function () {
  "use strict";

  const root = document.getElementById("econ-truth-root");
  const submit = document.getElementById("challenge-submit");
  const challengeInput = document.getElementById("challenge-id");
  const challengeId = Number(challengeInput && challengeInput.value);
  if (!root || !submit || root.dataset.locked === "true") return;

  const controls = Array.from(root.querySelectorAll('input[type="radio"]'));
  const result = document.getElementById("econ-truth-result");

  function answers() {
    const values = [];
    for (let row = 0; row < 8; row += 1) {
      const checked = root.querySelector('input[name="truth-' + row + '"]:checked');
      if (!checked) return null;
      values.push(Number(checked.value));
    }
    return values;
  }

  controls.forEach(function (control) {
    control.addEventListener("change", function () {
      submit.disabled = answers() === null;
    });
  });

  // The quiz shares its verdict vocabulary with the .dig upload panel
  // (view.js): the Korean state word leads in a chip, the sentence follows.
  // Both go in as text — server strings are never HTML — and a submission that
  // was refused or never reached the judge takes 안내 / 채점 오류 rather than
  // the failure skin, which belongs to a wrong answer alone.
  function showResult(klass, word, message) {
    if (!result) return;
    result.hidden = false;
    result.className = "ep-truth-result " + klass;
    result.textContent = "";
    const chip = document.createElement("span");
    chip.className = "state-chip";
    chip.textContent = word;
    const text = document.createElement("span");
    text.textContent = message;
    result.appendChild(chip);
    result.appendChild(text);
  }

  submit.addEventListener("click", async function () {
    const values = answers();
    if (!values) return;

    submit.disabled = true;
    submit.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> 채점 중';

    try {
      const nonce = (window.init && window.init.csrfNonce) || "";
      const response = await fetch("/api/v1/digital/challenges/" + challengeId + "/truth-table-attempt", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          ...(nonce ? { "CSRF-Token": nonce } : {}),
        },
        body: JSON.stringify({ answers: values }),
      });
      const payload = await response.json();
      const data = payload && payload.data ? payload.data : {};

      // "unavailable" means the round is shut, not that the one attempt is spent —
      // leave the form usable so the mentee can submit when the round reopens.
      const closed = data.status === "unavailable";
      const spent = data.status === "locked";
      if (closed) {
        submit.disabled = false;
        submit.innerHTML = '<i class="fa-solid fa-play" aria-hidden="true"></i> 채점 요청';
      } else {
        controls.forEach(function (control) { control.disabled = true; });
        submit.hidden = true;
      }
      if (closed || spent) {
        showResult(
          "is-notice",
          "안내",
          data.message ||
            (spent
              ? "이 문제는 한 번만 제출할 수 있습니다."
              : "라운드가 열리면 다시 제출해주세요.")
        );
      } else if (data.status === "correct") {
        showResult("is-pass", "전체 통과", data.message || "제출이 기록되었습니다.");
      } else {
        showResult("is-fail", "실패", data.message || "제출이 기록되었습니다.");
      }

      if (data.status === "correct") {
        const status = document.getElementById("econ-problem-status");
        if (status) {
          status.classList.add("is-solved");
          status.innerHTML = '<i class="fa-solid fa-check" aria-hidden="true"></i><span>완료</span>';
        }
      }
    } catch (error) {
      submit.disabled = false;
      submit.innerHTML = '<i class="fa-solid fa-play" aria-hidden="true"></i> 채점 요청';
      // No verdict came back at all, so this is not one: 채점 오류, not 실패.
      // The form stays usable, as it was, because the attempt may never have
      // been recorded.
      showResult(
        "is-error",
        "채점 오류",
        "제출하지 못했습니다. 연결을 확인한 뒤 다시 시도해주세요."
      );
    }
  });
})();
