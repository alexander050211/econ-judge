"""Generate readable reference solutions for the 2026 summer challenges.

Selected later reference solutions deliberately import earlier solutions as
Digital subcircuits to demonstrate optional component reuse. Contestant
solutions may instead use ordinary gates.

Usage: python tests/generate_solutions.py
"""

from __future__ import annotations

import shutil
import importlib.util
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "solutions" / "2026-summer"
CANONICAL_DIR = REPO_ROOT / "canonical"

_problemset_spec = importlib.util.spec_from_file_location(
    "solution_problemset", REPO_ROOT / "econ_judge" / "problemset.py"
)
if _problemset_spec is None or _problemset_spec.loader is None:
    raise RuntimeError("Could not load the starter-file manifest")
_problemset = importlib.util.module_from_spec(_problemset_spec)
_problemset_spec.loader.exec_module(_problemset)
HWP_STARTER_FILES: dict[int, str] = _problemset.HWP_STARTER_FILES



@dataclass(frozen=True)
class Point:
    x: int
    y: int


@dataclass(frozen=True)
class Component:
    name: str
    x: int
    y: int
    label: str | None = None
    input_count: int = 0
    output_count: int = 0


class Circuit:
    """Small Digital XML builder with explicit schematic coordinates."""

    OUTPUT_DX = {
        "Not": 40,
        "And": 80,
        "Or": 80,
        "XOr": 80,
        "NAnd": 100,
        "NOr": 100,
        "XNOr": 100,
    }
    TWO_INPUT_GATES = {"And", "Or", "XOr", "NAnd", "NOr", "XNOr"}

    def __init__(self) -> None:
        self.components: dict[str, Component] = {}
        self.order: list[str] = []
        self.segments: list[tuple[Point, Point]] = []
        self._segment_keys: set[tuple[int, int, int, int]] = set()

    def add_input(self, key: str, label: str, x: int, y: int) -> None:
        self._add(key, Component("In", x, y, label))

    def add_output(self, key: str, label: str, x: int, y: int) -> None:
        self._add(key, Component("Out", x, y, label))

    def add_gate(
        self, key: str, name: str, x: int, y: int, inputs: int = 2
    ) -> None:
        if name not in self.OUTPUT_DX:
            raise ValueError(f"unsupported gate: {name}")
        if name == "Not" and inputs != 1:
            inputs = 1
        elif name != "Not" and inputs < 2:
            raise ValueError(f"gate {name} needs at least two inputs")
        self._add(key, Component(name, x, y, input_count=inputs))

    def add_subcircuit(
        self,
        key: str,
        filename: str,
        x: int,
        y: int,
        input_count: int,
        output_count: int,
    ) -> None:
        self._add(
            key,
            Component(
                filename,
                x,
                y,
                input_count=input_count,
                output_count=output_count,
            ),
        )

    def add_seven_segment(self, key: str, x: int, y: int) -> None:
        self._add(key, Component("Seven-Seg", x, y, input_count=8))

    def _add(self, key: str, component: Component) -> None:
        if key in self.components:
            raise ValueError(f"duplicate component key: {key}")
        self.components[key] = component
        self.order.append(key)

    def out_pin(self, key: str, index: int = 0) -> Point:
        component = self.components[key]
        if component.name == "In":
            if index != 0:
                raise ValueError(f"component {key} has only one output")
            return Point(component.x, component.y)
        if component.name == "Not":
            return Point(component.x + 40, component.y)
        if component.name in self.TWO_INPUT_GATES:
            return Point(
                component.x + self.OUTPUT_DX[component.name],
                component.y + 20 * (component.input_count // 2),
            )
        if component.output_count:
            if not 0 <= index < component.output_count:
                raise ValueError(f"component {key} has {component.output_count} outputs")
            return Point(component.x + 60, component.y + 20 * index)
        raise ValueError(f"component {key} has no output pin")

    def in_pin(self, key: str, index: int = 0) -> Point:
        component = self.components[key]
        if component.name in {"Out", "Not"}:
            if index != 0:
                raise ValueError(f"component {key} has only one input")
            return Point(component.x, component.y)
        if component.name in self.TWO_INPUT_GATES:
            if not 0 <= index < component.input_count:
                raise ValueError(f"component {key} has {component.input_count} inputs")
            offset = 20 * index
            if component.input_count % 2 == 0 and index >= component.input_count // 2:
                offset += 20
            return Point(component.x, component.y + offset)
        if component.input_count and component.name != "Seven-Seg":
            if not 0 <= index < component.input_count:
                raise ValueError(f"component {key} has {component.input_count} inputs")
            return Point(component.x, component.y + 20 * index)
        raise ValueError(f"component {key} has no input pin")

    def seven_segment_pin(self, key: str, label: str) -> Point:
        component = self.components[key]
        if component.name != "Seven-Seg":
            raise ValueError(f"component {key} is not a Seven-Seg")
        offsets = {
            "a": (0, 0), "b": (20, 0), "c": (40, 0), "d": (60, 0),
            "e": (0, 140), "f": (20, 140), "g": (40, 140), "dp": (60, 140),
        }
        if label not in offsets:
            raise ValueError(f"unknown Seven-Seg pin: {label}")
        dx, dy = offsets[label]
        return Point(component.x + dx, component.y + dy)

    def connect(
        self,
        source: Point,
        destination: Point,
        *via: tuple[int, int] | Point,
    ) -> None:
        points = [source]
        points.extend(
            point if isinstance(point, Point) else Point(*point) for point in via
        )
        points.append(destination)
        if len(points) == 2 and source.x != destination.x and source.y != destination.y:
            points.insert(1, Point(destination.x, source.y))
        for first, second in zip(points, points[1:]):
            self.add_segment(first, second)

    def add_segment(self, first: Point, second: Point) -> None:
        if first == second:
            return
        if first.x != second.x and first.y != second.y:
            raise ValueError(f"diagonal wire: {first} -> {second}")
        ordered = sorted((first, second), key=lambda point: (point.x, point.y))
        key = (ordered[0].x, ordered[0].y, ordered[1].x, ordered[1].y)
        if key in self._segment_keys:
            return
        self._segment_keys.add(key)
        self.segments.append((first, second))

    def vertical_bus(
        self,
        source: Point,
        x: int,
        y_min: int,
        y_max: int,
        destinations: list[Point],
    ) -> None:
        if not y_min <= source.y <= y_max:
            raise ValueError(f"source {source} is outside bus range")
        self.connect(source, Point(x, source.y))
        # Digital only creates a junction where wire segments share endpoints.
        # Split the trunk at every branch instead of drawing one long segment.
        junctions = sorted({y_min, y_max, source.y, *(point.y for point in destinations)})
        for first_y, second_y in zip(junctions, junctions[1:]):
            self.add_segment(Point(x, first_y), Point(x, second_y))
        for destination in destinations:
            if not y_min <= destination.y <= y_max:
                raise ValueError(f"destination {destination} is outside bus range")
            self.connect(Point(x, destination.y), destination)

    def write(self, path: Path) -> None:
        root = ET.Element("circuit")
        ET.SubElement(root, "version").text = "2"
        ET.SubElement(root, "attributes")
        visual_elements = ET.SubElement(root, "visualElements")
        for key in self.order:
            component = self.components[key]
            visual = ET.SubElement(visual_elements, "visualElement")
            ET.SubElement(visual, "elementName").text = component.name
            attributes = ET.SubElement(visual, "elementAttributes")
            if component.label is not None:
                entry = ET.SubElement(attributes, "entry")
                ET.SubElement(entry, "string").text = "Label"
                ET.SubElement(entry, "string").text = component.label
            elif component.name in self.TWO_INPUT_GATES:
                if component.input_count != 2:
                    entry = ET.SubElement(attributes, "entry")
                    ET.SubElement(entry, "string").text = "Inputs"
                    ET.SubElement(entry, "int").text = str(component.input_count)
                entry = ET.SubElement(attributes, "entry")
                ET.SubElement(entry, "string").text = "wideShape"
                ET.SubElement(entry, "boolean").text = "true"
            ET.SubElement(
                visual,
                "pos",
                {"x": str(component.x), "y": str(component.y)},
            )

        wires = ET.SubElement(root, "wires")
        for first, second in self.segments:
            wire = ET.SubElement(wires, "wire")
            ET.SubElement(wire, "p1", {"x": str(first.x), "y": str(first.y)})
            ET.SubElement(wire, "p2", {"x": str(second.x), "y": str(second.y)})
        ET.SubElement(root, "measurementOrdering")

        tree = ET.ElementTree(root)
        ET.indent(tree, space="  ")
        path.parent.mkdir(parents=True, exist_ok=True)
        tree.write(path, encoding="utf-8", xml_declaration=True)


def solution_02() -> Circuit:
    c = Circuit()
    c.add_input("A", "A", 100, 200)
    c.add_input("B", "B", 100, 240)
    c.add_gate("xor", "XOr", 300, 200)
    c.add_output("Y", "Y", 520, 220)
    c.connect(c.out_pin("A"), c.in_pin("xor", 0))
    c.connect(c.out_pin("B"), c.in_pin("xor", 1))
    c.connect(c.out_pin("xor"), c.in_pin("Y"))
    return c


def solution_03() -> Circuit:
    c = Circuit()
    c.add_input("A", "A", 100, 160)
    c.add_input("B", "B", 100, 200)
    c.add_input("C", "C", 100, 280)
    c.add_gate("and_ab", "And", 280, 160)
    c.add_gate("and_abc", "And", 480, 200)
    c.add_output("Y", "Y", 700, 220)
    c.connect(c.out_pin("A"), c.in_pin("and_ab", 0))
    c.connect(c.out_pin("B"), c.in_pin("and_ab", 1))
    c.connect(c.out_pin("and_ab"), c.in_pin("and_abc", 0), (400, 180), (400, 200))
    c.connect(c.out_pin("C"), c.in_pin("and_abc", 1), (420, 280), (420, 240))
    c.connect(c.out_pin("and_abc"), c.in_pin("Y"))
    return c


def solution_04() -> Circuit:
    c = Circuit()
    c.add_input("X0", "X0", 100, 160)
    c.add_input("S", "S", 100, 240)
    c.add_input("X1", "X1", 100, 400)
    c.add_gate("not_s", "Not", 240, 240)
    c.add_gate("and_x0", "And", 380, 160)
    c.add_gate("and_x1", "And", 380, 360)
    c.add_gate("or_result", "Or", 600, 260)
    c.add_output("Y", "Y", 820, 280)
    c.connect(c.out_pin("S"), c.in_pin("not_s"))
    c.connect(c.out_pin("X0"), c.in_pin("and_x0", 0))
    c.connect(c.out_pin("not_s"), c.in_pin("and_x0", 1), (320, 240), (320, 200))
    c.connect(c.out_pin("S"), c.in_pin("and_x1", 0), (180, 240), (180, 360))
    c.connect(c.out_pin("X1"), c.in_pin("and_x1", 1))
    c.connect(c.out_pin("and_x0"), c.in_pin("or_result", 0), (520, 180), (520, 260))
    c.connect(c.out_pin("and_x1"), c.in_pin("or_result", 1), (540, 380), (540, 300))
    c.connect(c.out_pin("or_result"), c.in_pin("Y"))
    return c


def solution_05() -> Circuit:
    c = Circuit()
    c.add_input("A", "A", 100, 200)
    c.add_gate("nand", "NAnd", 300, 180)
    c.add_output("Y", "Y", 540, 200)
    c.vertical_bus(
        c.out_pin("A"),
        220,
        180,
        220,
        [c.in_pin("nand", 0), c.in_pin("nand", 1)],
    )
    c.connect(c.out_pin("nand"), c.in_pin("Y"))
    return c


def solution_06() -> Circuit:
    c = Circuit()
    c.add_input("A", "A", 100, 160)
    c.add_input("B", "B", 100, 320)
    c.add_gate("nand_a", "NAnd", 300, 140)
    c.add_gate("nand_b", "NAnd", 300, 300)
    c.add_gate("nand_or", "NAnd", 540, 220)
    c.add_output("Y", "Y", 780, 240)
    for source, gate, center in (("A", "nand_a", 160), ("B", "nand_b", 320)):
        c.vertical_bus(
            c.out_pin(source),
            220,
            center - 20,
            center + 20,
            [c.in_pin(gate, 0), c.in_pin(gate, 1)],
        )
    c.connect(c.out_pin("nand_a"), c.in_pin("nand_or", 0), (460, 160), (460, 220))
    c.connect(c.out_pin("nand_b"), c.in_pin("nand_or", 1), (480, 320), (480, 260))
    c.connect(c.out_pin("nand_or"), c.in_pin("Y"))
    return c


def solution_07() -> Circuit:
    c = Circuit()
    c.add_input("P", "P", 100, 160)
    c.add_input("Q", "Q", 100, 200)
    c.add_gate("sum", "XOr", 300, 160)
    c.add_gate("carry", "And", 300, 300)
    c.add_output("S", "S", 560, 180)
    c.add_output("C_out", "C_out", 560, 320)
    c.connect(c.out_pin("P"), c.in_pin("sum", 0))
    c.connect(c.out_pin("Q"), c.in_pin("sum", 1))
    c.connect(c.out_pin("P"), c.in_pin("carry", 0), (180, 160), (180, 300))
    c.connect(c.out_pin("Q"), c.in_pin("carry", 1), (220, 200), (220, 340))
    c.connect(c.out_pin("sum"), c.in_pin("S"))
    c.connect(c.out_pin("carry"), c.in_pin("C_out"))
    return c


def solution_08() -> Circuit:
    c = Circuit()
    c.add_input("P", "P", 100, 160)
    c.add_input("Q", "Q", 100, 200)
    c.add_input("C_in", "C_in", 100, 360)
    c.add_subcircuit("half_1", Path(HWP_STARTER_FILES[9]).name, 300, 160, 2, 2)
    c.add_subcircuit("half_2", Path(HWP_STARTER_FILES[9]).name, 520, 300, 2, 2)
    c.add_gate("carry", "Or", 700, 220)
    c.add_output("S", "S", 900, 300)
    c.add_output("C_out", "C_out", 900, 240)

    c.connect(c.out_pin("P"), c.in_pin("half_1", 0))
    c.connect(c.out_pin("Q"), c.in_pin("half_1", 1))
    c.connect(c.out_pin("half_1", 0), c.in_pin("half_2", 0), (440, 160), (440, 300))
    c.connect(c.out_pin("C_in"), c.in_pin("half_2", 1), (480, 360), (480, 320))
    c.connect(c.out_pin("half_1", 1), c.in_pin("carry", 0), (620, 180), (620, 220))
    c.connect(c.out_pin("half_2", 1), c.in_pin("carry", 1), (640, 320), (640, 260))
    c.connect(c.out_pin("half_2", 0), c.in_pin("S"))
    c.connect(c.out_pin("carry"), c.in_pin("C_out"))
    return c


def _flat_solution_09() -> Circuit:
    c = Circuit()
    # Bit 0 (least significant)
    c.add_input("X0", "X0", 100, 100)
    c.add_input("Y0", "Y0", 100, 140)
    c.add_gate("xor0", "XOr", 260, 100)
    c.add_gate("and0", "And", 260, 180)
    c.add_output("S0", "S0", 900, 120)
    c.connect(c.out_pin("X0"), c.in_pin("xor0", 0))
    c.connect(c.out_pin("Y0"), c.in_pin("xor0", 1))
    c.connect(c.out_pin("X0"), c.in_pin("and0", 0), (180, 100), (180, 180))
    c.connect(c.out_pin("Y0"), c.in_pin("and0", 1), (220, 140), (220, 220))
    c.connect(c.out_pin("xor0"), c.in_pin("S0"))

    # Bit 1
    c.add_input("X1", "X1", 100, 280)
    c.add_input("Y1", "Y1", 100, 320)
    c.add_gate("xor1", "XOr", 260, 280)
    c.add_gate("sum1", "XOr", 500, 300)
    c.add_gate("and1", "And", 260, 380)
    c.add_gate("and_c1", "And", 500, 440)
    c.add_gate("or_c1", "Or", 680, 380)
    c.add_output("S1", "S1", 900, 320)
    c.connect(c.out_pin("X1"), c.in_pin("xor1", 0))
    c.connect(c.out_pin("Y1"), c.in_pin("xor1", 1))
    c.connect(c.out_pin("X1"), c.in_pin("and1", 0), (180, 280), (180, 380))
    c.connect(c.out_pin("Y1"), c.in_pin("and1", 1), (220, 320), (220, 420))
    c.vertical_bus(
        c.out_pin("xor1"),
        400,
        300,
        440,
        [c.in_pin("sum1", 0), c.in_pin("and_c1", 0)],
    )
    c.vertical_bus(
        c.out_pin("and0"),
        440,
        200,
        480,
        [c.in_pin("sum1", 1), c.in_pin("and_c1", 1)],
    )
    c.connect(c.out_pin("and1"), c.in_pin("or_c1", 0), (620, 400), (620, 380))
    c.connect(c.out_pin("and_c1"), c.in_pin("or_c1", 1), (640, 460), (640, 420))
    c.connect(c.out_pin("sum1"), c.in_pin("S1"))

    # Bit 2 (most significant input bit)
    c.add_input("X2", "X2", 100, 540)
    c.add_input("Y2", "Y2", 100, 580)
    c.add_gate("xor2", "XOr", 260, 540)
    c.add_gate("sum2", "XOr", 500, 560)
    c.add_gate("and2", "And", 260, 640)
    c.add_gate("and_c2", "And", 500, 700)
    c.add_gate("or_c2", "Or", 680, 640)
    c.add_output("S2", "S2", 900, 580)
    c.add_output("S3", "S3", 900, 660)
    c.connect(c.out_pin("X2"), c.in_pin("xor2", 0))
    c.connect(c.out_pin("Y2"), c.in_pin("xor2", 1))
    c.connect(c.out_pin("X2"), c.in_pin("and2", 0), (180, 540), (180, 640))
    c.connect(c.out_pin("Y2"), c.in_pin("and2", 1), (220, 580), (220, 680))
    c.vertical_bus(
        c.out_pin("xor2"),
        400,
        560,
        700,
        [c.in_pin("sum2", 0), c.in_pin("and_c2", 0)],
    )
    c.vertical_bus(
        c.out_pin("or_c1"),
        820,
        400,
        740,
        [c.in_pin("sum2", 1), c.in_pin("and_c2", 1)],
    )
    c.connect(c.out_pin("and2"), c.in_pin("or_c2", 0), (620, 660), (620, 640))
    c.connect(c.out_pin("and_c2"), c.in_pin("or_c2", 1), (640, 720), (640, 680))
    c.connect(c.out_pin("sum2"), c.in_pin("S2"))
    c.connect(c.out_pin("or_c2"), c.in_pin("S3"))
    return c


def solution_10() -> Circuit:
    c = Circuit()
    for index, label in enumerate(("A3", "A2", "A1", "A0")):
        c.add_input(label, label, 100, 100 + 40 * index)
    for index, label in enumerate(("B3", "B2", "B1", "B0")):
        c.add_input(label, label, 100, 320 + 40 * index)
    c.add_gate("xor", "XOr", 300, 240)
    c.add_gate("equal", "Not", 440, 260)
    c.add_gate("not_b0", "Not", 300, 440)
    c.add_gate("leap", "And", 620, 320)
    c.add_output("L", "L", 840, 340)
    c.connect(c.out_pin("A0"), c.in_pin("xor", 0), (200, 220), (200, 240))
    c.connect(c.out_pin("B1"), c.in_pin("xor", 1), (240, 400), (240, 280))
    c.connect(c.out_pin("xor"), c.in_pin("equal"))
    c.connect(c.out_pin("B0"), c.in_pin("not_b0"))
    c.connect(c.out_pin("equal"), c.in_pin("leap", 0), (560, 260), (560, 320))
    c.connect(c.out_pin("not_b0"), c.in_pin("leap", 1), (580, 440), (580, 360))
    c.connect(c.out_pin("leap"), c.in_pin("L"))
    return c


def _flat_solution_11() -> Circuit:
    c = Circuit()
    c.add_input("A", "A", 100, 200)
    c.add_input("B", "B", 100, 280)
    c.add_input("C", "C", 100, 360)
    c.add_gate("or_ab", "Or", 300, 120)
    c.add_gate("or_abc", "Or", 500, 140)
    c.add_gate("and_ab", "And", 300, 240)
    c.add_gate("and_bc", "And", 300, 340)
    c.add_gate("and_ca", "And", 300, 440)
    c.add_gate("or_pairs_1", "Or", 520, 280)
    c.add_gate("or_pairs_2", "Or", 700, 340)
    c.add_gate("and_abc", "And", 520, 500)
    c.add_output("Y1", "Y1", 900, 160)
    c.add_output("Y2", "Y2", 900, 360)
    c.add_output("Y3", "Y3", 900, 520)
    c.vertical_bus(
        c.out_pin("A"),
        180,
        120,
        480,
        [c.in_pin("or_ab", 0), c.in_pin("and_ab", 0), c.in_pin("and_ca", 1)],
    )
    c.vertical_bus(
        c.out_pin("B"),
        220,
        160,
        340,
        [c.in_pin("or_ab", 1), c.in_pin("and_ab", 1), c.in_pin("and_bc", 0)],
    )
    c.vertical_bus(
        c.out_pin("C"),
        260,
        180,
        540,
        [c.in_pin("or_abc", 1), c.in_pin("and_bc", 1), c.in_pin("and_ca", 0), c.in_pin("and_abc", 1)],
    )
    c.connect(c.out_pin("or_ab"), c.in_pin("or_abc", 0))
    c.connect(c.out_pin("or_abc"), c.in_pin("Y1"))
    c.connect(c.out_pin("and_ab"), c.in_pin("or_pairs_1", 0), (460, 260), (460, 280))
    c.connect(c.out_pin("and_bc"), c.in_pin("or_pairs_1", 1), (480, 360), (480, 320))
    c.connect(c.out_pin("or_pairs_1"), c.in_pin("or_pairs_2", 0), (640, 300), (640, 340))
    c.connect(c.out_pin("and_ca"), c.in_pin("or_pairs_2", 1), (660, 460), (660, 380))
    c.connect(c.out_pin("or_pairs_2"), c.in_pin("Y2"))
    c.connect(c.out_pin("and_ab"), c.in_pin("and_abc", 0), (440, 260), (440, 500))
    c.connect(c.out_pin("and_abc"), c.in_pin("Y3"))
    return c


def solution_12() -> Circuit:
    c = Circuit()
    c.add_input("X1", "X1", 100, 200)
    c.add_input("X0", "X0", 100, 240)
    c.add_gate("positive", "Or", 300, 200)
    c.add_output("S0", "S0", 520, 220)
    c.connect(c.out_pin("X1"), c.in_pin("positive", 0))
    c.connect(c.out_pin("X0"), c.in_pin("positive", 1))
    c.connect(c.out_pin("positive"), c.in_pin("S0"))
    return c


def _flat_solution_13() -> Circuit:
    c = Circuit()
    for key, y in (("X1", 120), ("X0", 160), ("Y1", 280), ("Y0", 320), ("Z1", 440), ("Z0", 480)):
        c.add_input(key, key, 100, y)
    c.add_gate("x_positive", "Or", 280, 120)
    c.add_gate("y_positive", "Or", 280, 280)
    c.add_gate("z_positive", "Or", 280, 440)
    c.add_gate("xy", "And", 500, 160)
    c.add_gate("yz", "And", 500, 320)
    c.add_gate("zx", "And", 500, 480)
    c.add_gate("or_1", "Or", 680, 240)
    c.add_gate("or_2", "Or", 840, 320)
    c.add_output("S0", "S0", 1040, 340)
    for prefix, component_y in (("x", 120), ("y", 280), ("z", 440)):
        upper = prefix.upper()
        c.connect(c.out_pin(f"{upper}1"), Point(300, component_y))
        c.connect(
            c.out_pin(f"{upper}0"), Point(300, component_y + 40),
            (260, component_y + 20), (260, component_y + 40),
        )
    c.connect(c.out_pin("x_positive"), c.in_pin("xy", 0), (420, 140), (420, 160))
    c.connect(c.out_pin("y_positive"), c.in_pin("xy", 1), (440, 300), (440, 200))
    c.connect(c.out_pin("y_positive"), c.in_pin("yz", 0), (420, 300), (420, 320))
    c.connect(c.out_pin("z_positive"), c.in_pin("yz", 1), (440, 460), (440, 360))
    c.connect(c.out_pin("z_positive"), c.in_pin("zx", 0), (420, 460), (420, 480))
    c.connect(c.out_pin("x_positive"), c.in_pin("zx", 1), (400, 140), (400, 520))
    c.connect(c.out_pin("xy"), c.in_pin("or_1", 0), (620, 180), (620, 240))
    c.connect(c.out_pin("yz"), c.in_pin("or_1", 1), (640, 340), (640, 280))
    c.connect(c.out_pin("or_1"), c.in_pin("or_2", 0), (800, 260), (800, 320))
    c.connect(c.out_pin("zx"), c.in_pin("or_2", 1), (780, 500), (780, 360))
    c.connect(c.out_pin("or_2"), c.in_pin("S0"))
    return c


def solution_14() -> Circuit:
    c = Circuit()
    for key, y in (("X1", 100), ("X0", 180), ("Y1", 260),
                   ("Y0", 340), ("Z1", 420), ("Z0", 500)):
        c.add_input(key, key, 80, y)
    c.add_gate("both_high", "And", 300, 160)
    c.add_gate("both_low_bits", "And", 300, 320)
    c.add_gate("one_low_bit", "XOr", 300, 440)
    c.add_gate("z_positive", "Or", 300, 560)
    c.add_gate("mixed_case", "And", 520, 500)
    c.add_gate("or_1", "Or", 720, 360)
    c.add_gate("result", "And", 900, 260)
    c.add_output("S0", "S0", 1080, 280)

    c.connect(c.out_pin("X1"), c.in_pin("both_high", 0), (160, 100), (160, 160))
    c.connect(c.out_pin("Y1"), c.in_pin("both_high", 1), (200, 260), (200, 200))
    c.connect(c.out_pin("X0"), c.in_pin("both_low_bits", 0), (180, 180), (180, 320))
    c.connect(c.out_pin("Y0"), c.in_pin("both_low_bits", 1), (220, 340), (220, 360))
    c.connect(c.out_pin("X0"), c.in_pin("one_low_bit", 0), (180, 180), (180, 440))
    c.connect(c.out_pin("Y0"), c.in_pin("one_low_bit", 1), (220, 340), (220, 480))
    c.connect(c.out_pin("Z1"), c.in_pin("z_positive", 0), (240, 420), (240, 560))
    c.connect(c.out_pin("Z0"), c.in_pin("z_positive", 1), (260, 500), (260, 600))
    c.connect(c.out_pin("one_low_bit"), c.in_pin("mixed_case", 0), (460, 460), (460, 500))
    c.connect(c.out_pin("z_positive"), c.in_pin("mixed_case", 1), (480, 580), (480, 540))
    c.connect(c.out_pin("both_low_bits"), c.in_pin("or_1", 0), (660, 340), (660, 360))
    c.connect(c.out_pin("mixed_case"), c.in_pin("or_1", 1), (680, 520), (680, 400))
    c.connect(c.out_pin("both_high"), c.in_pin("result", 0))
    c.connect(c.out_pin("or_1"), c.in_pin("result", 1))
    c.connect(c.out_pin("result"), c.in_pin("S0"))
    return c


def solution_14_factored() -> Circuit:
    c = Circuit()
    c.add_input("X0", "X0", 100, 200)
    c.add_input("Y0", "Y0", 100, 280)
    c.add_input("Z1", "Z1", 100, 440)
    c.add_input("Z0", "Z0", 100, 520)
    c.add_gate("xy_any", "Or", 300, 200)
    c.add_gate("xy_pair", "And", 300, 320)
    c.add_gate("z_any", "Or", 300, 440)
    c.add_gate("threshold_tail", "Or", 500, 340)
    c.add_gate("low_condition", "And", 700, 260)
    c.add_input("X1", "X1", 500, 120)
    c.add_input("Y1", "Y1", 500, 160)
    c.add_gate("high_pair", "And", 700, 120)
    c.add_gate("result", "And", 900, 180)
    c.add_output("S0", "S0", 1080, 200)

    c.vertical_bus(c.out_pin("X0"), 180, 200, 320,
                   [c.in_pin("xy_any", 0), c.in_pin("xy_pair", 0)])
    c.vertical_bus(c.out_pin("Y0"), 220, 240, 360,
                   [c.in_pin("xy_any", 1), c.in_pin("xy_pair", 1)])
    c.connect(c.out_pin("Z1"), c.in_pin("z_any", 0))
    c.connect(c.out_pin("Z0"), c.in_pin("z_any", 1))
    c.connect(c.out_pin("xy_pair"), c.in_pin("threshold_tail", 0))
    c.connect(c.out_pin("z_any"), c.in_pin("threshold_tail", 1))
    c.connect(c.out_pin("xy_any"), c.in_pin("low_condition", 0))
    c.connect(c.out_pin("threshold_tail"), c.in_pin("low_condition", 1))
    c.connect(c.out_pin("X1"), c.in_pin("high_pair", 0))
    c.connect(c.out_pin("Y1"), c.in_pin("high_pair", 1))
    c.connect(c.out_pin("high_pair"), c.in_pin("result", 0))
    c.connect(c.out_pin("low_condition"), c.in_pin("result", 1))
    c.connect(c.out_pin("result"), c.in_pin("S0"))
    return c


def _flat_solution_15() -> Circuit:
    c = Circuit()
    c.add_input("Y", "Y", 100, 260)
    c.add_gate("not_y", "Not", 260, 340)
    c.add_gate("one", "Or", 420, 300)
    for index, label in enumerate(("a", "b", "c", "d", "e", "f", "g")):
        c.add_output(label, label, 760, 100 + 60 * index)
    c.connect(c.out_pin("Y"), c.in_pin("not_y"), (180, 260), (180, 340))
    c.connect(c.out_pin("Y"), c.in_pin("one", 0), (200, 260), (200, 300))
    c.connect(c.out_pin("not_y"), c.in_pin("one", 1))
    c.vertical_bus(
        c.out_pin("not_y"),
        560,
        100,
        340,
        [c.in_pin("a"), c.in_pin("e")],
    )
    c.vertical_bus(
        c.out_pin("one"),
        600,
        160,
        400,
        [c.in_pin("b"), c.in_pin("c"), c.in_pin("f")],
    )
    c.connect(c.out_pin("Y"), c.in_pin("d"), (660, 260), (660, 280))
    c.connect(c.out_pin("Y"), c.in_pin("g"), (700, 260), (700, 460))
    return c


def solution_09() -> Circuit:
    c = Circuit()
    for key, y in (("X2", 80), ("Y2", 120), ("X1", 280),
                   ("Y1", 320), ("X0", 480), ("Y0", 520)):
        c.add_input(key, key, 100, y)
    c.add_subcircuit("bit0", Path(HWP_STARTER_FILES[9]).name, 300, 500, 2, 2)
    c.add_subcircuit("bit1", Path(HWP_STARTER_FILES[10]).name, 500, 300, 3, 2)
    c.add_subcircuit("bit2", Path(HWP_STARTER_FILES[10]).name, 700, 100, 3, 2)
    c.add_output("S3", "S3", 900, 180)
    c.add_output("S2", "S2", 900, 100)
    c.add_output("S1", "S1", 900, 300)
    c.add_output("S0", "S0", 900, 500)

    c.connect(c.out_pin("X0"), c.in_pin("bit0", 0))
    c.connect(c.out_pin("Y0"), c.in_pin("bit0", 1))
    c.connect(c.out_pin("X1"), c.in_pin("bit1", 0))
    c.connect(c.out_pin("Y1"), c.in_pin("bit1", 1))
    c.connect(c.out_pin("bit0", 1), c.in_pin("bit1", 2), (440, 520), (440, 340))
    c.connect(c.out_pin("X2"), c.in_pin("bit2", 0))
    c.connect(c.out_pin("Y2"), c.in_pin("bit2", 1))
    c.connect(c.out_pin("bit1", 1), c.in_pin("bit2", 2), (620, 320), (620, 140))
    c.connect(c.out_pin("bit0", 0), c.in_pin("S0"))
    c.connect(c.out_pin("bit1", 0), c.in_pin("S1"))
    c.connect(c.out_pin("bit2", 0), c.in_pin("S2"))
    c.connect(c.out_pin("bit2", 1), c.in_pin("S3"), (840, 120), (840, 180))
    return c


def solution_11() -> Circuit:
    c = Circuit()
    c.add_input("A", "A", 100, 200)
    c.add_input("B", "B", 100, 280)
    c.add_input("C", "C", 100, 360)
    c.add_subcircuit("full", "08_full_adder.dig", 420, 260, 3, 2)
    c.add_gate("or_ab", "Or", 480, 80)
    c.add_gate("or_abc", "Or", 680, 100)
    c.add_gate("and_ab", "And", 480, 460)
    c.add_gate("and_abc", "And", 680, 480)
    c.add_output("Y1", "Y1", 900, 120)
    c.add_output("Y2", "Y2", 900, 300)
    c.add_output("Y3", "Y3", 900, 500)

    c.vertical_bus(
        c.out_pin("A"), 180, 80, 460,
        [c.in_pin("or_ab", 0), c.in_pin("full", 0), c.in_pin("and_ab", 0)],
    )
    c.vertical_bus(
        c.out_pin("B"), 220, 120, 500,
        [c.in_pin("or_ab", 1), c.in_pin("full", 1), c.in_pin("and_ab", 1)],
    )
    c.vertical_bus(
        c.out_pin("C"), 260, 140, 520,
        [c.in_pin("or_abc", 1), c.in_pin("full", 2), c.in_pin("and_abc", 1)],
    )
    c.connect(c.out_pin("or_ab"), c.in_pin("or_abc", 0))
    c.connect(c.out_pin("and_ab"), c.in_pin("and_abc", 0))
    c.connect(c.out_pin("or_abc"), c.in_pin("Y1"))
    c.connect(c.out_pin("full", 1), c.in_pin("Y2"), (800, 280), (800, 300))
    c.connect(c.out_pin("and_abc"), c.in_pin("Y3"))
    return c


def solution_13() -> Circuit:
    c = Circuit()
    for key, y in (("X1", 120), ("X0", 160), ("Y1", 280),
                   ("Y0", 320), ("Z1", 440), ("Z0", 480)):
        c.add_input(key, key, 100, y)
    c.add_subcircuit("x_positive", Path(HWP_STARTER_FILES[12]).name, 300, 120, 2, 1)
    c.add_subcircuit("y_positive", Path(HWP_STARTER_FILES[12]).name, 300, 280, 2, 1)
    c.add_subcircuit("z_positive", Path(HWP_STARTER_FILES[12]).name, 300, 440, 2, 1)
    c.add_gate("xor_xy", "XOr", 520, 160)
    c.add_gate("and_xy", "And", 520, 280)
    c.add_gate("and_carry", "And", 700, 360)
    c.add_gate("result", "Or", 900, 300)
    c.add_output("S0", "S0", 1100, 320)

    for prefix, component_y in (("x", 120), ("y", 280), ("z", 440)):
        upper = prefix.upper()
        c.connect(c.out_pin(f"{upper}1"), Point(300, component_y))
        c.connect(c.out_pin(f"{upper}0"), Point(300, component_y + 40))
    c.vertical_bus(Point(360, 140), 400, 140, 280,
                   [c.in_pin("xor_xy", 0), c.in_pin("and_xy", 0)])
    c.vertical_bus(Point(360, 300), 440, 200, 320,
                   [c.in_pin("xor_xy", 1), c.in_pin("and_xy", 1)])
    c.connect(Point(360, 460), c.in_pin("and_carry", 1), (660, 460), (660, 400))
    c.connect(c.out_pin("xor_xy"), c.in_pin("and_carry", 0),
              (620, 180), (620, 360))
    c.connect(c.out_pin("and_xy"), c.in_pin("result", 0))
    c.connect(c.out_pin("and_carry"), c.in_pin("result", 1),
              (840, 380), (840, 340))
    c.connect(c.out_pin("result"), c.in_pin("S0"))
    return c


def solution_15() -> Circuit:
    c = Circuit()
    c.add_input("Y", "Y", 100, 360)
    c.add_gate("not_y", "Not", 260, 440)
    c.add_gate("one", "Or", 420, 300)
    c.add_gate("zero", "And", 420, 500)
    c.add_seven_segment("display", 900, 260)
    output_positions = {
        "a": (1180, 100), "b": (1180, 160), "c": (1180, 220),
        "d": (1180, 280), "e": (1180, 380), "f": (1180, 440),
        "g": (1180, 500), "dp": (1180, 560),
    }
    for label, (x, y) in output_positions.items():
        c.add_output(label, label, x, y)

    c.connect(c.out_pin("Y"), c.in_pin("not_y"), (200, 360), (200, 440))
    c.connect(c.out_pin("Y"), c.in_pin("one", 0), (220, 360), (220, 300))
    c.connect(c.out_pin("not_y"), c.in_pin("one", 1), (360, 440), (360, 340))
    c.connect(c.out_pin("Y"), c.in_pin("zero", 0), (180, 360), (180, 500))
    c.connect(c.out_pin("not_y"), c.in_pin("zero", 1), (380, 440), (380, 540))

    # The paired pins in each column share a signal only where the glyphs do.
    c.connect(c.out_pin("not_y"), c.seven_segment_pin("display", "e"),
              (560, 440), (560, 400))
    c.connect(c.seven_segment_pin("display", "e"),
              c.seven_segment_pin("display", "a"))
    c.connect(c.out_pin("one"), c.seven_segment_pin("display", "f"),
              (800, 320), (800, 420), (920, 420))
    c.connect(c.seven_segment_pin("display", "f"),
              c.seven_segment_pin("display", "b"))
    c.connect(c.out_pin("one"), c.seven_segment_pin("display", "c"),
              (760, 320), (760, 200), (940, 200))
    c.connect(c.out_pin("Y"), c.seven_segment_pin("display", "g"),
              (720, 360), (720, 480), (940, 480))
    c.connect(c.out_pin("Y"), c.seven_segment_pin("display", "d"),
              (680, 360), (680, 180), (960, 180))
    c.connect(c.out_pin("zero"), c.seven_segment_pin("display", "dp"),
              (840, 520), (840, 540), (960, 540))

    c.connect(c.seven_segment_pin("display", "a"), c.in_pin("a"), (900, 100))
    c.connect(c.seven_segment_pin("display", "b"), c.in_pin("b"), (920, 160))
    c.connect(c.seven_segment_pin("display", "c"), c.in_pin("c"), (940, 220))
    c.connect(c.seven_segment_pin("display", "d"), c.in_pin("d"), (960, 280))
    c.connect(c.seven_segment_pin("display", "e"), c.in_pin("e"), (900, 380))
    c.connect(c.seven_segment_pin("display", "f"), c.in_pin("f"), (920, 440))
    c.connect(c.seven_segment_pin("display", "g"), c.in_pin("g"), (940, 500))
    c.connect(c.seven_segment_pin("display", "dp"), c.in_pin("dp"), (960, 560))
    return c


SOLUTIONS = {
    2: solution_02,
    3: solution_03,
    4: solution_04,
    5: solution_05,
    6: solution_06,
    7: solution_10,
    8: _flat_solution_11,
    9: solution_07,
    10: solution_08,
    11: solution_09,
    12: solution_12,
    13: solution_13,
    14: solution_14,
    15: solution_15,
}
CANONICAL_EXPORTS = {
    "07_half_adder.dig": 9,
    "08_full_adder.dig": 10,
    "12_at_least_one.dig": 12,
}
ALTERNATE_SOLUTIONS = []
# The former Challenge 14 alternate is intentionally not distributed: it has
# no matching contestant starter filename.


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    expected = set(HWP_STARTER_FILES.values())
    for stale in OUT_DIR.rglob("*.dig"):
        if stale.relative_to(OUT_DIR).as_posix() not in expected:
            stale.unlink()
    for challenge_id, builder in SOLUTIONS.items():
        path = OUT_DIR / HWP_STARTER_FILES[challenge_id]
        builder().write(path)
        print(f"challenge {challenge_id:>2}: {path.relative_to(REPO_ROOT)}")
    for challenge_id, filename, builder in ALTERNATE_SOLUTIONS:
        path = OUT_DIR / filename
        builder().write(path)
        print(f"alternate {challenge_id:>2}: {path.relative_to(REPO_ROOT)}")
    CANONICAL_DIR.mkdir(parents=True, exist_ok=True)
    for filename, challenge_id in CANONICAL_EXPORTS.items():
        shutil.copyfile(OUT_DIR / HWP_STARTER_FILES[challenge_id], CANONICAL_DIR / filename)
        print(f"canonical {challenge_id:>2}: canonical/{filename}")


if __name__ == "__main__":
    main()
