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
        # Label is intentionally vector-free: the per-test name is echoed to
        # students in the grader's `detail`, so embedding the input combo
        # (e.g. "(P=0,Q=1)") would leak the exact failing case and let a
        # student pinpoint-patch instead of reasoning about the circuit.
        label = f"{prefix} #{i+1:03d}"
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

def xor_gate(inp):
    a, b = inp
    return [a ^ b]


def three_input_and(inp):
    a, b, c = inp
    return [a & b & c]


def mux_2_to_1(inp):
    x0, x1, select = inp
    return [x1 if select else x0]


def not_gate(inp):
    (a,) = inp
    return [1 - a]


def or_gate(inp):
    a, b = inp
    return [a | b]


def half_adder(inp):
    p, q = inp
    return [p ^ q, p & q]


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


def abc_identity(inp):
    """Return the three threshold terms whose arithmetic sum is A+B+C."""
    a, b, c = inp
    y1 = a | b | c
    y2 = (a & b) | (b & c) | (c & a)
    y3 = a & b & c
    return [y1, y2, y3]


def at_least_one(inp):
    x1, x0 = inp
    return [int(bool(x1 or x0))]


def flood_warning(inp):
    x1, x0, y1, y0, z1, z0 = inp
    positive = int(bool(x1 or x0)) + int(bool(y1 or y0)) + int(bool(z1 or z0))
    return [int(positive >= 2)]


def flood_risk(inp):
    x1, x0, y1, y0, z1, z0 = inp
    x = (x1 << 1) | x0
    y = (y1 << 1) | y0
    z = (z1 << 1) | z0
    return [int(x * x + y * y + z >= 14)]


# Standard segment order: a=top, then clockwise through f, with g=middle.
# The patterns are copied from the two glyph illustrations in the HWP.
_GLYPH_Y = [0, 1, 1, 1, 0, 1, 1]
_GLYPH_N = [1, 1, 1, 0, 1, 1, 0]


def seven_segment_yn(inp):
    (y,) = inp
    return list(_GLYPH_Y if y else _GLYPH_N)


SPECS = {
    # Challenge 1 is graded by the one-attempt truth-table endpoint.
    2: dict(prefix="XOR", in_pins=["A", "B"], out_pins=["Y"], compute=xor_gate),
    3: dict(prefix="AND3", in_pins=["A", "B", "C"], out_pins=["Y"],
            compute=three_input_and),
    4: dict(prefix="MUX", in_pins=["X0", "X1", "S"], out_pins=["Y"],
            compute=mux_2_to_1),
    5: dict(prefix="NAND_NOT", in_pins=["A"], out_pins=["Y"], compute=not_gate),
    6: dict(prefix="NAND_OR", in_pins=["A", "B"], out_pins=["Y"], compute=or_gate),
    7: dict(prefix="LEAP",
             in_pins=["A3", "A2", "A1", "A0", "B3", "B2", "B1", "B0"],
             out_pins=["L"], compute=leap_year),
    8: dict(prefix="ABC", in_pins=["A", "B", "C"],
             out_pins=["Y1", "Y2", "Y3"], compute=abc_identity),
    9: dict(prefix="HA", in_pins=["P", "Q"], out_pins=["S", "C_out"],
            compute=half_adder),
    10: dict(prefix="FA", in_pins=["P", "Q", "C_in"], out_pins=["S", "C_out"],
             compute=full_adder),
    11: dict(prefix="ADD3", in_pins=["X2", "X1", "X0", "Y2", "Y1", "Y0"],
             out_pins=["S3", "S2", "S1", "S0"], compute=three_bit_adder),
    12: dict(prefix="A1", in_pins=["X1", "X0"], out_pins=["S0"],
             compute=at_least_one),
    13: dict(prefix="A2",
             in_pins=["X1", "X0", "Y1", "Y0", "Z1", "Z0"],
             out_pins=["S0"], compute=flood_warning),
    14: dict(prefix="RISK",
             in_pins=["X1", "X0", "Y1", "Y0", "Z1", "Z0"],
             out_pins=["S0"], compute=flood_risk),
    15: dict(prefix="SEG", in_pins=["Y"],
             out_pins=["a", "b", "c", "d", "e", "f", "g"],
             compute=seven_segment_yn),
}


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    active = {f"{challenge_id}.dig" for challenge_id in SPECS}
    for stale in OUT_DIR.glob("*.dig"):
        if stale.stem.isdigit() and stale.name not in active:
            stale.unlink()
    for challenge_id, spec in SPECS.items():
        path = OUT_DIR / f"{challenge_id}.dig"
        n = write_split_dig(path, spec["in_pins"], spec["out_pins"],
                            spec["compute"], spec["prefix"])
        print(f"  chal {challenge_id}: {n:>3} testcases → {path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
