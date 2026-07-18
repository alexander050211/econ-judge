"""Register the 2026 summer digital-logic challenges in a running CTFd.

The ``CHALLENGES`` list is also imported by ``bin/bootstrap.py`` and is the
source of truth for challenge ids, ordering, points, statements, and the
mapping to ``secret_tests/<id>.dig``.

Usage: python tests/register_challenges.py [--base-url http://127.0.0.1:4000]
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Optional

import requests


CHALLENGES = [
    # id, name, category, value, Markdown description, graded row count
    (1, "연습 #1 논리 게이트를 통해 진리표 채우기", "연습", 2,
     """아래 논리회로의 모든 입력 조합을 확인하고 출력 `Y`를 채우세요.

![A, B, C 입력과 Y 출력으로 이루어진 논리회로](/plugins/econ_judge/assets/problems/practice-1-circuit.png)

입력은 `A`, `B`, `C`이며 각 입력은 `0` 또는 `1`입니다. 이 문제는 **한 번만 제출할 수 있습니다.**""",
     8),
    (2, "연습 #2 진리표를 통해 회로 만들어보기", "연습", 2,
     """다음 진리표와 같은 동작을 하는 회로를 설계하세요.

| A | B | Y |
|---|---|---|
| 0 | 0 | 0 |
| 0 | 1 | 1 |
| 1 | 0 | 1 |
| 1 | 1 | 0 |

입력 단자는 `A`, `B`, 출력 단자는 `Y`입니다.""",
     4),
    (3, "연습 #3 입력이 3개인 AND 게이트", "연습", 3,
     """2입력 AND 게이트만 사용하여 3입력 AND 회로를 설계하세요.

입력 단자는 `A`, `B`, `C`, 출력 단자는 `Y`입니다. 세 입력이 모두 `1`일 때만 `Y=1`이어야 합니다.""",
     8),
    (4, "연습 #4 2:1 멀티플렉서", "연습", 3,
     """두 입력 중 선택 신호가 지정한 값을 출력하는 2:1 멀티플렉서를 설계하세요.

- 입력: `X0`, `X1`, `S`
- 출력: `Y`
- `S=0`이면 `Y=X0`
- `S=1`이면 `Y=X1`""",
     8),

    (5, "미션 #1 NAND 게이트로 NOT 게이트 만들기", "미션", 2,
     """**NAND 게이트 정확히 1개만** 사용하여 NOT 게이트를 설계하세요.

입력 단자는 `A`, 출력 단자는 `Y`입니다. NAND 외의 논리 게이트는 사용할 수 없습니다.""",
     2),
    (6, "미션 #2 NAND 게이트로 OR 게이트 만들기", "미션", 3,
     """드모르간의 법칙을 이용하여 **NAND 게이트 정확히 3개만**으로 OR 게이트를 설계하세요.

입력 단자는 `A`, `B`, 출력 단자는 `Y`입니다. NAND 외의 논리 게이트는 사용할 수 없습니다.""",
     4),
    (7, "미션 #3 반가산기 만들기", "미션", 6,
     """두 1비트 값 `P`, `Q`를 더하는 반가산기를 설계하세요.

- 입력: `P`, `Q`
- 출력: 합 `S`, 받아올림 `C_out`

`S`는 덧셈 결과의 낮은 자리이며, `C_out`은 받아올림입니다.""",
     4),
    (8, "미션 #4 전가산기 만들기", "미션", 6,
     """두 1비트 값과 이전 자리의 받아올림을 더하는 전가산기를 설계하세요.

- 입력: `P`, `Q`, `C_in`
- 출력: 합 `S`, 받아올림 `C_out`

세 입력의 합을 2비트 결과 `(C_out, S)`로 출력해야 합니다.""",
     8),
    (9, "미션 #5 3비트 덧셈 연산기 만들기", "미션", 8,
     """두 3비트 이진수 `X`, `Y`를 더하여 4비트 합 `S`를 출력하세요.

- 입력: `X2`, `X1`, `X0`, `Y2`, `Y1`, `Y0`
- 출력: `S3`, `S2`, `S1`, `S0`

`X0`, `Y0`, `S0`이 각 수의 가장 낮은 자리입니다.""",
     64),
    (10, "미션 #6 21세기 윤년 판독기", "미션", 10,
     """2000년부터 2099년까지의 연도가 윤년인지 판별하는 회로를 설계하세요.

- `A3`~`A0`: 연도 끝 두 자리 중 십의 자리 BCD
- `B3`~`B0`: 연도 끝 두 자리 중 일의 자리 BCD
- 출력 `L`: 윤년이면 `1`, 평년이면 `0`

`A0`과 `B0`이 가장 낮은 자리입니다. 각 BCD 자리가 9를 초과하는 입력은 채점하지 않습니다.

예: 2000년은 `L=1`, 2026년은 `L=0`입니다.""",
     100),
    (11, "미션 #7 A+B+C 등식 회로", "미션", 10,
     """두 입력에서는 다음 등식이 성립합니다.

`A + B = (A AND B) + (A OR B)`

이 관계를 세 입력으로 확장하여, 다음 등식을 만족하는 세 논리 출력 `Y1`, `Y2`, `Y3`를 찾아 회로로 구현하세요.

`A + B + C = Y1 + Y2 + Y3`

- 모든 입력 조합에서 `Y1 >= Y2 >= Y3`이어야 합니다.
- 왼쪽과 오른쪽의 `+`는 논리 OR가 아니라 비트 값의 산술 합입니다.
- 입력 단자: `A`, `B`, `C`
- 출력 단자: `Y1`, `Y2`, `Y3`""",
     8),

    (12, "프로젝트 A-1 주어진 수가 1 이상인지 판단하기", "프로젝트", 4,
     """2비트 이진수 `X`가 1 이상인지 판단하는 회로를 설계하세요.

- 입력: `X1`, `X0` (`X0`이 가장 낮은 자리)
- 출력: `S0`
- `X>=1`이면 `S0=1`, `X=0`이면 `S0=0`""",
     4),
    (13, "프로젝트 A-2 홍수 경보 판단", "프로젝트", 8,
     """세 조건 `X`, `Y`, `Z` 중 값이 1 이상인 조건이 두 개 이상인지 판단하세요. 각 조건은 0부터 3까지의 2비트 이진수입니다.

- 입력: `X1`, `X0`, `Y1`, `Y0`, `Z1`, `Z0`
- 출력: `S0`
- 1 이상인 값이 두 개 이상이면 `S0=1`, 아니면 `S0=0`""",
     64),
    (14, "프로젝트 B 홍수 위험 지역 판단", "프로젝트", 10,
     """0부터 3까지의 2비트 값 `X`, `Y`, `Z`를 받아 다음 위험도 조건을 판별하세요.

`X² + Y² + Z >= 14`

- 입력: `X1`, `X0`, `Y1`, `Y0`, `Z1`, `Z0`
- 출력: `S0`
- 조건이 참이면 `S0=1`, 아니면 `S0=0`

예: `(X,Y,Z)=(2,3,1)`이면 `1`, `(2,2,3)`이면 `0`입니다.""",
     64),
    (15, "프로젝트 C 7-segment 출력기", "프로젝트", 5,
     """Part B의 1비트 결과를 받아 7-segment에 문자를 표시하세요.

- 입력: `Y`
- 출력: `a`, `b`, `c`, `d`, `e`, `f`, `g`, `dp`
- `Y=1`이면 소문자 `y`
- `Y=0`이면 소문자 `n`
- 출력값 `1`은 해당 획이 켜짐을 뜻합니다.
- Digital의 `Seven-Seg` 부품을 정확히 1개 배치하고, 각 출력 단자를 같은 이름의 부품 입력에 연결하세요.
- 소수점 입력 `dp`는 항상 `0`이어야 합니다.

![7-segment의 a부터 g까지 획 배치](/plugins/econ_judge/assets/problems/seven-segment-map.png)

| `Y=1`: y | `Y=0`: n |
|---|---|
| ![y 모양](/plugins/econ_judge/assets/problems/seven-segment-y.png) | ![n 모양](/plugins/econ_judge/assets/problems/seven-segment-n.png) |""",
     2),
]


def login(base_url: str, name: str, password: str) -> tuple[requests.Session, str]:
    session = requests.Session()
    response = session.get(f"{base_url}/login")
    response.raise_for_status()
    nonce = re.search(r'name="nonce"[^>]+value="([^"]+)"', response.text).group(1)
    response = session.post(
        f"{base_url}/login",
        data={"name": name, "password": password, "nonce": nonce},
        allow_redirects=True,
    )
    match = re.search(r"'csrfNonce':\s*\"([0-9a-f]+)\"", response.text)
    if not match:
        raise RuntimeError(
            f"login failed; no csrfNonce in response (status={response.status_code})"
        )
    return session, match.group(1)


def list_challenge_ids(session: requests.Session, base_url: str) -> set[int]:
    response = session.get(f"{base_url}/api/v1/challenges?view=admin")
    response.raise_for_status()
    return {challenge["id"] for challenge in response.json()["data"]}


def create_challenge(
    session: requests.Session,
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
    response = session.post(
        f"{base_url}/api/v1/challenges",
        json=payload,
        headers={"CSRF-Token": csrf, "Content-Type": "application/json"},
    )
    if response.status_code != 200:
        print(
            f"  chal {cid}: HTTP {response.status_code} {response.text[:300]}",
            file=sys.stderr,
        )
        return None
    data = response.json().get("data") or {}
    created_id = data.get("id")
    if created_id != cid:
        print(
            f"  chal {cid}: created with id={created_id} (expected {cid})",
            file=sys.stderr,
        )
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:4000")
    parser.add_argument("--admin", default="admin")
    parser.add_argument("--password", default="demo1234")
    args = parser.parse_args()

    session, csrf = login(args.base_url, args.admin, args.password)
    existing = list_challenge_ids(session, args.base_url)
    print(f"existing challenge ids: {sorted(existing)}")

    created = []
    skipped = []
    for cid, name, category, value, description, _rows in CHALLENGES:
        if cid in existing:
            skipped.append(cid)
            continue
        data = create_challenge(
            session, args.base_url, csrf, cid, name, category, value, description
        )
        if data:
            created.append((cid, data.get("id"), name))

    print(f"\ncreated {len(created)}:")
    for cid, real_id, name in created:
        marker = "" if real_id == cid else f"  assigned id={real_id}, expected {cid}"
        print(f"  {real_id:>2}: {name}{marker}")
    print(f"skipped (already present): {sorted(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
