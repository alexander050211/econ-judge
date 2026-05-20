"""Generate per-row Digital `Testcase` `.dig` files from truth-table specs.

Each spec describes one challenge: input pin names, output pin names, and a
function `compute(inputs) -> outputs`. The script enumerates every input combo,
builds a `<Testcase>` element per row (so Digital CLI reports N pass/fail
lines), and writes the result to `secret_tests/<challenge_id>.dig`.

Usage: python tests/generate_secret_tests.py
"""

from __future__ import annotations

import itertools
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "secret_tests"


def write_split_dig(path: Path, in_pins: list[str], out_pins: list[str],
                    compute, prefix: str) -> int:
    """Enumerate 2^len(in_pins) input combos and write one Testcase block per row.

    If compute() returns None for a combo, that row is skipped — useful for
    inputs with validity constraints (e.g., BCD digits must be 0-9, or comparator
    G/L/E outputs must be one-hot).
    """
    cols = in_pins + out_pins
    blocks: list[str] = []
    n_inputs = len(in_pins)
    i = 0
    for combo in itertools.product([0, 1], repeat=n_inputs):
        in_vals = list(combo)
        out = compute(in_vals)
        if out is None:
            continue
        out_vals = list(out)
        assert len(out_vals) == len(out_pins), f"compute returned {len(out_vals)} outputs, expected {len(out_pins)}"
        row = " ".join(str(v) for v in in_vals + out_vals)
        in_summary = ",".join(f"{p}={v}" for p, v in zip(in_pins, in_vals))
        label = f"{prefix} #{i+1:03d} ({in_summary})"
        data = " ".join(cols) + "\n" + row + "\n"
        blocks.append(
            "    <visualElement>\n"
            "      <elementName>Testcase</elementName>\n"
            "      <elementAttributes>\n"
            "        <entry>\n"
            "          <string>Label</string>\n"
            f"          <string>{label}</string>\n"
            "        </entry>\n"
            "        <entry>\n"
            "          <string>Testdata</string>\n"
            "          <testData>\n"
            f"            <dataString>{data}</dataString>\n"
            "          </testData>\n"
            "        </entry>\n"
            "      </elementAttributes>\n"
            f'      <pos x="100" y="{100 + i*60}"/>\n'
            "    </visualElement>"
        )
        i += 1
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<circuit>\n"
        "  <version>2</version>\n"
        "  <attributes/>\n"
        "  <visualElements>\n"
        + "\n".join(blocks) + "\n"
        "  </visualElements>\n"
        "  <wires/>\n"
        "</circuit>\n"
    )
    path.write_text(xml, encoding="utf-8")
    return len(blocks)


# ---------------------------------------------------------------------------
# Challenge specs
# ---------------------------------------------------------------------------

def half_adder(inp):
    p, q = inp
    s = p ^ q
    cout = p & q
    return [s, cout]


def full_adder(inp):
    p, q, ci = inp
    total = p + q + ci
    return [total & 1, (total >> 1) & 1]


def three_bit_adder(inp):
    x2, x1, x0, y2, y1, y0 = inp
    x = (x2 << 2) | (x1 << 1) | x0
    y = (y2 << 2) | (y1 << 1) | y0
    s = x + y
    return [(s >> 3) & 1, (s >> 2) & 1, (s >> 1) & 1, s & 1]


def two_bit_comparator(inp):
    a1, a0, b1, b0 = inp
    a = (a1 << 1) | a0
    b = (b1 << 1) | b0
    return [int(a > b), int(a < b), int(a == b)]


def ex1_xnor(inp):
    a, b = inp
    return [int(a == b)]


def ex2_3input_and(inp):
    a, b, c = inp
    return [a & b & c]


def ex3_2to1_mux(inp):
    x0, x1, s = inp
    return [x1 if s else x0]


def t1_not(inp):
    (a,) = inp
    return [1 - a]


def t1_and(inp):
    a, b = inp
    return [a & b]


def t1_xor(inp):
    a, b = inp
    return [a ^ b]


def leap_year(inp):
    """L=1 iff year (2000 + 10*tens + ones) is a leap year. Skips invalid BCD
    digits (tens or ones > 9). For 2000-2099, leap iff year % 4 == 0."""
    a3, a2, a1, a0, b3, b2, b1, b0 = inp
    tens = (a3 << 3) | (a2 << 2) | (a1 << 1) | a0
    ones = (b3 << 3) | (b2 << 2) | (b1 << 1) | b0
    if tens > 9 or ones > 9:
        return None
    year = 2000 + 10 * tens + ones
    is_leap = (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)
    return [int(is_leap)]


def complement(inp):
    """R = max(7 - S, 0), output as 3-bit (S=0 → R=7, S≥7 → R=0)."""
    s3, s2, s1, s0 = inp
    s = (s3 << 3) | (s2 << 2) | (s1 << 1) | s0
    r = max(7 - s, 0)
    return [(r >> 2) & 1, (r >> 1) & 1, r & 1]


def divide_by_3_ceil(inp):
    """T = ⌈R/3⌉ where R ∈ [0,7] and T ∈ [0,3]."""
    r2, r1, r0 = inp
    r = (r2 << 2) | (r1 << 1) | r0
    t = -(-r // 3)
    return [(t >> 1) & 1, t & 1]


def shelter_assignment(inp):
    """Y=0 → shelter A, Y=1 → shelter B. Skip rows where G/L/E not one-hot
    (illegal comparator state)."""
    g, l, e, c2, c1, c0 = inp
    if g + l + e != 1:
        return None
    c = (c2 << 2) | (c1 << 1) | c0
    if g:
        y = 0
    elif l:
        y = 1
    else:
        y = 1 if c >= 4 else 0
    return [y]


def p2_23bit_comparator(inp):
    """A (2-bit, 0..3) zero-padded to 3 bits, compared with B (3-bit, 0..7)."""
    a1, a0, b2, b1, b0 = inp
    a = (a1 << 1) | a0
    b = (b2 << 2) | (b1 << 1) | b0
    return [int(a > b), int(a < b), int(a == b)]


def p1_full_wiring(inp):
    """Full P1 pipeline: X+Y → S, R = max(7-S, 0), T = ⌈R/3⌉. Output T1, T0."""
    x2, x1, x0, y2, y1, y0 = inp
    x = (x2 << 2) | (x1 << 1) | x0
    y = (y2 << 2) | (y1 << 1) | y0
    s = x + y
    r = max(7 - s, 0)
    t = -(-r // 3)
    return [(t >> 1) & 1, t & 1]


# 7-segment glyph patterns for the P2 'a'/'b' display. Standard Wikipedia
# segment labels (a=top, b=top-right, c=bottom-right, d=bottom, e=bottom-left,
# f=top-left, g=middle). Order in output: a, b, c, d, e, f, g.
# 'a' (Y=0): a b c d e g lit (uppercase-A shape, closed bottom).
# 'b' (Y=1): c d e f g lit (lowercase b, no top, no top-right).
_GLYPH_A = [1, 1, 1, 1, 1, 0, 1]
_GLYPH_B = [0, 0, 1, 1, 1, 1, 1]


def p2_c_seven_seg(inp):
    """Y → 7 segments (a, b, c, d, e, f, g) per the camp glyph definition."""
    (y,) = inp
    return _GLYPH_A if y == 0 else _GLYPH_B


def p2_full_wiring(inp):
    """Full P2 pipeline: A vs B → G/L/E → shelter Y → 7-seg glyph."""
    a1, a0, b2, b1, b0, c2, c1, c0 = inp
    a = (a1 << 1) | a0
    b = (b2 << 2) | (b1 << 1) | b0
    c = (c2 << 2) | (c1 << 1) | c0
    if a > b:
        y = 0
    elif a < b:
        y = 1
    else:
        y = 1 if c >= 4 else 0
    return _GLYPH_A if y == 0 else _GLYPH_B


SPECS = {
    1: dict(prefix="HA", in_pins=["P", "Q"], out_pins=["S", "C_out"],
            compute=half_adder),
    2: dict(prefix="FA", in_pins=["P", "Q", "C_in"], out_pins=["S", "C_out"],
            compute=full_adder),
    3: dict(prefix="A3", in_pins=["X2", "X1", "X0", "Y2", "Y1", "Y0"],
            out_pins=["S3", "S2", "S1", "S0"], compute=three_bit_adder),
    4: dict(prefix="CMP", in_pins=["A1", "A0", "B1", "B0"],
            out_pins=["G", "L", "E"], compute=two_bit_comparator),
    5: dict(prefix="XNOR", in_pins=["A", "B"], out_pins=["Y"],
            compute=ex1_xnor),
    6: dict(prefix="AND3", in_pins=["A", "B", "C"], out_pins=["Y"],
            compute=ex2_3input_and),
    7: dict(prefix="MUX", in_pins=["X0", "X1", "S"], out_pins=["Y"],
            compute=ex3_2to1_mux),
    8: dict(prefix="NOT", in_pins=["A"], out_pins=["Y"], compute=t1_not),
    9: dict(prefix="ANDN", in_pins=["A", "B"], out_pins=["Y"], compute=t1_and),
    10: dict(prefix="XORN", in_pins=["A", "B"], out_pins=["Y"], compute=t1_xor),
    11: dict(prefix="LEAP",
             in_pins=["A3", "A2", "A1", "A0", "B3", "B2", "B1", "B0"],
             out_pins=["L"], compute=leap_year),
    12: dict(prefix="COMP",
             in_pins=["S3", "S2", "S1", "S0"],
             out_pins=["R2", "R1", "R0"], compute=complement),
    13: dict(prefix="DIV3",
             in_pins=["R2", "R1", "R0"], out_pins=["T1", "T0"],
             compute=divide_by_3_ceil),
    14: dict(prefix="SHEL",
             in_pins=["G", "L", "E", "C2", "C1", "C0"],
             out_pins=["Y"], compute=shelter_assignment),
    15: dict(prefix="CMP23",
             in_pins=["A1", "A0", "B2", "B1", "B0"],
             out_pins=["G", "L", "E"], compute=p2_23bit_comparator),
    16: dict(prefix="WIRE1",
             in_pins=["X2", "X1", "X0", "Y2", "Y1", "Y0"],
             out_pins=["T1", "T0"], compute=p1_full_wiring),
    17: dict(prefix="SEG",
             in_pins=["Y"],
             out_pins=["a", "b", "c", "d", "e", "f", "g"],
             compute=p2_c_seven_seg),
    18: dict(prefix="WIRE2",
             in_pins=["A1", "A0", "B2", "B1", "B0", "C2", "C1", "C0"],
             out_pins=["a", "b", "c", "d", "e", "f", "g"],
             compute=p2_full_wiring),
}


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    for challenge_id, spec in SPECS.items():
        path = OUT_DIR / f"{challenge_id}.dig"
        n = write_split_dig(path, spec["in_pins"], spec["out_pins"],
                            spec["compute"], spec["prefix"])
        print(f"  chal {challenge_id}: {n:>3} testcases → {path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
