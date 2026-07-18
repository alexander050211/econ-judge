"""Small problem-set constants shared by routes and graders."""


TRUTH_TABLE_CHALLENGE_ID = 1
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
