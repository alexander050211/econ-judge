"""How bin/bootstrap.py deletes demo solves, against CTFd's real table shape.

``_clear_demo_data`` runs on every camp-day boot (CTFD_DEMO_DATA=false), and it
deletes from ``Solves`` — which in CTFd is JOINED-TABLE INHERITANCE: a parent
``submissions`` table carrying the polymorphic ``type`` discriminator and the
``provided`` column, and a child ``solves`` table whose id is a foreign key
back to it. The marker the demo seed writes lives in ``provided``, i.e. on the
PARENT, so a filter on it makes any bulk ``Query.delete()`` a multiple-table
DELETE. That statement does not survive either database this project runs on:

* SQLite (the free-tier default, and the camp-day config) refuses to compile it
  at all — ``NotImplementedError`` before a single row is read, on every boot
  including an empty one, so the container never finishes bootstrapping; and
* PostgreSQL compiles it into ``DELETE FROM solves USING submissions`` with no
  join predicate — a cross product that deletes every real solve while leaving
  the marked parent rows, and hence the marker, behind.

Neither is reachable from a unit test that imports bootstrap.py: the file does
``from CTFd.models import ...`` at module scope and CTFd is not vendored here.
So this module does two separate things instead, and it is worth being blunt
about which is which:

* ``ClearDemoDataSourceTests`` reads the shipped function with ``ast`` — that
  is the half actually bound to bin/bootstrap.py, and it is what fails if the
  bulk delete ever comes back.
* ``JoinedInheritanceDeleteTests`` runs both deletion strategies against
  models built here to CTFd 3.8.5's shape on in-memory SQLite. ``clear_demo``
  below MIRRORS ``_clear_demo_data``'s statements; it does not import them.
  The source tests are what keep the mirror honest.

SQLAlchemy is not in requirements-dev.txt, so the second half skips where it is
absent. The first half needs nothing but the standard library.
"""

from __future__ import annotations

import ast
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP = ROOT / "bin" / "bootstrap.py"
BOOTSTRAP_TREE = ast.parse(BOOTSTRAP.read_text(encoding="utf-8"))

try:
    from sqlalchemy import (
        Column,
        DateTime,
        ForeignKey,
        Integer,
        String,
        Text,
        create_engine,
        text,
    )
    from sqlalchemy.orm import column_property, declarative_base, sessionmaker
except ImportError:  # pragma: no cover - depends on the interpreter, not the code
    HAVE_SQLALCHEMY = False
else:
    HAVE_SQLALCHEMY = True

SKIP_REASON = "SQLAlchemy is not installed (it is not in requirements-dev.txt)"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Imports nothing from CTFd, so it loads directly; only the starter filenames
# are read from it, to check the marker cannot collide with a real submission.
problemset = load("bootstrap_problemset", ROOT / "econ_judge" / "problemset.py")


def bootstrap_constant(name: str):
    """Read a module-level string constant out of bin/bootstrap.py's source."""
    for node in BOOTSTRAP_TREE.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"bin/bootstrap.py no longer defines {name}")


def bootstrap_function(name: str) -> ast.FunctionDef:
    for node in ast.walk(BOOTSTRAP_TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"bin/bootstrap.py no longer defines {name}()")


# Taken from the shipped file rather than repeated here, so a rename of the
# marker cannot leave this suite testing a string nothing writes any more.
DEMO_SOLVE_MARKER = bootstrap_constant("DEMO_SOLVE_MARKER")


def receiver(node: ast.Attribute) -> str:
    """Dotted spelling of what an attribute is being read off, e.g. "db.session"
    for ``db.session.delete``. Returns "" for anything that is not a plain
    chain of names."""
    parts = []
    current = node.value
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return ""
    parts.append(current.id)
    return ".".join(reversed(parts))


def delete_call_receivers(tree: ast.AST) -> list:
    return [
        receiver(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "delete"
    ]


class ClearDemoDataSourceTests(unittest.TestCase):
    """The half that is actually bound to bin/bootstrap.py."""

    def setUp(self):
        self.function = bootstrap_function("_clear_demo_data")

    def test_the_only_delete_is_the_per_object_one(self):
        # `Solves.query.filter_by(...).delete()` and `session.query(Solves)
        # .filter_by(...).delete()` both read as a `.delete` on something other
        # than `db.session`, and both are the multiple-table DELETE this
        # module's docstring is about. JoinedInheritanceDeleteTests below shows
        # what each database does with one.
        receivers = delete_call_receivers(self.function)
        self.assertTrue(receivers, "_clear_demo_data no longer deletes anything")
        self.assertEqual(set(receivers), {"db.session"})

    def test_the_delete_runs_once_per_row(self):
        # A `db.session.delete(obj)` outside a loop would be a single row, i.e.
        # the marked solves would only ever be partly cleared.
        in_a_loop = any(
            delete_call_receivers(loop)
            for loop in ast.walk(self.function)
            if isinstance(loop, (ast.For, ast.AsyncFor))
        )
        self.assertTrue(in_a_loop)

    def test_only_rows_carrying_the_demo_marker_are_selected(self):
        marker_filters = [
            node
            for node in ast.walk(self.function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "filter_by"
            and [
                keyword
                for keyword in node.keywords
                if keyword.arg == "provided"
                and isinstance(keyword.value, ast.Name)
                and keyword.value.id == "DEMO_SOLVE_MARKER"
            ]
        ]
        self.assertEqual(len(marker_filters), 1)

    def test_a_failure_here_cannot_stop_the_boot(self):
        # This runs during bootstrap on camp morning. An exception escaping it
        # takes the whole container down over cosmetic scoreboard rows.
        self.assertTrue(
            any(isinstance(node, ast.Try) for node in self.function.body),
            "_clear_demo_data's body is no longer wrapped in try/except",
        )

    def test_the_marker_cannot_match_a_real_submission(self):
        # A real Solve records the uploaded answer file's basename in
        # `provided`; the marker has to stay outside that set for "delete only
        # the marked rows" to mean anything.
        basenames = {name.rsplit("/", 1)[-1] for name in problemset.HWP_STARTER_FILES.values()}
        self.assertNotIn(DEMO_SOLVE_MARKER, basenames)


def ctfd_shaped_models():
    """CTFd 3.8.5's Submissions/Solves/Fails shape, and only the parts of it
    that decide how a DELETE compiles.

    Faithful in the three respects that matter: `submissions` is the single
    base table and carries both the polymorphic discriminator and `provided`;
    `solves` is a child table joined by a primary-key foreign key; and
    challenge_id/user_id are `column_property` pairs spanning both tables,
    which is why "filter a Solve by a submissions column" is such an easy thing
    to write. Fails is single-table (no `__tablename__`), as it is in CTFd.
    """
    Base = declarative_base()

    class Submissions(Base):
        __tablename__ = "submissions"
        id = Column(Integer, primary_key=True)
        challenge_id = Column(Integer)
        user_id = Column(Integer)
        ip = Column(String(46))
        provided = Column(Text)
        type = Column(String(32))
        date = Column(DateTime)
        __mapper_args__ = {
            "polymorphic_identity": "submission",
            "polymorphic_on": type,
        }

    class Solves(Submissions):
        __tablename__ = "solves"
        id = Column(
            None, ForeignKey("submissions.id", ondelete="CASCADE"), primary_key=True
        )
        challenge_id = column_property(Column(Integer), Submissions.challenge_id)
        user_id = column_property(Column(Integer), Submissions.user_id)
        __mapper_args__ = {"polymorphic_identity": "correct"}

    class Fails(Submissions):
        __mapper_args__ = {"polymorphic_identity": "incorrect"}

    return Base, Submissions, Solves, Fails


def clear_demo(session, Solves):
    """MIRROR of bin/bootstrap.py's `_clear_demo_data`, not an import of it.

    bootstrap.py binds CTFd at module scope, so the function itself cannot be
    reached from here; ClearDemoDataSourceTests is what asserts the shipped
    file still has these statements. `session.query(Solves)` is spelled where
    bootstrap writes `Solves.query` — Flask-SQLAlchemy's `.query` is that same
    session query, and only the deletion strategy is under test.
    """
    doomed = session.query(Solves).filter_by(provided=DEMO_SOLVE_MARKER).all()
    for solve in doomed:
        session.delete(solve)
    session.commit()
    return len(doomed)


def clear_demo_bulk(session, Solves):
    """The statement `_clear_demo_data` used to carry, kept so the failure it
    caused stays reproducible rather than only described in a comment."""
    return (
        session.query(Solves)
        .filter_by(provided=DEMO_SOLVE_MARKER)
        .delete(synchronize_session=False)
    )


@unittest.skipUnless(HAVE_SQLALCHEMY, SKIP_REASON)
class JoinedInheritanceDeleteTests(unittest.TestCase):
    # A real camp-day scoreboard row and a fabricated one, told apart by
    # `provided` alone — exactly as bootstrap tells them apart.
    REAL_ANSWER = "1-1번_반가산기(Half Adder)만들기.dig"

    def setUp(self):
        _, self.Submissions, self.Solves, self.Fails = ctfd_shaped_models()
        engine = create_engine("sqlite://")
        self.Submissions.metadata.create_all(engine)
        self.session = sessionmaker(bind=engine)()
        self.addCleanup(self.session.close)

        self.session.add_all(
            [
                self.Solves(challenge_id=1, user_id=1, provided=DEMO_SOLVE_MARKER),
                self.Solves(challenge_id=2, user_id=1, provided=DEMO_SOLVE_MARKER),
                self.Solves(challenge_id=9, user_id=2, provided=self.REAL_ANSWER),
                self.Solves(challenge_id=10, user_id=3, provided=self.REAL_ANSWER),
                self.Fails(challenge_id=9, user_id=3, provided=self.REAL_ANSWER),
            ]
        )
        self.session.commit()

    def rows(self, table):
        return self.session.execute(text(f"select count(*) from {table}")).scalar()

    def provided_values(self):
        return sorted(
            row[0]
            for row in self.session.execute(text("select provided from submissions"))
        )

    def test_the_fixture_reproduces_the_column_split_that_causes_this(self):
        # If `provided` ever appeared on the child table too, every assertion
        # below would still pass while testing nothing.
        self.assertIn("provided", self.Submissions.__table__.c)
        self.assertNotIn("provided", self.Solves.__table__.c)
        self.assertEqual(self.rows("solves"), 4)
        self.assertEqual(self.rows("submissions"), 5)

    def test_the_bulk_delete_it_replaced_will_not_compile_on_sqlite(self):
        # The camp-day container runs SQLite. This raises before any row is
        # read, so it fired on every boot — including the empty first one.
        with self.assertRaises(NotImplementedError):
            clear_demo_bulk(self.session, self.Solves)

    def test_the_shipped_strategy_runs_on_sqlite(self):
        self.assertEqual(clear_demo(self.session, self.Solves), 2)

    def test_it_removes_the_marked_rows_from_both_tables(self):
        # The parent row has to go as well: the PostgreSQL compilation of the
        # bulk form deleted only from `solves`, so the marker survived and a
        # re-run found nothing left to clean up.
        clear_demo(self.session, self.Solves)
        self.assertNotIn(DEMO_SOLVE_MARKER, self.provided_values())
        self.assertEqual(self.rows("solves"), 2)
        self.assertEqual(self.rows("submissions"), 3)

    def test_real_solves_and_fails_are_left_alone(self):
        clear_demo(self.session, self.Solves)
        self.assertEqual(
            self.provided_values(), [self.REAL_ANSWER] * 3
        )
        surviving = self.session.query(self.Solves).all()
        self.assertEqual(
            sorted(solve.challenge_id for solve in surviving), [9, 10]
        )
        self.assertEqual(self.session.query(self.Fails).count(), 1)

    def test_a_second_boot_finds_nothing_left_to_clear(self):
        # bootstrap calls this on every boot, not once.
        self.assertEqual(clear_demo(self.session, self.Solves), 2)
        self.assertEqual(clear_demo(self.session, self.Solves), 0)
        self.assertEqual(self.rows("submissions"), 3)


if __name__ == "__main__":
    unittest.main()
