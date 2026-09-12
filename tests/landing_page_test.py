"""The landing page at /, as bin/bootstrap.py ships it.

INDEX_CONTENT is a CTFd Page: CTFd pastes it into ``main > .container``
as-is, so the page itself can run nothing. Its phase, its auth state and the
clock digits are written by econ_judge/assets/landing.js — loaded on every
page from THEME_HEADER_CSS — onto markup that arrives bare. These tests pin
the page's half of that contract (what the served markup must and must not
carry, which rule shows which action, and that every name landing.js writes
is one the page reads), and they check that every schedule, count, score and
limit figure the page prints is one the code enforces, in every copy, so a
schedule or limit change cannot leave the front page quoting the old value.

bootstrap.py binds CTFd at module scope, so its strings are read with ``ast``
rather than imported. The upload limits and error sentences come from
econ_judge.endpoints under tests/plugin_surface_test.py's CTFd stubs;
competition.py and grader.py import nothing from CTFd and load directly.
"""

from __future__ import annotations

import ast
import collections
import importlib
import importlib.util
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP_SOURCE = (ROOT / "bin" / "bootstrap.py").read_text(encoding="utf-8")
BOOTSTRAP_TREE = ast.parse(BOOTSTRAP_SOURCE)
LANDING_JS = (ROOT / "econ_judge" / "assets" / "landing.js").read_text(encoding="utf-8")
VIEW_JS = (ROOT / "econ_judge" / "assets" / "view.js").read_text(encoding="utf-8")


def load(name, path, register=False):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    if register:
        sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def bootstrap_assignment(name: str) -> ast.Assign:
    for node in BOOTSTRAP_TREE.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return node
    raise AssertionError(f"bin/bootstrap.py no longer defines {name}")


def bootstrap_constant(name: str):
    return ast.literal_eval(bootstrap_assignment(name).value)


INDEX = bootstrap_constant("INDEX_CONTENT")
THEME = bootstrap_constant("THEME_HEADER_CSS")
DEMO_TEAMS = bootstrap_constant("DEMO_TEAMS")

# Registered like tests/competition_test.py does, for its dataclass.
competition = load("landing_competition", ROOT / "econ_judge" / "competition.py", register=True)
problemset = load("landing_problemset", ROOT / "econ_judge" / "problemset.py")
register = load("landing_register", ROOT / "tests" / "register_challenges.py")
# Only the stub context manager is taken from it, as tests/health_export_test.py
# does; loading by path keeps this file runnable on its own.
ctfd_stubs = load("landing_surface", ROOT / "tests" / "plugin_surface_test.py").ctfd_stubs

# The timeout the grader falls back to when the deploy sets none — the only
# value a static page can honestly state.
with patch.dict(os.environ):
    os.environ.pop("ECON_JUDGE_TIMEOUT", None)
    grader = load("landing_grader", ROOT / "econ_judge" / "grader.py")


def endpoints_module():
    with ctfd_stubs():
        return importlib.import_module("econ_judge.endpoints")


# The submission-folder figure: the answer it shows and the one part that
# answer uses. Both files, the figure's caption and the grader's rules are
# checked against these two ids.
EXAMPLE_ID = 10
EXAMPLE_PART_ID = 9
SOLUTIONS = ROOT / "solutions" / "2026-summer"


# ── reading the page ────────────────────────────────────────────────────

class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []

    def text(self):
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def classes(self):
        return set((self.attrs.get("class") or "").split())

    def walk(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.walk()

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent


class TreeBuilder(HTMLParser):
    VOID = {"area", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.current = Node("#root", {}, None)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in self.VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.current.children.append(Node(tag, attrs, self.current))

    def handle_endtag(self, tag):
        node = self.current
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.current = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


def squash(text: str) -> str:
    return " ".join(text.split())


def split_top_level(selector_list: str):
    """Split a selector list on commas that are not inside parentheses."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(selector_list):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(selector_list[start:i])
            start = i + 1
    parts.append(selector_list[start:])
    return [squash(p) for p in parts if p.strip()]


def css_rules_in_context(css: str):
    """(conditions, selector, declarations) for every style rule, in source
    order. `conditions` is the tuple of enclosing @media / @supports / @container
    preludes; @keyframes and @property blocks hold no selectors and are skipped."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules = []

    def walk(block, conditions):
        i = 0
        while True:
            j = block.find("{", i)
            if j < 0:
                return
            prelude = squash(block[i:j])
            depth, k = 1, j + 1
            while depth:
                depth += {"{": 1, "}": -1}.get(block[k], 0)
                k += 1
            body = block[j + 1:k - 1]
            if prelude.startswith(("@media", "@supports", "@container")):
                walk(body, conditions + (prelude,))
            elif not prelude.startswith("@"):
                rules.extend((conditions, selector, body) for selector in split_top_level(prelude))
            i = k

    walk(css, ())
    return rules


def css_rules(css: str):
    """(selector, declarations) for every style rule, conditional ones included."""
    return [(selector, body) for _, selector, body in css_rules_in_context(css)]


def declares(body: str, prop: str):
    """The value `body` gives `prop`, or None."""
    found = re.search(rf"(?:^|;)\s*{re.escape(prop)}\s*:\s*([^;]+)", body)
    return found.group(1).strip() if found else None


def specificity(selector: str):
    """(ids, classes, types) as CSS Selectors 4 counts them, for the selector
    shapes this page uses: :not/:is/:has take their most specific argument,
    :where counts nothing, and any other functional pseudo-class counts once."""
    a = b = c = 0
    while True:
        found = re.search(r":(not|is|has|where)\(", selector)
        if not found:
            break
        start = i = found.end()
        depth = 1
        while depth:
            depth += {"(": 1, ")": -1}.get(selector[i], 0)
            i += 1
        inner = selector[start:i - 1]
        if found.group(1) != "where":
            inner_a, inner_b, inner_c = max(specificity(part) for part in split_top_level(inner))
            a, b, c = a + inner_a, b + inner_b, c + inner_c
        selector = selector[:found.start()] + selector[i:]
    b += len(re.findall(r"\[[^\]]*\]", selector))
    selector = re.sub(r"\[[^\]]*\]", "", selector)
    selector = re.sub(r"\([^()]*\)", "", selector)
    a += len(re.findall(r"#[\w-]+", selector))
    b += len(re.findall(r"\.[\w-]+", selector)) + len(re.findall(r"(?<!:):(?!:)[\w-]+", selector))
    c += len(re.findall(r"(?:^|[\s>+~])[a-zA-Z][\w-]*", selector)) + len(re.findall(r"::[\w-]+", selector))
    return a, b, c


def theme_css() -> str:
    """THEME_HEADER_CSS's own style element, without the tag or its @imports."""
    css = re.search(r'<style id="econ-judge-theme">(.*?)</style>', THEME, re.S).group(1)
    return re.sub(r"@import[^;]*;", "", css)


def css_class_tokens(css: str) -> set:
    tokens = set()
    for selector, _ in css_rules(css):
        selector = re.sub(r"\[[^\]]*\]", "", selector)
        tokens.update(re.findall(r"\.(-?[_a-zA-Z][_a-zA-Z0-9-]*)", selector))
    return tokens


assert INDEX.startswith("<style>"), "INDEX_CONTENT no longer opens with its style block"
STYLE = INDEX[len("<style>"):INDEX.index("</style>")]
MARKUP = INDEX[INDEX.index("</style>") + len("</style>"):]
RULES_IN_CONTEXT = css_rules_in_context(STYLE)
RULES = [(selector, body) for _, selector, body in RULES_IN_CONTEXT]
_builder = TreeBuilder()
_builder.feed(MARKUP)
_builder.close()
TREE = _builder.root
ELEMENTS = list(TREE.walk())
TEXT = squash(TREE.text())


def by_attr(name, value=None):
    return [e for e in ELEMENTS if name in e.attrs and (value is None or e.attrs[name] == value)]


def one(name, value=None):
    found = by_attr(name, value)
    if len(found) != 1:
        raise AssertionError(f"expected one element with {name}={value!r}, found {len(found)}")
    return found[0]


def with_classes(*names, within=None):
    pool = list(within.walk()) if within is not None else ELEMENTS
    return [e for e in pool if set(names) <= e.classes()]


def minutes(delta: timedelta) -> int:
    return int(delta.total_seconds() // 60)


def probe_phases() -> dict:
    """Every phase competition.current_phase can return, driven through the
    function itself rather than retyped: name -> the CompetitionPhase."""
    start = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)
    r1, gap, r2 = competition.ROUND_1_DURATION, competition.BREAK_DURATION, competition.ROUND_2_DURATION
    found = {}
    with patch.dict(os.environ, {}, clear=True):
        phase = competition.current_phase()
        found[phase.name] = phase
    with patch.dict(os.environ, {"ECON_JUDGE_COMPETITION_START": "not-a-date"}, clear=True):
        phase = competition.current_phase()
        found[phase.name] = phase
    with patch.dict(os.environ, {"ECON_JUDGE_COMPETITION_START": start.isoformat()}, clear=True):
        for moment in (start - timedelta(seconds=1), start, start + r1, start + r1 + gap, start + r1 + gap + r2):
            phase = competition.current_phase(moment)
            found[phase.name] = phase
    return found


PHASES = probe_phases()
# The class a phase's copy carries; the attribute-free stage is `pending`.
VARIANTS = {**{name: name for name in PHASES}, None: "pending"}


def revealing(token: str):
    """Selectors whose subject carries .token and whose rule shows it."""
    for selector, body in RULES:
        subject = selector.split()[-1]
        display = re.search(r"display\s*:\s*([^;]+)", body)
        if re.search(rf"\.{token}(?![\w-])", subject) and display and display.group(1).strip() != "none":
            yield selector


def phase_in(selector: str):
    found = re.findall(r'\[data-phase="([^"]+)"\]', selector)
    return found[0] if found else None


def subject_classes(selector: str) -> set:
    """The classes on a selector's subject (its last compound), with the
    arguments of functional pseudo-classes such as :has() and :not() dropped."""
    previous = None
    while previous != selector:
        previous = selector
        selector = re.sub(r":[\w-]+\((?:[^()]|\([^()]*\))*\)", "", selector)
    subject = selector.split()[-1]
    subject = re.sub(r"\[[^\]]*\]", "", subject)
    return set(re.findall(r"\.([\w-]+)", subject))


def visible_prose(phase, auth):
    """The band's sentences a visitor reads in one state, from the markup and
    the reveal rules: the clock column's note, the lede, and the action's own
    words (its hint or its quiet box)."""
    variant = VARIANTS[phase]
    hero = with_classes("hero")[0]
    parts = [e for e in with_classes("rnote", "ph", variant, within=hero)]
    if phase == "before":
        column = with_classes("au-inline")[0]
        parts += [e for e in column.walk() if "lede" in e.classes()]
    else:
        column = with_classes("actcol")[0]
        parts += [e for e in with_classes("lede", "ph", variant, within=column) if "tail" not in e.classes()]
    if auth == "out":
        shown = {"out"}
    else:
        shown = {kind for kind in ("go", "score", "wait", "none")
                 if any('[data-auth="in"]' in s and phase_in(s) == phase for s in revealing(kind))}
    for block in column.walk():
        if "au" in block.classes() and block.classes() & shown:
            parts += [e for e in block.walk() if e.classes() & {"hint", "quiet"}]
    return " ".join(squash(e.text()) for e in parts)


def human_size(size: int) -> str:
    """view.js humanSize(), which captions the folder the upload box holds."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.2f} MB"


def lf_size(path: Path) -> int:
    """A file's size as git stores it (LF line ends), whatever the checkout's
    line endings are."""
    return len(path.read_bytes().replace(b"\r\n", b"\n"))


def dig_components(path: Path) -> set:
    names = re.findall(r"<elementName>([^<]+)</elementName>", path.read_text(encoding="utf-8"))
    return {name for name in names if name.endswith(".dig")}


# ── the served page ─────────────────────────────────────────────────────

class ServedMarkupTests(unittest.TestCase):
    def test_the_page_runs_and_fetches_nothing(self):
        # CTFd serves this verbatim to every visitor; behaviour belongs in
        # landing.js, which THEME_HEADER_CSS loads.
        lowered = INDEX.lower()
        for needle in ("<script", "<link", "@import"):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, lowered)
        self.assertEqual(lowered.count("<style"), 1)
        handlers = [e.tag for e in ELEMENTS if any(name.startswith("on") for name in e.attrs)]
        self.assertEqual(handlers, [])

    def test_the_literal_stays_raw(self):
        # A non-raw string turns a CSS escape such as "\2713" into a control
        # character before CTFd ever sees it, silently.
        segment = ast.get_source_segment(BOOTSTRAP_SOURCE, bootstrap_assignment("INDEX_CONTENT").value)
        self.assertTrue(segment.startswith('r"""<style>'), segment[:12])

    def test_the_stage_is_served_bare(self):
        # The attribute-free stage IS the pending state (연결 중, --:--, and
        # 로그인 for a signed-out visitor) and what a visitor without
        # JavaScript keeps; landing.js writes the rest.
        stages = [e for e in ELEMENTS if "stage" in e.classes()]
        self.assertEqual(len(stages), 1)
        self.assertEqual(set(stages[0].attrs), {"class"})
        root = one("id", "econ-landing")
        self.assertIn(stages[0], list(root.walk()))
        self.assertEqual(by_attr("style"), [], "no inline style anywhere in the served markup")

    def test_the_page_declares_its_language(self):
        # CTFd's <html> carries no lang; the page's own text is Korean.
        self.assertEqual(one("id", "econ-landing").attrs.get("lang"), "ko")

    def test_the_phase_announcer_is_served_empty(self):
        # landing.js writes one sentence here when the phase or the tail
        # changes; served empty, it says nothing on first paint.
        status = one("id", "econ-landing-status")
        self.assertEqual(status.tag, "p")
        self.assertEqual(status.attrs.get("role"), "status")
        self.assertEqual(status.attrs.get("aria-live"), "polite")
        self.assertEqual(status.text(), "")
        stage = next(e for e in ELEMENTS if "stage" in e.classes())
        children = [c for c in stage.children if isinstance(c, Node)]
        band = next(c for c in children if "band" in c.classes())
        self.assertIn(status, children)
        self.assertLess(children.index(status), children.index(band))
        self.assertIn("vh", status.classes())
        hidden = [b for s, b in RULES if s == ".econ-landing .vh"]
        self.assertTrue(hidden and declares(hidden[0], "position") == "absolute" and declares(hidden[0], "clip"))
        self.assertFalse(any(re.search(r"display\s*:\s*none", b) for s, b in RULES if "vh" in subject_classes(s)))

    def test_the_clock_never_takes_the_navbar_clocks_id(self):
        # round-ui.js returns early when #econ-round-countdown already exists,
        # and the navbar clock would silently die on every page.
        self.assertEqual(INDEX.count('id="econ-landing-clock"'), 1)
        self.assertNotIn("econ-round-countdown", MARKUP)
        clock = one("id", "econ-landing-clock")
        self.assertEqual(clock.attrs.get("role"), "timer")
        self.assertEqual(len(by_attr("id", clock.attrs["aria-labelledby"])), 1)

    def test_the_js_slots_read_as_placeholders(self):
        slots = {e.attrs["data-econ-clock"]: e for e in by_attr("data-econ-clock")}
        self.assertEqual(set(slots), {"hh", "mm", "ss", "elapsed", "total"})
        self.assertEqual(slots["hh"].text(), "")
        self.assertEqual(slots["mm"].text(), "--")
        self.assertEqual(slots["ss"].text(), ":--")
        self.assertEqual(slots["elapsed"].text(), "")
        for slot in ("hh", "mm", "ss"):
            self.assertIn(slots[slot], list(one("id", "econ-landing-clock").walk()))

    def test_the_signed_in_sentence_reads_with_or_without_a_name(self):
        user = one("data-econ-user")
        self.assertEqual(user.text(), "")
        # served: "로그인되어 있습니다." — filled: "2조로 로그인되어 있습니다."
        self.assertEqual(squash(user.parent.text()), "로그인되어 있습니다.")
        self.assertIs(user.parent.children[0], user)
        # landing.js's userLabel() ends the name with its own space
        # (tests/landing_clock_test.py pins that); a second one from the page
        # would be one more thing to keep in step.
        self.assertFalse(
            any("data-econ-user" in s and "::after" in s for s, _ in RULES),
            "the space after the team name has one owner, landing.js",
        )

    def test_no_specimen_leftovers_or_invented_values(self):
        for needle in ("cand-", "mock", "42:17", "경과 28분", "2조", "14.2 KB"):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, INDEX)
        self.assertNotIn("--el", MARKUP)

    def test_ids_are_unique_and_every_label_resolves(self):
        ids = [e.attrs["id"] for e in by_attr("id")]
        self.assertEqual(len(ids), len(set(ids)), collections.Counter(ids).most_common(3))
        for label in [e for e in ELEMENTS if e.tag == "label"]:
            with self.subTest(label=label.attrs.get("for")):
                self.assertIn(label.attrs.get("for"), ids)

    def test_the_toy_has_one_pair_of_answers_per_row(self):
        radios = [e for e in ELEMENTS if e.tag == "input" and e.attrs.get("type") == "radio"]
        counts = collections.Counter(r.attrs.get("name") for r in radios)
        self.assertEqual(len(counts), 8)
        self.assertEqual(set(counts.values()), {2})
        labelled = {e.attrs["for"] for e in ELEMENTS if e.tag == "label"}
        self.assertTrue(all(r.attrs.get("id") in labelled for r in radios))
        self.assertEqual(len([e for e in ELEMENTS if "toy" in e.classes()]), 1)

    def test_the_toy_grades_in_the_problem_pages_three_tiers(self):
        # The same word the problem page's result card gives each tier, and
        # the card for none right is its 실패, never a 부분 통과 over 0 / 8.
        words = dict(re.findall(r'"is-(\w+)":\s*"([^"]+)"', VIEW_JS))
        cards = {kind: with_classes("vp", kind) for kind in ("partial", "fail", "pass")}
        self.assertEqual(len(with_classes("vp")), 3)
        for kind, found in cards.items():
            with self.subTest(kind=kind):
                self.assertEqual(len(found), 1)
                self.assertEqual([squash(e.text()) for e in with_classes("chip", within=found[0])], [words[kind]])
        self.assertTrue(any(e.tag == "h3" and squash(e.text()) == "0 / 8 테스트케이스 통과" for e in cards["fail"][0].walk()))
        self.assertTrue(any(e.tag == "h3" and squash(e.text()) == "8 / 8 테스트케이스 통과" for e in cards["pass"][0].walk()))
        # The answer key is the majority the question states (A is the high
        # bit of the case number), and 실패 shows exactly when every row's
        # right answer is left unchecked.
        def key(suffix, card):
            row = rf"\.i(\d)\.o(\d){re.escape(suffix)}"
            chains = [s for s, b in RULES if s.endswith(f".vp.{card}") and declares(b, "display") == "block"
                      and len(re.findall(row, s)) == 8]
            self.assertEqual(len(chains), 1, card)
            return {int(i): int(o) for i, o in re.findall(row, chains[0])}
        majority = {case: int(bin(case).count("1") >= 2) for case in range(8)}
        self.assertEqual(key(":checked ~", "pass"), majority)
        self.assertEqual(key(":not(:checked) ~", "fail"), majority)

    def test_every_action_has_somewhere_to_go(self):
        hrefs = {e.attrs["href"] for e in by_attr("href")}
        self.assertTrue({"/login", "/challenges", "/my-score"} <= hrefs, hrefs)

    def test_no_copy_points_at_a_side_of_the_screen(self):
        # The hero reflows below 992px, so "the table on the right" would
        # send a phone reader to nothing.
        for word in ("오른쪽", "왼쪽"):
            with self.subTest(word=word):
                self.assertNotIn(word, TEXT)


class StateMachineTests(unittest.TestCase):
    def test_every_phase_competition_can_return_is_styled(self):
        # The probe and the source have to agree, or a phase was missed.
        named = {
            node.args[0].value
            for node in ast.walk(ast.parse((ROOT / "econ_judge" / "competition.py").read_text(encoding="utf-8")))
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "CompetitionPhase"
            and node.args and isinstance(node.args[0], ast.Constant)
        }
        self.assertEqual(set(PHASES), named)
        for phase in PHASES:
            with self.subTest(phase=phase):
                self.assertIn(f'[data-phase="{phase}"]', STYLE)

    def test_the_missing_attribute_is_the_pending_state(self):
        # landing.js never writes a "loading" phase; it removes the attribute.
        self.assertNotIn('data-phase="loading"', INDEX)
        self.assertTrue(any(":not([data-phase])" in s and s.endswith(".ph.pending") for s in revealing("pending")))

    def test_every_phase_names_its_own_copy(self):
        # Each phase's copy is the .ph variant of the same name, revealed by
        # its own [data-phase]; a phase added to competition.py has to bring
        # its chip, clock label, strip values and lede.
        hero, strip = with_classes("hero")[0], with_classes("strip")[0]
        clabel = one("id", "econ-landing-clabel")
        # 현재 단계's value, and the round cell's label and value
        cells = [c for c in strip.children if isinstance(c, Node)][:2]
        slots = [part for cell in cells for part in cell.children
                 if isinstance(part, Node) and any("ph" in e.classes() for e in part.walk())]
        self.assertEqual([s.tag for s in slots], ["b", "small", "b"])
        notes = {"break", "open", "misconfigured"}
        for phase, variant in VARIANTS.items():
            with self.subTest(phase=phase):
                gate = f'[data-phase="{phase}"]' if phase else ":not([data-phase])"
                self.assertTrue(any(gate in s for s in revealing(variant)))
                self.assertEqual(len([e for e in with_classes("chip", "ph", variant) if "tail" not in e.classes()]), 1)
                self.assertEqual(len(with_classes("ph", variant, within=clabel)), 1)
                for slot in slots:
                    self.assertEqual(len(with_classes("ph", variant, within=slot)), 1, slot.tag)
                self.assertEqual(len(with_classes("rnote", "ph", variant, within=hero)), 1 if phase in notes else 0)
                ledes = [e for e in with_classes("lede", "ph", variant) if "tail" not in e.classes()]
                self.assertEqual(len(ledes), 0 if phase == "before" else 1)
        self.assertEqual(len(with_classes("rnote")), len(notes), "every note is a .ph variant")

    def test_no_state_says_the_same_thing_twice(self):
        # The note under the clock, the lede and the action's own words sit
        # side by side on a desktop and one under another on a phone.
        facts = ("운영진에게 알려주세요", "열리지 않습니다", "불러오는 중", "시작 시간이 되면",
                 "끝났습니다", "종료되었습니다", "새로 고침")
        for phase in VARIANTS:
            for auth in ("in", "out"):
                prose = visible_prose(phase, auth)
                with self.subTest(phase=phase, auth=auth, prose=prose):
                    # A note reads "<phase> · <sentence>", so the joiner splits too.
                    sentences = [s.strip() for s in re.split(r"(?<=[.])\s+|\s·\s", prose) if len(s.strip()) > 6]
                    self.assertEqual(len(sentences), len(set(sentences)), collections.Counter(sentences).most_common(2))
                    for fact in facts:
                        self.assertLessEqual(prose.count(fact), 1, fact)

    def test_signed_out_is_always_offered_login(self):
        shown = list(revealing("out"))
        self.assertIn('.econ-landing [data-auth="out"] .au.out', shown)
        self.assertIn(".econ-landing .stage:not([data-auth]) .au.out", shown)
        self.assertTrue(all(phase_in(s) is None for s in shown))

    def test_a_signed_in_team_is_never_painted_login(self):
        # Before landing.js has written data-auth, CTFd's own navbar (its
        # logout link) is the one sign of a signed-in visitor.
        guard = 'body:has(.navbar a[href$="/logout"]) .econ-landing .stage:not([data-auth]) .au.out'
        rules = [b for s, b in RULES if s == guard]
        self.assertEqual(len(rules), 1)
        self.assertEqual(declares(rules[0], "display"), "none")
        reveal = ".econ-landing .stage:not([data-auth]) .au.out"
        self.assertGreater(specificity(guard), specificity(reveal))

    def test_a_signed_in_team_is_only_sent_where_it_can_submit(self):
        # 도전 과제로 opens an empty board in 시작 전 and 일정 설정 필요, and before
        # the phase is known it cannot be promised at all.
        submitting = {name for name, phase in PHASES.items() if phase.submissions_open}
        shown = list(revealing("go"))
        self.assertTrue(shown)
        for selector in shown:
            with self.subTest(selector=selector):
                self.assertIn('[data-auth="in"]', selector)
                self.assertIn(phase_in(selector), submitting)
                self.assertNotIn(":not([data-phase])", selector)
        self.assertEqual({phase_in(s) for s in shown}, submitting)
        for closed in ("before", "misconfigured"):
            self.assertFalse(any(f'[data-phase="{closed}"]' in s for s in shown))

    def test_every_phase_gives_a_signed_in_team_exactly_one_answer(self):
        actions = {kind: {phase_in(s) for s in revealing(kind) if '[data-auth="in"]' in s} for kind in ("go", "score", "wait", "none")}
        closed_with_board = {n for n, p in PHASES.items() if not p.submissions_open and p.visible_challenge_ids}
        self.assertEqual(actions["score"], closed_with_board)
        self.assertEqual(actions["none"], {"misconfigured"})
        self.assertEqual(actions["wait"], {"before"})
        seen = collections.Counter(p for phases in actions.values() for p in phases)
        self.assertEqual(dict(seen), {name: 1 for name in PHASES})

    def test_the_navbar_pill_goes_only_once_a_phase_is_written(self):
        pill = [(s, b) for s, b in RULES if "#econ-round-countdown" in s]
        self.assertTrue(pill)
        for selector, body in pill:
            self.assertIn(".stage[data-phase]", selector)
            self.assertRegex(body, r"display\s*:\s*none")

    def test_the_band_breaks_out_and_main_guards_it(self):
        # 100vw counts the scrollbar, so the band is always a little wider
        # than the page. main clips it: main spans the window, and unlike
        # body its overflow is not handed to the viewport, so nothing is
        # left to pan. Clipping .econ-landing or .container would cancel the
        # breakout itself.
        self.assertTrue(any(s == "main" and declares(b, "overflow-x") == "clip" for s, b in RULES))
        self.assertFalse(any(s in ("body", "html") and re.search(r"overflow", b) for s, b in RULES))
        for selector, body in RULES:
            if selector in (".econ-landing", ".container") or selector.endswith(" .container"):
                self.assertNotRegex(body, r"(?<![\w-])overflow(-x|-y)?\s*:", selector)
        band = [b for s, b in RULES if s == ".econ-landing .band"]
        self.assertTrue(any("margin-inline: calc(50% - 50vw)" in b for b in band))

    def test_the_layout_tiers_follow_ctfds_container(self):
        # CTFd's .container is 540px wide from 576 to 767.98px, so the phone
        # tier runs to Bootstrap's md step; the column itself lines up with
        # the navbar through the container's own gutter.
        media = {conditions[0] for conditions, _, _ in RULES_IN_CONTEXT if conditions and conditions[0].startswith("@media")}
        self.assertIn("@media (max-width: 767.98px)", media)
        self.assertIn("@media (min-width: 768px)", media)
        self.assertFalse(any(re.search(r"\b64[01]px", m) for m in media), media)
        # Bootstrap's own .98 edges, so a fractional width (zoom, OS scaling)
        # between two tiers still gets one of them.
        self.assertFalse(any(re.search(r"\b(?:767|991)px", m) for m in media), media)
        wrap = [b for c, s, b in RULES_IN_CONTEXT if s == ".econ-landing .wrap" and not c]
        self.assertEqual(len(wrap), 1)
        self.assertIsNone(declares(wrap[0], "max-width"))
        self.assertEqual(declares(wrap[0], "padding-inline"), "calc(var(--bs-gutter-x, 1.5rem) * .5)")
        # The stacked toy stops at 480px only in the 720px container; under
        # 768px it runs the column's full width, as 로그인 above it does.
        caps = [c for c, s, b in RULES_IN_CONTEXT if s == ".econ-landing .toycol" and declares(b, "max-width")]
        self.assertEqual(caps, [("@media (min-width: 768px) and (max-width: 991.98px)",)])

    def test_the_narrow_rail_is_left_out_with_nothing_to_caption(self):
        # 준비 모드 and 일정 설정 필요 have no current segment. Before the first
        # phase the rail only holds its place (visibility, not display, so the
        # phase that arrives moves nothing), and without JavaScript it goes.
        tablet = ("@media (max-width: 991.98px)",)
        hidden = {s for c, s, b in RULES_IN_CONTEXT
                  if c == tablet and s.endswith(" .rail") and declares(b, "display") == "none"}
        self.assertEqual(hidden, {
            '.econ-landing [data-phase="open"] .rail',
            '.econ-landing [data-phase="misconfigured"] .rail',
        })
        held = [b for c, s, b in RULES_IN_CONTEXT if c == tablet and s == ".econ-landing .stage:not([data-phase]) .rail"]
        self.assertEqual([declares(b, "visibility") for b in held], ["hidden"])
        self.assertIsNone(declares(held[0], "display"))
        no_js = [s for c, s, b in RULES_IN_CONTEXT
                 if c == ("@media (max-width: 991.98px) and (scripting: none)",) and declares(b, "display") == "none"]
        self.assertEqual(no_js, [".econ-landing .rail"])


class NumbersTests(unittest.TestCase):
    r1_min = minutes(competition.ROUND_1_DURATION)
    gap_min = minutes(competition.BREAK_DURATION)
    r2_min = minutes(competition.ROUND_2_DURATION)
    r1_n = len(competition.ROUND_1_CHALLENGE_IDS)
    r2_n = len(competition.ROUND_2_CHALLENGE_IDS)
    total_n = len(competition.ALL_CHALLENGE_IDS)
    r1_pt = competition.ROUND_INFO["round1"]["points"]
    r2_pt = competition.ROUND_INFO["round2"]["points"]
    # The 곧 마감 tail: landing.js's largest "remaining <= N" is its data-tail test.
    tail_min = max(int(n) for n in re.findall(r"remaining\s*<=\s*(\d+)", LANDING_JS)) // 60

    def assertStated(self, phrase):
        self.assertIn(phrase, TEXT)

    def test_the_schedule(self):
        self.assertStated(f"1라운드 진행 중 · {self.r1_min}분")
        self.assertStated(f"2라운드 진행 중 · {self.r2_min}분")
        self.assertStated(f"1라운드 · {self.r1_min}분 · {self.r1_n}문제 {self.r1_pt}점")
        self.assertStated(f"2라운드 · {self.r2_min}분 · {self.r2_n}문제 {self.r2_pt}점")
        self.assertStated(f"{self.r1_min}분 동안 {self.r1_n}문제 · {self.r1_pt}점")
        self.assertStated(f"{self.r2_min}분 동안 {self.r2_n}문제 · {self.r2_pt}점")
        # the rail is drawn to the real proportions, from one copy
        self.assertEqual(re.findall(r"(\d+)fr (\d+)fr (\d+)fr", STYLE),
                         [(str(self.r1_min), str(self.gap_min), str(self.r2_min))])
        total = one("data-econ-clock", "total")
        self.assertEqual(
            {"round1": f"{self.r1_min}분", "round2": f"{self.r2_min}분"},
            {next(c for c in ("round1", "round2") if c in e.classes()): e.text() for e in total.walk()},
        )
        self.assertEqual(TEXT.count(f"{self.tail_min}분 안에 마감"), 2)

    def test_every_count_score_and_duration_is_one_the_code_uses(self):
        # Exhaustive, so a copy left behind by a schedule change fails even
        # while a correct copy elsewhere still passes the phrases above.
        total_pt = self.r1_pt + self.r2_pt
        self.assertEqual({int(n) for n in re.findall(r"(\d+)문제", TEXT)}, {self.r1_n, self.r2_n, self.total_n})
        # 0 is 부분 통과는 0점
        self.assertEqual({int(n) for n in re.findall(r"(?<![\d+])(\d+)점", TEXT)}, {0, self.r1_pt, self.r2_pt, total_pt})
        # 0분 and 1분 are the ruler's own labels
        self.assertLessEqual({int(n) for n in re.findall(r"(?<![\d.])(\d+)분", TEXT)},
                             {0, 1, self.tail_min, self.r1_min, self.r2_min, self.r1_min + self.r2_min})
        self.assertNotIn(" pt", TEXT)

    def test_each_phase_quotes_its_own_round(self):
        rounds = {"before": 1, "round1": 1, "break": 2, "round2": 2}
        own = {1: (self.r1_n, self.r1_pt, self.r1_min), 2: (self.r2_n, self.r2_pt, self.r2_min)}
        checked = 0
        for e in ELEMENTS:
            cls = e.classes()
            bound = {rounds[c] for c in cls & rounds.keys()}
            if "ph" not in cls or len(bound) != 1:
                continue
            count, points, span = own[bound.pop()]
            text = squash(e.text())
            with self.subTest(cls=sorted(cls), text=text[:30]):
                for n in re.findall(r"(\d+)문제", text):
                    self.assertEqual(int(n), count)
                for n in re.findall(r"(?<![\d+])(\d+)점", text):
                    self.assertEqual(int(n), points)
                for n in re.findall(r"(\d+)분", text):
                    self.assertEqual(int(n), self.tail_min if "tail" in cls else span)
                checked += 1
        self.assertGreater(checked, 20)
        # 시작 전's lede has no .ph (it only shows in before) and names round 1
        waiting = next(e for e in with_classes("au-inline")[0].walk() if "lede" in e.classes())
        self.assertIn(f"{self.r1_min}분 동안 {self.r1_n}문제 · {self.r1_pt}점", squash(waiting.text()))

    def test_the_score_table_and_the_strip(self):
        rows = [[squash(cell.text()) for cell in row.walk() if cell.tag in ("th", "td")]
                for row in ELEMENTS if row.tag == "tr"]
        self.assertEqual(rows[1:], [
            ["1라운드", f"{self.r1_min}분", f"{self.r1_n}문제", f"{self.r1_pt}점"],
            ["2라운드", f"{self.r2_min}분", f"{self.r2_n}문제", f"{self.r2_pt}점"],
            ["온라인 합계", f"{self.r1_min + self.r2_min}분", f"{self.total_n}문제",
             f"{self.r1_pt + self.r2_pt}점"],
        ])
        self.assertStated(f"{self.total_n}문제 · {self.r1_pt + self.r2_pt}점")
        self.assertStated(f"{self.r1_pt + self.r2_pt}점 만점")

    def test_the_per_problem_points(self):
        values = {cid: value for cid, _name, _cat, value, *_ in register.CHALLENGES}
        r1 = "+".join(str(values[i]) for i in sorted(competition.ROUND_1_CHALLENGE_IDS))
        r2 = "+".join(str(values[i]) for i in sorted(competition.ROUND_2_CHALLENGE_IDS))
        self.assertStated(f"1라운드 {r1} · 2라운드 {r2}")

    def test_the_upload_limits(self):
        endpoints = endpoints_module()
        kb, rest = divmod(endpoints.MAX_UPLOAD_BYTES, 1024)
        mb, mb_rest = divmod(endpoints.MAX_BUNDLE_BYTES, 1024 * 1024)
        self.assertEqual((rest, mb_rest), (0, 0), "a limit the page can no longer state in whole units")
        files = endpoints.MAX_BUNDLE_FILES
        self.assertStated(f"{kb} KB / 파일")
        self.assertStated(f"파일 하나 {kb} KB · 한 폴더에 .dig {files}개 · 폴더 전체 {mb} MB")
        self.assertStated(f"파일 하나 {kb} KB, 한 폴더에 .dig {files}개 · {mb} MB를 넘기면")
        # Every whole-number KB on the page is the per-file limit (the
        # figure's decimal size is the example folder's, checked below).
        self.assertEqual({int(n) for n in re.findall(r"(?<![\d.])(\d+) KB", TEXT)}, {kb})

    def test_the_grading_timeout(self):
        self.assertStated(f"채점은 {grader.TIMEOUT_SEC}초에서 끊깁니다")

    def test_the_quoted_sentences_are_the_real_ones(self):
        # The page quotes, in the failure list's order, the sentence the
        # problem page really shows for each item, and says where it shows:
        # the result card, or the strip under the folder picker (view.js
        # checks the answer file before anything is uploaded).
        endpoints = endpoints_module()
        quoted = []
        for span in (e for e in ELEMENTS if e.tag == "span" and e.parent.tag == "div" and "t" in e.parent.classes()):
            where = [c for c in span.children if isinstance(c, Node) and c.tag == "i"]
            self.assertEqual(len(where), 1, squash(span.text()))
            quoted.append((squash(where[0].text()), squash(span.text())[len(squash(where[0].text())):].strip()))
        component = grader._incomplete_class_message("FA #001: Component 1-1번_반가산기.dig not found")
        pin = grader._incomplete_class_message("XOR #001: Test signal Y not found in the circuit!")
        answer = PurePosixPath(problemset.HWP_STARTER_FILES[3]).name
        with tempfile.TemporaryDirectory() as tmp:
            wide = Path(tmp) / "wide.dig"
            wide.write_text(
                '<?xml version="1.0" encoding="utf-8"?><circuit><visualElements><visualElement>'
                "<elementName>And</elementName><elementAttributes><entry><string>Inputs</string>"
                "<int>3</int></entry></elementAttributes><pos x=\"0\" y=\"0\"/></visualElement>"
                "</visualElements><wires/></circuit>",
                encoding="utf-8",
            )
            fan_in = grader._validate_structure(2, str(wide))
        self.assertEqual(quoted, [
            ("결과 카드", component),
            ("폴더를 고를 때", f"선택한 폴더에 이 문제의 답안 파일({answer})이 없습니다."),
            ("결과 카드", pin),
            ("결과 카드", fan_in),
            ("결과 카드", endpoints._ERROR_MESSAGES["timeout"]),
        ])
        # Both halves around the file name, in the browser's copy and the
        # server's, however either is spelled around them.
        source = (ROOT / "econ_judge" / "endpoints.py").read_text(encoding="utf-8")
        for copy in (VIEW_JS, source):
            self.assertIn("선택한 폴더에 이 문제의 답안 파일(", copy)
            self.assertIn(")이 없습니다.", copy)

    def test_the_fan_in_rule_is_stated_as_the_grader_applies_it(self):
        # Every problem, and every .dig in the chosen folder: one wide gate
        # in another problem's file turns a correct answer down.
        gates = "·".join(sorted(name.upper() for name in grader._FAN_IN_GATES))
        stated = re.search(r"([A-Z]+(?:·[A-Z]+)+)는 모든 문제에서 2입력만", TEXT)
        self.assertIsNotNone(stated)
        self.assertEqual("·".join(sorted(stated.group(1).split("·"))), gates)
        answer = SOLUTIONS / PurePosixPath(problemset.HWP_STARTER_FILES[EXAMPLE_ID])
        part = SOLUTIONS / PurePosixPath(problemset.HWP_STARTER_FILES[EXAMPLE_PART_ID])
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp) / "다른문제.dig"
            other.write_text(
                '<?xml version="1.0" encoding="utf-8"?><circuit><visualElements><visualElement>'
                "<elementName>Or</elementName><elementAttributes><entry><string>Inputs</string>"
                "<int>3</int></entry></elementAttributes><pos x=\"0\" y=\"0\"/></visualElement>"
                "</visualElements><wires/></circuit>",
                encoding="utf-8",
            )
            self.assertIsNone(grader._validate_structure(EXAMPLE_ID, str(answer), (str(part),)))
            self.assertIn("입력이 2개보다 많은", grader._validate_structure(EXAMPLE_ID, str(answer), (str(part), str(other))))
        self.assertStated("고른 폴더의 .dig를 모두 검사")

    def test_the_example_folder_is_one_the_grader_accepts(self):
        # The figure is the page's only example submission. Its answer is a
        # problem with no component rules of its own, it lists every file
        # that answer needs, and its caption reads the way the upload box
        # would caption the reference files (the files
        # tests/canonical_self_test.py grades with Digital.jar, there inside
        # their full round folder).
        self.assertNotIn(EXAMPLE_ID, grader._STRICT_COMPONENTS)
        self.assertNotEqual(EXAMPLE_ID, 15)
        self.assertNotIn(EXAMPLE_PART_ID, grader._STRICT_COMPONENTS)
        names = {cid: PurePosixPath(problemset.HWP_STARTER_FILES[cid]).name for cid in (EXAMPLE_ID, EXAMPLE_PART_ID)}
        for cid in names:
            self.assertTrue((ROOT / "econ_judge" / "assets" / "starters" / PurePosixPath(problemset.HWP_STARTER_FILES[cid])).is_file())
        tree = with_classes("tree")[0]
        rows = [(squash(r.children[0].text() if isinstance(r.children[0], Node) else ""), squash(n.text()))
                for r in tree.children if isinstance(r, Node)
                for n in r.children if isinstance(n, Node) and n.tag == "em"]
        self.assertEqual([note for _, note in rows], ["← 폴더 이름은 자유", "← 답안", "← 사용한 부품"])
        self.assertEqual(rows[1][0], f"├─ {names[EXAMPLE_ID]}")
        self.assertEqual(rows[2][0], f"└─ {names[EXAMPLE_PART_ID]}")
        answer = SOLUTIONS / PurePosixPath(problemset.HWP_STARTER_FILES[EXAMPLE_ID])
        part = SOLUTIONS / PurePosixPath(problemset.HWP_STARTER_FILES[EXAMPLE_PART_ID])
        self.assertEqual(dig_components(answer), {names[EXAMPLE_PART_ID]})
        self.assertEqual(dig_components(part), set())
        self.assertIsNone(grader._validate_structure(EXAMPLE_ID, str(answer), (str(part),)))
        caption = squash(with_classes("figcap")[0].text())
        self.assertEqual(caption, f"제출 폴더의 예{human_size(lf_size(answer) + lf_size(part))} · 2개 .dig 파일")

    def test_the_other_rules_and_the_stack(self):
        self.assertStated(f"{len(DEMO_TEAMS)}개 조")
        self.assertStated(f"#{problemset.TRUTH_TABLE_CHALLENGE_ID:02d} · 제출 기회 1회")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        ctfd = re.search(r"^ENV CTFD_VERSION=(\S+)", dockerfile, re.M).group(1)
        digital = re.search(r"^ENV DIGITAL_VERSION=(\S+)", dockerfile, re.M).group(1)
        self.assertStated(f"CTFd {ctfd} · Digital.jar {digital}")
        self.assertEqual(set(re.findall(r"Digital\.jar (v[\d.]+)", TEXT)), {digital})


class LandingJsContractTests(unittest.TestCase):
    """Every name landing.js writes has a reader in INDEX_CONTENT."""

    def written(self, helper):
        return set(re.findall(rf'\b{helper}\("([^"]+)"', LANDING_JS))

    def test_the_stage_attributes(self):
        attrs = self.written("attr")
        self.assertEqual(attrs, {"data-auth", "data-phase", "data-tail"})
        for name in attrs:
            with self.subTest(attr=name):
                self.assertRegex(STYLE, rf"\[{re.escape(name)}[\]=]")
        tails = [s for s in revealing("tail") if '[data-tail="1"]' in s]
        self.assertEqual({phase_in(s) for s in tails}, {"round1", "round2"})
        for phase in ("round1", "round2"):
            for kind in ("chip", "lede"):
                selector = f'.econ-landing [data-tail="1"][data-phase="{phase}"] .{kind}.ph.{phase}:not(.tail)'
                self.assertTrue(any(s == selector and declares(b, "display") == "none" for s, b in RULES), selector)
        # Equal specificity, so the default must come after the phase reveal
        # or the 곧 마감 copy shows at 42:17.
        order = [s for s, _ in RULES]
        self.assertGreater(order.index(".econ-landing [data-phase] .ph.tail"),
                           order.index('.econ-landing [data-phase="round1"] .ph.round1'))

    def test_the_custom_properties(self):
        # Whole names: a reader renamed to --elapsed or --major-ticks still
        # starts with the name landing.js writes, and reads nothing it does.
        def reads(selector):
            bodies = [b for s, b in RULES if s == selector]
            self.assertTrue(bodies, selector)
            return set().union(*(re.findall(r"var\(\s*(--[\w-]+)", b) for b in bodies))

        props = self.written("prop")
        self.assertEqual(props, {"--el", "--ticks", "--major"})
        self.assertLessEqual({"--ticks", "--major"}, reads(".econ-landing .ruler i"))
        self.assertIn("--el", reads(".econ-landing .ruler i.el"))
        self.assertTrue(any(declares(b, "--seg-fill") == "var(--el, 0%)" for _, b in RULES))

    def test_the_tail_announcement_is_the_tail_ledes_own_sentence(self):
        # A page can first see a round already in its tail, so what the
        # status line says there has to hold for any time left under five
        # minutes, as the visible lede does.
        tail = re.search(r"const TAIL = \{([^}]*)\}", LANDING_JS)
        self.assertIsNotNone(tail)
        said = dict(re.findall(r'(round\d):\s*"([^"]+)"', tail.group(1)))
        self.assertEqual(set(said), {"round1", "round2"})
        for phase, sentence in said.items():
            with self.subTest(phase=phase):
                lede = with_classes("lede", "ph", phase, "tail")
                self.assertEqual(len(lede), 1)
                self.assertTrue(squash(lede[0].text()).startswith(sentence), sentence)

    def test_the_clock_classes(self):
        flags = self.written("flag")
        self.assertEqual(flags, {"is-warn", "is-crit", "done", "is-long"})
        # flag() writes to the clock's id; the page's rules key on .clock.
        self.assertIn("clock", one("id", "econ-landing-clock").classes())
        base = declares(dict(RULES)[".econ-landing .clock"], "color")
        for name in flags:
            with self.subTest(flag=name):
                rules = [b for s, b in RULES if {"clock", name} <= subject_classes(s)]
                self.assertTrue(rules)
                wanted = "font-size" if name == "is-long" else "color"
                self.assertTrue(any(declares(b, wanted) and declares(b, wanted) != base for b in rules))

    def test_the_slots_and_the_ids(self):
        slots = re.search(r"const SLOTS = \[([^\]]*)\]", LANDING_JS)
        self.assertIsNotNone(slots)
        names = set(re.findall(r'"([\w-]+)"', slots.group(1)))
        self.assertEqual(names, {e.attrs["data-econ-clock"] for e in by_attr("data-econ-clock")})
        if "data-econ-user" in LANDING_JS:
            one("data-econ-user")
        ids = {e.attrs["id"] for e in by_attr("id")}
        for wanted in re.findall(r'getElementById\("([\w-]+)"\)', LANDING_JS) + re.findall(r'"(econ-[\w-]+)"', LANDING_JS):
            if wanted.startswith("econ-") and wanted not in ("econ-round-countdown",):
                with self.subTest(id=wanted):
                    self.assertIn(wanted, ids)
        classes = set().union(*(e.classes() for e in ELEMENTS))
        for selector in re.findall(r'querySelector(?:All)?\("([^"]+)"\)', LANDING_JS):
            for token in re.findall(r"\.([\w-]+)", re.sub(r"\[[^\]]*\]", "", selector)):
                with self.subTest(selector=selector):
                    self.assertIn(token, classes)


class AccessibilityTests(unittest.TestCase):
    def test_the_filled_buttons_ring_is_solid(self):
        # The page's 3px halo is under 3:1 against a blue button.
        order = [s for s, _ in RULES]
        ring = dict(RULES)[".econ-landing .lbtn.primary:focus-visible"]
        self.assertIn("var(--d-accent)", declares(ring, "box-shadow"))
        self.assertGreater(order.index(".econ-landing .lbtn.primary:focus-visible"),
                           order.index(".econ-landing .lbtn:focus-visible"))

    def test_the_blue_run_button_keeps_its_focus_ring(self):
        # Signed in before the start, the toy's 채점 is blue, and the rule
        # that makes it blue outranks the toy's own focus ring.
        blue = [s for s, b in RULES if '[data-phase="before"]' in s and ".run:not(:checked)" in s
                and ":focus-visible" not in s and ":hover" not in s and declares(b, "box-shadow")]
        rings = [s for s, b in RULES if '[data-phase="before"]' in s and ":focus-visible" in s
                 and "var(--d-accent)" in (declares(b, "box-shadow") or "")]
        self.assertTrue(blue and rings)
        self.assertGreater(max(specificity(s) for s in rings), max(specificity(s) for s in blue))

    def test_focus_and_the_chosen_answer_survive_forced_colours(self):
        # Windows High Contrast drops box-shadow, so no rule may take the
        # outline away, and every halo ring carries an outline of its own.
        self.assertNotRegex(STYLE, r"outline\s*:\s*none")
        for selector, body in RULES:
            if ":focus-visible" in selector and "var(--d-focus)" in (declares(body, "box-shadow") or ""):
                with self.subTest(selector=selector):
                    self.assertTrue(declares(body, "outline"))
        forced = [(s, b) for c, s, b in RULES_IN_CONTEXT if c == ("@media (forced-colors: active)",)]
        self.assertEqual([(s, declares(b, "--d-accent")) for s, b in forced], [(".econ-landing .toy .rad", "Highlight")])
        chosen = [b for s, b in RULES if s.endswith(" .tb .r0 .l0") and ".i0.o0:checked" in s]
        self.assertTrue(chosen and all(declares(b, "forced-color-adjust") == "none" for b in chosen))

    def test_the_toy_comes_before_the_action_in_the_markup(self):
        # Never shown together, so the order changes nothing on screen; but
        # when a round opens under focus in the toy, the browser's next Tab
        # starts from where the toy was, and lands on the action.
        hero = with_classes("hero")[0]
        columns = [c for c in hero.children if isinstance(c, Node)]
        order = [next(k for k in ("clockcol", "toycol", "actcol") if k in c.classes()) for c in columns]
        self.assertEqual(order, ["clockcol", "toycol", "actcol"])

    def test_the_verdict_is_a_live_region_that_is_always_rendered(self):
        # A status region that itself goes from display:none to block
        # announces nothing; only the cards inside it toggle.
        wrap = with_classes("vwrap")
        self.assertEqual(len(wrap), 1)
        self.assertEqual(wrap[0].attrs.get("role"), "status")
        for selector, body in RULES:
            if "vwrap" in subject_classes(selector):
                self.assertIsNone(declares(body, "display"), selector)

    def test_every_toy_input_is_pinned_to_its_stand_in(self):
        supported = [(s, b) for c, s, b in RULES_IN_CONTEXT if any(x.startswith("@supports (anchor-name") for x in c)]
        anchors = {s: declares(b, "anchor-name") for s, b in supported if declares(b, "anchor-name")}
        pinned = {s: declares(b, "position-anchor") for s, b in supported if declares(b, "position-anchor")}
        for n in range(8):
            with self.subTest(row=n):
                self.assertEqual(pinned[f".econ-landing .toy .i{n}"], anchors[f".econ-landing .toy .r{n} .rad"])
        self.assertEqual(pinned[".econ-landing .toy .run"], anchors[".econ-landing .toy [data-toy-run]"])
        self.assertEqual(len(set(anchors.values())), 9)
        radios = {e.attrs["id"] for e in ELEMENTS if e.tag == "input"}
        rows = {f"i{n}" for n in range(8)}
        for e in ELEMENTS:
            if e.tag == "input" and e.attrs.get("type") == "radio":
                self.assertEqual(len(e.classes() & rows), 1, e.attrs["id"])
        self.assertIn("econ-toy-run", radios)

    def test_reduced_motion_stops_movement_but_keeps_states(self):
        reduced = [(s, b) for c, s, b in RULES_IN_CONTEXT if c == ("@media (prefers-reduced-motion: reduce)",)]
        self.assertTrue(reduced)
        for selector, body in reduced:
            if selector.startswith(".econ-landing *"):
                self.assertIn("animation: none !important", body)
                self.assertIn("transition: none !important", body)
                self.assertIsNone(declares(body, "transform"), "the open chevron's rotation is a state")
        self.assertIn((".econ-landing .lbtn:active", "none"),
                      [(s, declares(b, "transform")) for s, b in reduced])
        self.assertEqual(declares(dict(RULES)[".econ-landing details.raw[open] summary svg.ic"], "transform"), "rotate(180deg)")

    def test_screen_readers_skip_the_decoration(self):
        self.assertEqual(with_classes("sep")[0].attrs.get("aria-hidden"), "true")
        pickers = with_classes("rad")
        self.assertEqual(len(pickers), 8)
        self.assertTrue(all(p.attrs.get("aria-hidden") == "true" for p in pickers))
        # the radios the pickers stand for stay exposed
        for e in ELEMENTS:
            if e.tag == "input":
                self.assertFalse(any(a.attrs.get("aria-hidden") == "true" for a in e.ancestors()), e.attrs.get("id"))
                self.assertTrue(e.attrs.get("aria-label"))
        # only the resting dot is silent; a graded ✓ / ✗ is still read
        mark = dict(RULES)[".econ-landing .toy .mk::after"]
        self.assertEqual(declares(mark, "content"), 'var(--g, "·" / "")')


class ThemeHeaderTests(unittest.TestCase):
    TAG = '<script defer src="/plugins/econ_judge/assets/{}"></script>'

    def test_landing_js_loads_after_round_ui(self):
        # defer keeps document order, so window.econRoundState already exists
        # when landing.js runs and it can share round-ui.js's poll.
        round_ui = THEME.find(self.TAG.format("round-ui.js"))
        landing = THEME.find(self.TAG.format("landing.js"))
        self.assertGreaterEqual(round_ui, 0)
        self.assertGreater(landing, round_ui)
        self.assertEqual(THEME.count("assets/landing.js"), 1)
        self.assertTrue((ROOT / "econ_judge" / "assets" / "landing.js").is_file())

    def test_no_landing_class_is_one_the_theme_styles(self):
        # Several of the design's class names were Bootstrap's (.btn, .card,
        # .mark) and CTFd's core CSS styles those with rules that outrank a
        # scoped selector; the theme is the part of that surface this repo owns.
        used = set().union(*(e.classes() for e in ELEMENTS))
        self.assertEqual(used & css_class_tokens(theme_css()), set())
        self.assertEqual(used & {"btn", "card", "mark", "nav", "small", "active", "show", "collapse"}, set())

    def test_the_clock_alarm_classes_only_skin_the_navbar_pill_in_the_theme(self):
        # landing.js puts .is-warn / .is-crit on the hero clock; the theme's
        # rules for them must stay keyed on the pill's id.
        for selector, _ in css_rules(theme_css()):
            if re.search(r"\.is-(warn|crit)\b", selector):
                self.assertIn("#econ-round-countdown", selector)

    def test_every_mono_weight_the_page_uses_is_loaded(self):
        # The last minute is 600, and a weight the font request leaves out is
        # drawn as a smeared faux bold at 130px.
        loaded = re.search(r"family=Geist\+Mono:wght@(\d+)\.\.(\d+)", THEME)
        self.assertIsNotNone(loaded)
        low, high = int(loaded.group(1)), int(loaded.group(2))
        crit = declares(dict(RULES)[".econ-landing .clock.is-crit"], "font-weight")
        self.assertTrue(low <= int(crit) <= high, crit)

    def test_the_navbar_uses_the_pages_name_for_challenges(self):
        # CTFd's translation says 챌린지; the plugin and the page say 도전 과제.
        # Only after the link is known to exist, however the line is spelled.
        self.assertRegex(
            THEME, r"if\s*\(\s*!chalLink\s*\)\s*return;[\s\S]{0,200}?chalLink\.textContent\s*=\s*['\"]도전 과제['\"]"
        )
        self.assertIn("도전 과제로", TEXT)
        self.assertNotIn("챌린지", TEXT)


if __name__ == "__main__":
    unittest.main()
