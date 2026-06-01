"""Per-challenge concept manifests + generic fail hints for the submission
testbench UI.

Why this exists: Digital grades one full truth-table row per `Testcase`, and
the raw per-case results (which case index passed/failed) used to be echoed to
students. That let a student pinpoint-patch the exact failing input combo
instead of reasoning about the circuit. The submission modal now shows, on a
failed attempt, only an aggregate `K/N` count plus ONE generic, concept-level
hint per challenge — never a case index or input vector.

`concepts` is a short, student-facing list of the *aspects* a challenge
verifies. It drives the "testbench" animation in the grading state (a manifest
of what is being checked) and is purely educational — it is NOT derived from,
and carries no, per-case pass/fail signal.

`hint` is the single line shown under the aggregate count on any failure. Keep
it directional but vector-free ("받아올림 출력을 확인" — never "P=1,Q=0 에서 틀림").

Keys are challenge ids, matching tests/generate_secret_tests.py SPECS and
econ_judge/endpoints.py _PROJECT_PHASE_COLS.
"""

CHALLENGE_CONCEPTS = {
    # ── 연습 (practice) ──────────────────────────────────────────
    5: {  # XNOR  A,B → Y
        "concepts": ["같음 판정(XNOR)"],
        "hint": "두 입력이 같을 때만 1이 나오는지 확인해 보세요.",
    },
    6: {  # 3-input AND  A,B,C → Y
        "concepts": ["3입력 AND"],
        "hint": "세 입력이 모두 1일 때만 1이 나오는지 확인해 보세요.",
    },
    7: {  # 2:1 MUX  X0,X1,S → Y
        "concepts": ["선택 신호 OFF 경로", "선택 신호 ON 경로"],
        "hint": "선택 신호(S)에 따라 올바른 입력이 통과되는지 확인해 보세요.",
    },
    # ── 미션 (mission) ───────────────────────────────────────────
    8: {  # NOT  A → Y
        "concepts": ["반전(NOT)"],
        "hint": "입력을 반전한 값이 출력되는지 확인해 보세요.",
    },
    9: {  # AND  A,B → Y
        "concepts": ["기본 AND"],
        "hint": "두 입력이 모두 1일 때만 1이 나오는지 확인해 보세요.",
    },
    10: {  # XOR  A,B → Y
        "concepts": ["배타적 논리합(XOR)"],
        "hint": "두 입력이 서로 다를 때만 1이 나오는지 확인해 보세요.",
    },
    11: {  # leap year  BCD → L
        "concepts": ["BCD 입력 처리", "4의 배수 규칙", "윤년 판정"],
        "hint": "연도가 4의 배수일 때 윤년으로 판정되는지 확인해 보세요.",
    },
    # ── Project 1 ────────────────────────────────────────────────
    1: {  # half adder  P,Q → S,C_out
        "concepts": ["합(Sum)", "받아올림(Carry)"],
        "hint": "받아올림(C_out) 출력을 다시 확인해 보세요.",
    },
    2: {  # full adder  P,Q,C_in → S,C_out
        "concepts": ["합(Sum)", "받아올림(Carry)", "올림 입력 반영(C_in)"],
        "hint": "올림 입력(C_in)이 합과 받아올림에 모두 반영됐는지 확인해 보세요.",
    },
    3: {  # 3-bit adder  X,Y → S3..S0
        "concepts": ["하위 비트 합", "자리올림 전파", "최상위 자리올림(S3)"],
        "hint": "자리올림이 다음 비트로 올바르게 전파되는지 확인해 보세요.",
    },
    12: {  # complement  S → R
        "concepts": ["7의 보수 계산", "0 하한 처리"],
        "hint": "S가 7 이상일 때 결과가 0으로 고정되는지 확인해 보세요.",
    },
    13: {  # divide by 3 (ceil)  R → T
        "concepts": ["3으로 나눈 몫", "나머지 올림(⌈⌉)"],
        "hint": "나머지가 남을 때 몫이 올림되는지 확인해 보세요.",
    },
    16: {  # P1 full wiring  X,Y → T1,T0
        "concepts": ["덧셈 단계", "보수 단계", "나눗셈 단계", "단계 간 연결"],
        "hint": "각 단계의 출력이 다음 단계의 입력으로 올바르게 연결됐는지 확인해 보세요.",
    },
    # ── Project 2 ────────────────────────────────────────────────
    4: {  # 2-bit comparator  A,B → G,L,E
        "concepts": ["크다(G)", "작다(L)", "같다(E)"],
        "hint": "세 출력(G/L/E) 중 정확히 하나만 1이 되는지 확인해 보세요.",
    },
    15: {  # 2,3-bit comparator  A,B → G,L,E
        "concepts": ["자리 맞춤(2→3비트)", "크다/작다/같다 판정"],
        "hint": "2비트 값을 3비트로 맞춰 비교했는지 확인해 보세요.",
    },
    14: {  # shelter assignment  G,L,E,C → Y
        "concepts": ["비교 결과 분기", "정원 기준(C≥4)"],
        "hint": "비교 결과와 정원 조건이 대피소 배정에 함께 반영됐는지 확인해 보세요.",
    },
    17: {  # 7-segment  Y → a..g
        "concepts": ["글자 'a' 패턴", "글자 'b' 패턴"],
        "hint": "각 세그먼트(a~g)가 글자 모양에 맞게 켜지는지 확인해 보세요.",
    },
    18: {  # P2 full wiring  A,B,C → a..g
        "concepts": ["비교 단계", "대피소 배정", "7세그먼트 출력", "단계 간 연결"],
        "hint": "비교부터 7세그먼트 출력까지 전 단계가 연결됐는지 확인해 보세요.",
    },
}

DEFAULT_CONCEPT_INFO = {
    "concepts": ["회로 동작"],
    "hint": "회로의 출력이 문제 조건과 일치하는지 다시 확인해 보세요.",
}


def concept_info(challenge_id: int) -> dict:
    """Return {'concepts': [...], 'hint': '...'} for a challenge, or a safe
    generic default for any id without an authored entry."""
    return CHALLENGE_CONCEPTS.get(challenge_id, DEFAULT_CONCEPT_INFO)
