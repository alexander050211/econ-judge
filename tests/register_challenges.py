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
    (1, "P1 A-1 Half Adder", "Project 1", 4,
     "Two-bit inputs P, Q → sum S and carry-out C_out. 4 testcases.", 4),
    (2, "P1 A-2 Full Adder", "Project 1", 7,
     "Three-bit inputs P, Q, C_in → sum S and carry-out C_out. 8 testcases.", 8),
    (3, "P1 A-3 3-bit Ripple Adder", "Project 1", 7,
     "Two 3-bit inputs X, Y → 4-bit sum S3..S0. 64 testcases.", 64),
    (4, "P2 A-1 2-bit Comparator", "Project 2", 6,
     "Two 2-bit inputs A, B → G/L/E (greater/less/equal). 16 testcases.", 16),
    (5, "연습 #1 Truth Table → Boolean", "연습", 3,
     "Implement the truth table {00→1, 01→0, 10→0, 11→1} (XNOR). 4 testcases.", 4),
    (6, "연습 #2 3-input AND", "연습", 3,
     "Build a 3-input AND using only 2-input AND gates. 8 testcases.", 8),
    (7, "연습 #3 2:1 MUX", "연습", 4,
     "Y = X0 if S=0 else X1. 8 testcases.", 8),
    (8, "미션 T1#1 NOR → NOT", "미션", 2,
     "Realize NOT using NOR gates only (gate budget honor-system). 2 testcases.", 2),
    (9, "미션 T1#2 NOR → AND", "미션", 3,
     "Realize AND using NOR gates only (gate budget honor-system). 4 testcases.", 4),
    (10, "미션 T1#3 NOR → XOR", "미션", 5,
     "Realize XOR using NOR gates only (gate budget honor-system). 4 testcases.", 4),
    (11, "미션 T2#4 Leap Year Detector (2000-2099)", "미션", 6,
     "BCD inputs A3..A0 (tens), B3..B0 (ones) → L. Only BCD-valid digits "
     "(both ≤ 9) are tested. 100 testcases.", 100),
    (12, "P1 B 보수 계산기", "Project 1", 7,
     "S3..S0 → R2..R0 where R = max(7-S, 0) clamped to 3 bits. 16 testcases.", 16),
    (13, "P1 C ÷3 Round-up", "Project 1", 7,
     "R2..R0 → T1, T0 where T = ⌈R/3⌉. 8 testcases.", 8),
    (14, "P2 B 대피소 배정", "Project 2", 6,
     "G, L, E, C2..C0 → Y (0=A, 1=B). Only one-hot G/L/E rows are tested. "
     "24 testcases.", 24),
    (15, "P2 A-2 2,3-bit Comparator", "Project 2", 3,
     "A1, A0 (2-bit) vs B2..B0 (3-bit) → G, L, E. A is zero-padded to 3 bits. "
     "Canonical P2 A-1 is seeded so this can be solved before P2 A-1. "
     "32 testcases.", 32),
    (16, "P1 Full Wiring (X, Y → T1, T0)", "Project 1", 10,
     "X2..X0, Y2..Y0 → T1, T0 — full pipeline ⌈max(7-(X+Y), 0)/3⌉. "
     "All P1 sub-circuits (A1, A2, A3, B, C) are seeded as canonical, so "
     "this can be solved standalone. 64 testcases.", 64),
    (17, "P2 C 7-segment Driver", "Project 2", 5,
     "Y → a, b, c, d, e, f, g (Out pins driving the camp's 7-seg display). "
     "Y=0 lights 'a' (a b c d e g); Y=1 lights 'b' (c d e f g). "
     "2 testcases.", 2),
    (18, "P2 Full Wiring (A, B, C → 7-seg)", "Project 2", 12,
     "A1 A0 (2-bit), B2..B0 (3-bit), C2..C0 (3-bit) → a..g. Full pipeline "
     "through A1, A2, B, C. 256 testcases. NOTE: needs canonical "
     "C_7segment출력기.dig with Out pins (deferred to 2026 skeleton "
     "authoring); will surface a grader-misconfigured error until present.",
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
