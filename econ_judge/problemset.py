"""Small problem-set constants shared by routes and graders."""


TRUTH_TABLE_CHALLENGE_ID = 1
STARTER_FILES = {
    2: "02_truth_table_xor.dig",
    3: "03_three_input_and.dig",
    4: "04_two_to_one_mux.dig",
    5: "05_nand_not.dig",
    6: "06_nand_or.dig",
    7: "07_half_adder.dig",
    8: "08_full_adder.dig",
    9: "09_three_bit_adder.dig",
    10: "10_leap_year.dig",
    11: "11_abc_identity.dig",
    12: "12_at_least_one.dig",
    13: "13_flood_warning.dig",
    14: "14_flood_risk.dig",
    15: "15_seven_segment_yn.dig",
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
