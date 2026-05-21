"""Register the digital-typed challenges in a running CTFd instance.

Authenticates as admin via session cookie, scrapes the CSRF nonce from the
post-login page, then POSTs each challenge to /api/v1/challenges. Skips any
challenge whose id already exists. Reads CHALLENGES from this file (the source
of truth for the mapping challenge_id ↔ secret_tests/<id>.dig).

Usage: python tests/register_challenges.py [--base-url http://127.0.0.1:4000]
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Optional

import requests

CHALLENGES = [
    # id, name, category, value, description, secret-tests-row-count
    #
    # Pedagogical ramp: 연습 problems are the on-ramp AND the building blocks
    # for Project problems. HA (id 1) and FA (id 2) used to live in P1 but
    # are now in 연습 — they're literally the foundation circuits, and the
    # P1 grader auto-seeds canonical copies into the harder problems' tempdirs
    # so solving the project problems doesn't require these to be solved first.
    # Description annotations make that link explicit.
    (1, "연습 #1 Half Adder", "연습", 4,
     "두 비트 입력 P, Q → 합 S와 받아올림 C_out. 4 testcases. "
     "💡 이 회로는 P1 3비트 가산기 / P1 Full Wiring의 부품으로 자동 활용됩니다.",
     4),
    (2, "연습 #2 Full Adder", "연습", 7,
     "세 비트 입력 P, Q, C_in → 합 S와 받아올림 C_out. 8 testcases. "
     "💡 이 회로는 P1 3비트 가산기 / P1 Full Wiring의 부품으로 자동 활용됩니다.",
     8),
    (3, "P1 A-3 3-bit Ripple Adder", "Project 1", 7,
     "두 3비트 입력 X, Y → 4비트 합 S3..S0. 64 testcases. "
     "💡 연습 #1 (HA) / 연습 #2 (FA) 회로가 표준 구현으로 자동 제공되므로, "
     "두 연습 문제를 먼저 풀지 않아도 이 문제를 풀 수 있습니다.",
     64),
    (4, "P2 A-1 2-bit Comparator", "Project 2", 6,
     "두 2비트 입력 A, B → G/L/E (greater/less/equal). 16 testcases.", 16),
    (5, "연습 #3 Truth Table → Boolean", "연습", 3,
     "진리표 {00→1, 01→0, 10→0, 11→1} (XNOR) 구현. 4 testcases.", 4),
    (6, "연습 #4 3-input AND", "연습", 3,
     "2-입력 AND 게이트만으로 3-입력 AND 구성. 8 testcases.", 8),
    (7, "연습 #5 2:1 MUX", "연습", 4,
     "Y = X0 if S=0 else X1. 8 testcases. "
     "💡 멀티플렉서 구성은 P2 B 대피소 배정의 핵심 패턴입니다.",
     8),
    (8, "미션 T1#1 NOR → NOT", "미션", 2,
     "NOR 게이트만으로 NOT 구현 (게이트 수는 명예 제도). 2 testcases.", 2),
    (9, "미션 T1#2 NOR → AND", "미션", 3,
     "NOR 게이트만으로 AND 구현 (게이트 수는 명예 제도). 4 testcases.", 4),
    (10, "미션 T1#3 NOR → XOR", "미션", 5,
     "NOR 게이트만으로 XOR 구현 (게이트 수는 명예 제도). 4 testcases.", 4),
    (11, "미션 T2#4 Leap Year Detector (2000-2099)", "미션", 6,
     "BCD 입력 A3..A0 (10의 자리), B3..B0 (1의 자리) → L. "
     "BCD 유효 자릿수 (둘 다 ≤ 9)만 채점. 100 testcases.", 100),
    (12, "P1 B 보수 계산기", "Project 1", 7,
     "S3..S0 → R2..R0, 여기서 R = max(7-S, 0)를 3비트로 클램프. 16 testcases. "
     "💡 P1 A 시리즈의 표준 구현이 부품으로 자동 제공됩니다.",
     16),
    (13, "P1 C ÷3 Round-up", "Project 1", 7,
     "R2..R0 → T1, T0, 여기서 T = ⌈R/3⌉. 8 testcases.", 8),
    (14, "P2 B 대피소 배정", "Project 2", 6,
     "G, L, E, C2..C0 → Y (0=A, 1=B). G/L/E one-hot 행만 채점. 24 testcases. "
     "💡 연습 #5 (2:1 MUX) 패턴이 이 문제 풀이의 핵심 힌트입니다.",
     24),
    (15, "P2 A-2 2,3-bit Comparator", "Project 2", 3,
     "A1, A0 (2비트) vs B2..B0 (3비트) → G, L, E. A는 3비트로 zero-pad. "
     "P2 A-1 표준 구현이 부품으로 자동 제공되므로 P2 A-1 전에도 풀 수 있습니다. "
     "32 testcases.", 32),
    (16, "P1 Full Wiring (X, Y → T1, T0)", "Project 1", 10,
     "X2..X0, Y2..Y0 → T1, T0 — 전체 파이프라인 ⌈max(7-(X+Y), 0)/3⌉. 64 testcases. "
     "💡 P1의 모든 부품 회로 (연습 #1 HA, 연습 #2 FA, P1 A-3 3비트 가산기, "
     "P1 B 보수, P1 C ÷3) 표준 구현이 자동 제공되므로 독립적으로 풀 수 있습니다.",
     64),
    (17, "P2 C 7-segment Driver", "Project 2", 5,
     "Y → a, b, c, d, e, f, g (캠프 7-seg 디스플레이 Out 핀). "
     "Y=0이면 'a' 점등 (a b c d e g); Y=1이면 'b' 점등 (c d e f g). 2 testcases.", 2),
    (18, "P2 Full Wiring (A, B, C → 7-seg)", "Project 2", 12,
     "A1 A0 (2비트), B2..B0 (3비트), C2..C0 (3비트) → a..g. P2 A1, A2, B, C 전체 "
     "파이프라인. 256 testcases. NOTE: 표준 C_7segment출력기.dig (Out 핀 포함) "
     "필요 — 2026 skeleton 작성 시 추가 예정. 그 전까지는 grader-misconfigured "
     "에러로 응답함.",
     256),
]


def login(base_url: str, name: str, password: str) -> tuple[requests.Session, str]:
    s = requests.Session()
    r = s.get(f"{base_url}/login")
    r.raise_for_status()
    nonce = re.search(r'name="nonce"[^>]+value="([^"]+)"', r.text).group(1)
    r = s.post(
        f"{base_url}/login",
        data={"name": name, "password": password, "nonce": nonce},
        allow_redirects=True,
    )
    # Login success → redirect away from /login; CTFd renders the new page with
    # a fresh csrfNonce inside window.init for subsequent API calls.
    m = re.search(r"'csrfNonce':\s*\"([0-9a-f]+)\"", r.text)
    if not m:
        raise RuntimeError(f"login failed; no csrfNonce in response (status={r.status_code})")
    return s, m.group(1)


def list_challenge_ids(s: requests.Session, base_url: str) -> set[int]:
    r = s.get(f"{base_url}/api/v1/challenges?view=admin")
    r.raise_for_status()
    return {c["id"] for c in r.json()["data"]}


def create_challenge(
    s: requests.Session,
    base_url: str,
    csrf: str,
    cid: int,
    name: str,
    category: str,
    value: int,
    description: str,
) -> Optional[dict]:
    payload = {
        "name": name,
        "category": category,
        "description": description,
        "value": value,
        "state": "visible",
        "type": "digital",
    }
    r = s.post(
        f"{base_url}/api/v1/challenges",
        json=payload,
        headers={"CSRF-Token": csrf, "Content-Type": "application/json"},
    )
    if r.status_code != 200:
        print(f"  chal {cid}: HTTP {r.status_code} {r.text[:300]}", file=sys.stderr)
        return None
    data = r.json().get("data") or {}
    created_id = data.get("id")
    if created_id != cid:
        print(
            f"  chal {cid}: created with id={created_id} (DB auto-assigned, may "
            f"not match secret_tests/{cid}.dig — fix DB or rename file)",
            file=sys.stderr,
        )
    return data


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://127.0.0.1:4000")
    p.add_argument("--admin", default="admin")
    p.add_argument("--password", default="demo1234")
    args = p.parse_args()

    s, csrf = login(args.base_url, args.admin, args.password)
    existing = list_challenge_ids(s, args.base_url)
    print(f"existing challenge ids: {sorted(existing)}")

    created = []
    skipped = []
    for cid, name, category, value, description, _rows in CHALLENGES:
        if cid in existing:
            skipped.append(cid)
            continue
        data = create_challenge(s, args.base_url, csrf, cid, name, category, value, description)
        if data:
            created.append((cid, data.get("id"), name))

    print(f"\ncreated {len(created)}:")
    for cid, real_id, name in created:
        marker = "" if real_id == cid else f"  ⚠ assigned id={real_id}, expected {cid}"
        print(f"  {real_id:>2}: {name}{marker}")
    print(f"skipped (already present): {sorted(skipped)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
