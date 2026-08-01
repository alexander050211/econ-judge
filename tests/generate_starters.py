"""Generate disconnected Digital starter files for challenges 2 through 15."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import importlib.util
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "econ_judge" / "assets" / "starters"

_problemset_spec = importlib.util.spec_from_file_location(
    "starter_problemset", REPO_ROOT / "econ_judge" / "problemset.py"
)
if _problemset_spec is None or _problemset_spec.loader is None:
    raise RuntimeError("Could not load the starter-file manifest")
_problemset = importlib.util.module_from_spec(_problemset_spec)
_problemset_spec.loader.exec_module(_problemset)
HWP_STARTER_FILES: dict[int, str] = _problemset.HWP_STARTER_FILES



@dataclass(frozen=True)
class Starter:
    filename: str
    inputs: tuple[tuple[str, int, int], ...]
    outputs: tuple[tuple[str, int, int], ...]
    seven_segment: tuple[int, int] | None = None


STARTERS = {
    2: Starter(
        "02_truth_table_xor.dig",
        (("A", 100, 200), ("B", 100, 240)),
        (("Y", 520, 220),),
    ),
    3: Starter(
        "03_three_input_and.dig",
        (("A", 100, 160), ("B", 100, 200), ("C", 100, 280)),
        (("Y", 700, 220),),
    ),
    4: Starter(
        "04_two_to_one_mux.dig",
        (("X0", 100, 160), ("S", 100, 240), ("X1", 100, 400)),
        (("Y", 820, 280),),
    ),
    5: Starter(
        "05_nand_not.dig",
        (("A", 100, 200),),
        (("Y", 540, 200),),
    ),
    6: Starter(
        "06_nand_or.dig",
        (("A", 100, 160), ("B", 100, 320)),
        (("Y", 780, 240),),
    ),
    7: Starter(
        "07_half_adder.dig",
        (("P", 100, 160), ("Q", 100, 200)),
        (("S", 560, 180), ("C_out", 560, 320)),
    ),
    8: Starter(
        "08_full_adder.dig",
        (("P", 100, 160), ("Q", 100, 200), ("C_in", 100, 360)),
        (("S", 900, 300), ("C_out", 900, 240)),
    ),
    9: Starter(
        "09_three_bit_adder.dig",
        (
            ("X2", 100, 80), ("Y2", 100, 120),
            ("X1", 100, 280), ("Y1", 100, 320),
            ("X0", 100, 480), ("Y0", 100, 520),
        ),
        (
            ("S3", 900, 180), ("S2", 900, 100),
            ("S1", 900, 300), ("S0", 900, 500),
        ),
    ),
    10: Starter(
        "10_leap_year.dig",
        (
            ("A3", 100, 100), ("A2", 100, 140),
            ("A1", 100, 180), ("A0", 100, 220),
            ("B3", 100, 320), ("B2", 100, 360),
            ("B1", 100, 400), ("B0", 100, 440),
        ),
        (("L", 840, 340),),
    ),
    11: Starter(
        "11_abc_identity.dig",
        (("A", 100, 200), ("B", 100, 280), ("C", 100, 360)),
        (("Y1", 900, 120), ("Y2", 900, 300), ("Y3", 900, 500)),
    ),
    12: Starter(
        "12_at_least_one.dig",
        (("X1", 100, 200), ("X0", 100, 240)),
        (("S0", 520, 220),),
    ),
    13: Starter(
        "13_flood_warning.dig",
        (
            ("X1", 100, 120), ("X0", 100, 160),
            ("Y1", 100, 280), ("Y0", 100, 320),
            ("Z1", 100, 440), ("Z0", 100, 480),
        ),
        (("S0", 1100, 320),),
    ),
    14: Starter(
        "14_flood_risk.dig",
        (
            ("X1", 80, 100), ("X0", 80, 180),
            ("Y1", 80, 260), ("Y0", 80, 340),
            ("Z1", 80, 420), ("Z0", 80, 500),
        ),
        (("S0", 1080, 280),),
    ),
    15: Starter(
        "15_seven_segment_yn.dig",
        (("Y", 100, 360),),
        (
            ("a", 1180, 100), ("b", 1180, 160),
            ("c", 1180, 220), ("d", 1180, 280),
            ("e", 1180, 380), ("f", 1180, 440),
            ("g", 1180, 500), ("dp", 1180, 560),
        ),
        seven_segment=(900, 260),
    ),
}

STARTER_SOURCE_IDS = {
    2: 2, 3: 3, 4: 4, 5: 5, 6: 6,
    7: 10, 8: 11,
    9: 7, 10: 8, 11: 9,
    12: 12, 13: 13, 14: 14, 15: 15,
}


def _visual_element(parent, name: str, x: int, y: int, label: str | None = None):
    visual = ET.SubElement(parent, "visualElement")
    ET.SubElement(visual, "elementName").text = name
    attributes = ET.SubElement(visual, "elementAttributes")
    if label is not None:
        entry = ET.SubElement(attributes, "entry")
        ET.SubElement(entry, "string").text = "Label"
        ET.SubElement(entry, "string").text = label
    ET.SubElement(visual, "pos", {"x": str(x), "y": str(y)})


def write_starter(starter: Starter, path: Path) -> None:
    root = ET.Element("circuit")
    ET.SubElement(root, "version").text = "2"
    ET.SubElement(root, "attributes")
    visual_elements = ET.SubElement(root, "visualElements")

    for label, x, y in starter.inputs:
        _visual_element(visual_elements, "In", x, y, label)
    if starter.seven_segment is not None:
        _visual_element(visual_elements, "Seven-Seg", *starter.seven_segment)
    for label, x, y in starter.outputs:
        _visual_element(visual_elements, "Out", x, y, label)

    ET.SubElement(root, "wires")
    ET.SubElement(root, "measurementOrdering")
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    expected = set(HWP_STARTER_FILES.values())
    for stale in OUT_DIR.rglob("*.dig"):
        if stale.relative_to(OUT_DIR).as_posix() not in expected:
            stale.unlink()
    for challenge_id in HWP_STARTER_FILES:
        starter = STARTERS[STARTER_SOURCE_IDS[challenge_id]]
        path = OUT_DIR / HWP_STARTER_FILES[challenge_id]
        write_starter(starter, path)
        print(f"challenge {challenge_id:>2}: {path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
