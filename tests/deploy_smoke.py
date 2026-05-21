"""End-to-end smoke test against the live Render deploy: register a throwaway
user, log in, submit every challenge that has a 5조 reference submission, and
verify each grades as expected.

Coverage:
  chal 1, 2, 3      P1 A-1, A-2, A-3   (HA, FA composed, 3-bit composed)
  chal 4, 15        P2 A-1, A-2        (2-bit compare, 2-3-bit composed)
  chal 5, 6, 7      연습 1-3
  chal 8, 9, 10     미션 T1#1-3
  chal 11           미션 T2#4 (leap year, 100 rows)
  chal 12, 13, 14   P1 B, P1 C, P2 B

Not covered: chal 16 (P1 full wiring, no 5조 file), chal 17/18 (await 2026
skeleton)."""

import os
import re
import sys

import requests


def _load_dotenv() -> None:
    """Best-effort .env loader (no python-dotenv dep). Values from the shell
    env take precedence — `os.environ.setdefault` only sets unset keys."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

BASE = os.environ.get("ECON_JUDGE_URL", "https://econ-judge.onrender.com")
USERNAME = os.environ.get("SMOKE_USER", "smoke-test-1")
EMAIL = os.environ.get("SMOKE_EMAIL", "smoke1@econ-judge.local")
PASSWORD = os.environ.get("SMOKE_PASSWORD", "smoketest-pw-1")

SAMPLES_ROOT = "tests/samples/5jo-26winter"
SAMPLES = {
    1: f"{SAMPLES_ROOT}/5조_프로젝트1/A1_반가산기(HalfAdder)만들기.dig",
    2: f"{SAMPLES_ROOT}/5조_프로젝트1/A2_전가산기(FullAdder)만들기.dig",
    3: f"{SAMPLES_ROOT}/5조_프로젝트1/A3_3비트덧셈연산기만들기.dig",
    4: f"{SAMPLES_ROOT}/5조_프로젝트2/A1_2비트비교기.dig",
    5: f"{SAMPLES_ROOT}/5조_연습문제/1번_진리표와bool대수.dig",
    6: f"{SAMPLES_ROOT}/5조_연습문제/2번_입력이3개인AND게이트.dig",
    7: f"{SAMPLES_ROOT}/5조_연습문제/3번_2대1멀티플렉서(MUX).dig",
    8: f"{SAMPLES_ROOT}/5조_미션문제/1번_NOR게이트로NOT게이트만들기.dig",
    9: f"{SAMPLES_ROOT}/5조_미션문제/2번_NOR게이트로AND게이트만들기.dig",
    10: f"{SAMPLES_ROOT}/5조_미션문제/3번_NOR게이트로XOR게이트만들기.dig",
    11: f"{SAMPLES_ROOT}/5조_미션문제/4번_21세기윤년판독기만들기.dig",
    12: f"{SAMPLES_ROOT}/5조_프로젝트1/B_보수계산기만들기.dig",
    13: f"{SAMPLES_ROOT}/5조_프로젝트1/C_3의나눗셈기만들기.dig",
    14: f"{SAMPLES_ROOT}/5조_프로젝트2/B_대피소배정하기.dig",
    15: f"{SAMPLES_ROOT}/5조_프로젝트2/A2_2,3비트비교기.dig",
}


def nonce_from(html: str) -> str:
    m = re.search(r'name="nonce"[^>]+value="([^"]+)"', html)
    if not m:
        raise RuntimeError("no nonce in page")
    return m.group(1)


def authed_session() -> requests.Session:
    s = requests.Session()
    r = s.get(f"{BASE}/register")
    r.raise_for_status()
    s.post(
        f"{BASE}/register",
        data={"name": USERNAME, "email": EMAIL, "password": PASSWORD, "nonce": nonce_from(r.text)},
    )
    r = s.get(f"{BASE}/login")
    r = s.post(
        f"{BASE}/login",
        data={"name": USERNAME, "password": PASSWORD, "nonce": nonce_from(r.text)},
        allow_redirects=True,
    )
    if "'userId': 0" in r.text or "userId" not in r.text:
        raise RuntimeError("login didn't establish session")
    return s


def submit(s: requests.Session, cid: int, path: str) -> dict:
    abs_path = os.path.abspath(path)
    with open(abs_path, "rb") as f:
        r = s.post(
            f"{BASE}/api/v1/digital/challenges/{cid}/attempt",
            files={"file": (os.path.basename(abs_path), f, "application/octet-stream")},
            timeout=60,
        )
    r.raise_for_status()
    return r.json()["data"]


def main() -> int:
    s = authed_session()
    print(f"[smoke] logged in as {USERNAME}")
    print(f"[smoke] submitting {len(SAMPLES)} challenges...")
    print()

    fails = []
    for cid in sorted(SAMPLES):
        data = submit(s, cid, SAMPLES[cid])
        status = data.get("status")
        msg = data.get("message", "").split("\n")[0]
        marker = "OK  " if status == "correct" else "FAIL"
        print(f"  {marker}  chal {cid:>2}: {status:<10} {msg}")
        if status != "correct":
            fails.append((cid, data))

    print()
    if fails:
        print(f"[smoke] {len(fails)}/{len(SAMPLES)} FAILED")
        for cid, data in fails:
            print(f"  chal {cid}: {data}")
        return 1
    print(f"[smoke] PASS: {len(SAMPLES)}/{len(SAMPLES)} challenges graded correctly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
