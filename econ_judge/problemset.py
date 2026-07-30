"""Problem-set constants derived from the final contest HWP."""


TRUTH_TABLE_CHALLENGE_ID = 1

# These are the filenames that the final HWP says are already present on each
# team's contest notebook. They are reference names only: the final source
# files stay off the web server because several contain completed circuits.
HWP_STARTER_FILES = {
    2: 'N조_1라운드/2번_진리표를통해회로만들어보기.dig',
    3: 'N조_1라운드/3번_입력이3개인AND게이트.dig',
    4: 'N조_1라운드/4번_2대1멀티플렉서(MUX).dig',
    5: 'N조_1라운드/5-1번_NAND게이트로NOT게이트만들기.dig',
    6: 'N조_1라운드/5-2번_NAND게이트로OR게이트만들기.dig',
    7: 'N조_1라운드/6-1번_21세기윤년판독기만들기.dig',
    8: 'N조_1라운드/6-2번_A+B+C를집합의연산으로나타내기.dig',
    9: 'N조_2라운드/1-1번_반가산기(Half Adder)만들기.dig',
    10: 'N조_2라운드/1-2번_전가산기(Full Adder)만들기.dig',
    11: 'N조_2라운드/1-3번_3비트덧셈연산기만들기.dig',
    12: 'N조_2라운드/2-1번_주어진이진수가1이상인지판단하기.dig',
    13: 'N조_2라운드/2-2번_1이상인이진수가2개이상인지판단하기.dig',
    14: 'N조_2라운드/3-1번_X^2+Y^2+Z가14이상인지판단하기.dig',
    15: 'N조_2라운드/3-2번_7-segment출력기.dig',
}

TRUTH_TABLE_ROWS = (
    (0, 0, 0),
    (0, 0, 1),
    (0, 1, 0),
    (0, 1, 1),
    (1, 0, 0),
    (1, 0, 1),
    (1, 1, 0),
    (1, 1, 1),
)
# The HWP diagram is Y = (A OR B) AND C.
TRUTH_TABLE_EXPECTED = tuple((a | b) & c for a, b, c in TRUTH_TABLE_ROWS)


def normalize_truth_table_answers(answers):
    if not isinstance(answers, list) or len(answers) != len(TRUTH_TABLE_ROWS):
        return None
    if any(
        isinstance(answer, bool)
        or not isinstance(answer, int)
        or answer not in (0, 1)
        for answer in answers
    ):
        return None
    return tuple(answers)
