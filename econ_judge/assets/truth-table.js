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

      controls.forEach(function (control) { control.disabled = true; });
      submit.hidden = true;
      if (result) {
        result.hidden = false;
        result.className = "ep-truth-result " + (data.status === "correct" ? "is-pass" : "is-fail");
        result.textContent = data.message || "제출이 기록되었습니다.";
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
      if (result) {
        result.hidden = false;
        result.className = "ep-truth-result is-fail";
        result.textContent = "제출하지 못했습니다. 연결을 확인한 뒤 다시 시도해주세요.";
      }
    }
  });
})();
