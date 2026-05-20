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
    """Enumerate 2^len(in_pins) input combos and write one Testcase block per row."""
    cols = in_pins + out_pins
    blocks: list[str] = []
    n_inputs = len(in_pins)
    for i, combo in enumerate(itertools.product([0, 1], repeat=n_inputs)):
        in_vals = list(combo)
        out_vals = list(compute(in_vals))
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


SPECS = {
    1: dict(prefix="HA", in_pins=["P", "Q"], out_pins=["S", "C_out"],
            compute=half_adder),
    2: dict(prefix="FA", in_pins=["P", "Q", "C_in"], out_pins=["S", "C_out"],
            compute=full_adder),
    3: dict(prefix="A3", in_pins=["X2", "X1", "X0", "Y2", "Y1", "Y0"],
            out_pins=["S3", "S2", "S1", "S0"], compute=three_bit_adder),
    4: dict(prefix="CMP", in_pins=["A1", "A0", "B1", "B0"],
            out_pins=["G", "L", "E"], compute=two_bit_comparator),
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
