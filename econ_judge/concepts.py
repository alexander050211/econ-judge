"""Per-challenge concept manifests + per-concept directional hints for the
submission testbench UI.

Why this exists: Digital grades one full truth-table row per `Testcase`, so on
a wrong answer the system knows only the aggregate K/N count — NOT which case or
input combo failed. The submission modal therefore shows, on any non-pass, a
CHECKLIST of every aspect the circuit must satisfy, each with a short directional
hint ("re-check X"), never a per-case result.

Each challenge maps to an ordered list of {label, hint}:
  - `label`: the aspect/stage being verified. Names the ASPECT, never the
    boolean rule (e.g. "받아올림(Carry)", "보수 계산", "윤년 조건") — a label must
    not hand over the equation the way a label like "7의 보수" or "C≥4" would.
  - `hint`: DIRECTIONAL ONLY (project decision, 2026-06-01). Points the student
    at what to re-check; never restates the rule, the formula, or an input
    vector. "받아올림(C_out) 출력을 다시 확인해 보세요." — not "C_out = P AND Q".

`label` drives the bench manifest animation (a line per aspect). `hint` is shown
in the result panel's "확인해 볼 점" checklist on a wrong/partial answer. Neither
carries any per-case pass/fail signal, so the whole list is safe to send on
every response.

Keys are challenge ids, matching tests/generate_secret_tests.py SPECS and
econ_judge/endpoints.py _PROJECT_PHASE_COLS.
"""

CHALLENGE_CONCEPTS = {
    # ── 연습 (practice) ──────────────────────────────────────────
    5: [  # XNOR  A,B → Y
        {"label": "같음 판정", "hint": "같음 판정 출력을 다시 확인해 보세요."},
    ],
    6: [  # 3-input AND  A,B,C → Y
        {"label": "3입력 판정", "hint": "세 입력에 대한 출력을 다시 확인해 보세요."},
    ],
    7: [  # 2:1 MUX  X0,X1,S → Y
        {"label": "선택 OFF 경로", "hint": "선택 신호가 꺼졌을 때 통과하는 입력을 확인해 보세요."},
        {"label": "선택 ON 경로", "hint": "선택 신호가 켜졌을 때 통과하는 입력을 확인해 보세요."},
    ],
    # ── 미션 (mission) ───────────────────────────────────────────
    8: [  # NOT  A → Y
        {"label": "반전", "hint": "입력에 대한 반전 출력을 다시 확인해 보세요."},
    ],
    9: [  # AND  A,B → Y
        {"label": "기본 판정", "hint": "두 입력에 대한 출력을 다시 확인해 보세요."},
    ],
    10: [  # XOR  A,B → Y
        {"label": "배타적 판정", "hint": "두 입력에 대한 출력을 다시 확인해 보세요."},
    ],
    11: [  # leap year  BCD → L
        {"label": "BCD 자리 해석", "hint": "입력된 BCD 자리를 올바르게 해석했는지 확인해 보세요."},
        {"label": "윤년 조건", "hint": "윤년 조건이 올바르게 적용됐는지 확인해 보세요."},
        {"label": "판정 출력", "hint": "최종 윤년 판정 출력을 다시 확인해 보세요."},
    ],
    # ── Project 1 ────────────────────────────────────────────────
    1: [  # half adder  P,Q → S,C_out
        {"label": "합(Sum)", "hint": "합(Sum) 출력을 다시 확인해 보세요."},
        {"label": "받아올림(Carry)", "hint": "받아올림(C_out) 출력을 다시 확인해 보세요."},
    ],
    2: [  # full adder  P,Q,C_in → S,C_out
        {"label": "합(Sum)", "hint": "합(Sum) 출력을 다시 확인해 보세요."},
        {"label": "받아올림(Carry)", "hint": "받아올림(C_out) 출력을 다시 확인해 보세요."},
        {"label": "올림 입력 반영", "hint": "올림 입력(C_in)이 합과 받아올림에 모두 반영됐는지 확인해 보세요."},
    ],
    3: [  # 3-bit adder  X,Y → S3..S0
        {"label": "하위 비트 합", "hint": "하위 비트의 합을 다시 확인해 보세요."},
        {"label": "자리올림 전파", "hint": "자리올림이 다음 비트로 전파되는지 확인해 보세요."},
        {"label": "최상위 자리올림", "hint": "최상위 자리올림(S3)을 확인해 보세요."},
    ],
    12: [  # complement  S → R
        {"label": "보수 계산", "hint": "보수 계산 결과를 다시 확인해 보세요."},
        {"label": "하한 처리", "hint": "결과가 음수가 되지 않도록 처리했는지 확인해 보세요."},
    ],
    13: [  # divide by 3 (ceil)  R → T
        {"label": "몫 계산", "hint": "나눗셈의 몫을 다시 확인해 보세요."},
        {"label": "나머지 처리", "hint": "나머지가 있을 때 몫 처리 방식을 다시 확인해 보세요."},
    ],
    16: [  # P1 full wiring  X,Y → T1,T0
        {"label": "덧셈 단계", "hint": "덧셈 단계의 출력을 다시 확인해 보세요."},
        {"label": "보수 단계", "hint": "보수 단계의 출력을 다시 확인해 보세요."},
        {"label": "나눗셈 단계", "hint": "나눗셈 단계의 출력을 다시 확인해 보세요."},
        {"label": "단계 간 연결", "hint": "각 단계의 출력이 다음 단계 입력으로 연결됐는지 확인해 보세요."},
    ],
    # ── Project 2 ────────────────────────────────────────────────
    4: [  # 2-bit comparator  A,B → G,L,E
        {"label": "크다(G)", "hint": "크다(G) 출력을 다시 확인해 보세요."},
        {"label": "작다(L)", "hint": "작다(L) 출력을 다시 확인해 보세요."},
        {"label": "같다(E)", "hint": "같다(E) 출력을 다시 확인해 보세요."},
        {"label": "출력 배타성", "hint": "세 출력 중 하나만 켜지는지 확인해 보세요."},
    ],
    15: [  # 2,3-bit comparator  A,B → G,L,E
        {"label": "자리 맞춤", "hint": "두 값의 자리수를 맞춰 비교했는지 확인해 보세요."},
        {"label": "대소 판정", "hint": "크다·작다·같다 출력을 다시 확인해 보세요."},
        {"label": "출력 배타성", "hint": "세 출력 중 하나만 켜지는지 확인해 보세요."},
    ],
    14: [  # shelter assignment  G,L,E,C → Y
        {"label": "비교 결과 분기", "hint": "비교 결과에 따른 대피소 분기를 다시 확인해 보세요."},
        {"label": "정원 기준", "hint": "정원 조건 판정을 다시 확인해 보세요."},
    ],
    17: [  # 7-segment  Y → a..g
        {"label": "글자 'a' 패턴", "hint": "글자 'a' 모양에 맞게 세그먼트가 켜지는지 확인해 보세요."},
        {"label": "글자 'b' 패턴", "hint": "글자 'b' 모양에 맞게 세그먼트가 켜지는지 확인해 보세요."},
    ],
    18: [  # P2 full wiring  A,B,C → a..g
        {"label": "비교 단계", "hint": "비교 단계의 출력을 다시 확인해 보세요."},
        {"label": "대피소 배정", "hint": "대피소 배정 결과를 다시 확인해 보세요."},
        {"label": "7세그먼트 출력", "hint": "7세그먼트 출력이 글자 모양에 맞는지 확인해 보세요."},
        {"label": "단계 간 연결", "hint": "비교부터 출력까지 각 단계가 연결됐는지 확인해 보세요."},
    ],
}

DEFAULT_CONCEPTS = [
    {"label": "회로 동작", "hint": "회로의 출력이 문제 조건과 일치하는지 다시 확인해 보세요."},
]


def concept_info(challenge_id: int) -> list:
    """Return the ordered [{label, hint}, ...] manifest for a challenge, or a
    safe generic default for any id without an authored entry."""
    return CHALLENGE_CONCEPTS.get(challenge_id, DEFAULT_CONCEPTS)
