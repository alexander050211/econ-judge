# econ-judge

Auto-grader + live scoreboard for the **SNU SENS 2026 하계 공학 캠프 E-CON 논설** (logic-design) task. Built as a CTFd custom challenge type plugin: mentees answer a truth table or upload their `.dig` round folder through the web UI, the server checks secret testcases using Digital's CLI, and a projector view puts the standings on the BK Hall screen. Challenge scores are all-or-nothing; failed circuit submissions still receive an aggregate pass count as feedback.

> Status — **2026 summer final set: Round 1 (35 points) + Round 2 (45 points) = 80 online judge points**. Round 3 is a 20-point physical build and is graded manually outside the website. The contest ran on 2026-08-04 — see [What actually ran](#what-actually-ran-2026-08-04).

## Why it exists

Two retrospective items from the previous (2026 winter) E-CON cycle this directly addresses:

1. **논설 심사 had no audience or feedback.** Mentees presented their breadboards only to judges in isolation. Live scoreboard + per-testcase pass/fail visibility reframes 심사 as a public competition with built-in feedback.
2. **Circuit feedback needs to be immediate without exposing secret vectors.** The judge reports only the aggregate number of passing rows and awards points only when every row passes.

Full motivation, architecture, scope, and timeline live in the spec inside the project wiki:

- `interests/snu-sens/2026-summer-camp/econ-autograder-spec.md`

## Approach

CTFd is the host platform. We ship a custom challenge type plugin (id `digital`) modeled on [ghidragolf/ctfd-fileupload](https://github.com/ghidragolf/ctfd-fileupload):

- `BaseChallenge` subclass with stub `attempt()`/`solve()`/`fail()` (the default text-flag flow is not used)
- Challenge 1 is answered as a truth table through a dedicated **one-attempt** JSON endpoint, `/api/v1/digital/challenges/1/truth-table-attempt`. The other 14 accept `.dig` circuits.
- Circuits go to `/api/v1/digital/challenges/<id>/attempt` as `multipart/form-data`, and the browser submits the **whole selected folder**, not a single file: every `.dig` in it is appended under the repeated key `files`, each part named `<folder>/<name>.dig` when the folder came from the directory picker and `submission/<name>.dig` when it was dropped (a dropped `File` carries an empty `webkitRelativePath`, so the team's folder name never reaches the wire — only the basename matters to grading). The endpoint requires exactly one path segment before the filename (no nested folders), flattens the folder into an isolated temp dir, picks this challenge's answer file out of `problemset.HWP_STARTER_FILES`, and leaves the rest beside it as the components Digital resolves by filename. Caps: 32 files, 256 KB per file, 1 MB per bundle.
- Before Java runs, `grader._validate_structure` enforces the construction rules a truth table cannot see (e.g. "exactly one NAND gate", "no gate with more than two inputs"). A structural rejection counts as a wrong answer, not a system error.
- The grader then subprocesses `java -Xmx256m -Dfile.encoding=UTF-8 -cp Digital.jar CLI test -circ <answer.dig> -tests secret_tests/<id>.dig`, parses the `<label>: passed|failed` stdout, and writes a Solve only when every testcase passes. A wrong answer returns `passed/total` plus a directional 확인해 볼 점 checklist from `concepts.py` — never which row failed.
- Point-deducting hints run **on paper, by mentors**. Nothing in this repo creates a CTFd `Hints` row; the `hints` field in a grading response is the free checklist above, not a purchased hint.
- CTFd's public scoreboard is switched off (`score_visibility=admins`). Mentees see `/my-score` (their own score plus an anonymized leader), and the BK Hall projector page reads `/api/v1/digital/projector`.
- `competition.py` derives every phase — before / round 1 / break / round 2 / finished — from the single `ECON_JUDGE_COMPETITION_START` timestamp and syncs CTFd's own challenge visibility to it, so a restart mid-contest recovers on its own.

Synchronous grading (no RabbitMQ), one gunicorn gevent worker, one grading slot: enough at this camp's scale of 4 teams / ~12 mentees.

## Layout

```
econ-judge/
├── README.md                  ← this file
├── Dockerfile                 ← CTFd 3.8.5 + JRE 21 + Digital.jar + this plugin
├── render.yaml                ← Render service + the camp pre-flight / teardown checklists
├── requirements-dev.txt       ← what the test suite needs
├── econ_judge/                ← the CTFd plugin module
│   ├── __init__.py            ← `digital` challenge type, load(), blueprints, template overrides
│   ├── endpoints.py           ← /attempt, /truth-table-attempt, /competition, /my-score, /projector, /health, /export
│   ├── grader.py              ← structural checks + Digital CLI subprocess + concurrency guard
│   ├── competition.py         ← round schedule; drives CTFd challenge visibility
│   ├── health.py              ← readiness checks: Digital.jar, java, secret_tests
│   ├── problems.py            ← full-page participant problem views
│   ├── problemset.py          ← challenge → starter-file manifest, truth-table answer key
│   ├── concepts.py            ← participant-safe concept labels + directional hints
│   ├── assets/                ← challenge-type templates, JS, problem images, starter .dig folders
│   └── templates/             ← overridden CTFd login/register + the problem page
├── bin/
│   ├── bootstrap.py           ← seeds admin, the 15 challenges, the team roster, CTFd pages
│   └── entrypoint.sh          ← bootstrap, then gunicorn (gevent, 1 worker)
├── secret_tests/<id>.dig      ← the graded Digital Testcase files
├── canonical/                 ← reference sub-circuits (no longer auto-seeded into submissions)
├── solutions/2026-summer/     ← generated reference solutions
├── tests/                     ← unit tests, file generators, and manual smoke scripts
└── .github/workflows/         ← keep-warm pinger + the test suite
```

Two of the API routes on that `endpoints.py` line are operational rather than participant-facing:

- `GET /api/v1/digital/health` — unauthenticated readiness probe for the grading path: Digital.jar, a `java` on PATH, and one secret test per submittable challenge. 200 only when all three pass, 503 with `failed_checks` naming the failures otherwise. It is what `render.yaml`'s `healthCheckPath` points at, and `?verbose=1` (full paths, row counts, `ECON_JUDGE_*` tuning) and `?deep=1` (grades a reference circuit) are admin-gated variants of it.
- `GET /api/v1/digital/export` — admin-only archive of the whole contest record: per-participant scores plus the round-by-round attempt log, with `?format=csv` for one row per (team, challenge). Step 1 of the teardown checklist in `render.yaml` is this URL.

⚠️ `secret_tests/` and `solutions/` are **committed in this public repository**. That was acceptable for a one-off camp, but it means the answers are readable by anyone who finds the repo: a future camp must author fresh testcases rather than reuse these.

## What actually ran (2026-08-04)

Only what the repository and its configuration establish:

- Commit `509f5a5` set `ECON_JUDGE_COMPETITION_START=2026-08-04T13:20:00+09:00` with `ECON_JUDGE_REHEARSAL=false`. Against the durations in `competition.py` that is **Round 1 13:20–14:30 KST**, a 10-minute break, then **Round 2 14:40–16:00 KST**.
- The contest was live: commit `8a67a04` is timestamped 13:45 KST — 25 minutes into Round 1 — and relaxes the upload check that had required each team's folder to be named exactly `N조_1라운드`. It is the last commit in the repo.
- `render.yaml` still carries that past start date and `CTFD_DEMO_DATA: "true"` as the historical record of the event. Render's Environment panel overrides both, so what a given deploy actually ran with lives there, not in this file. Left at a past date, the service sits in the `finished` phase permanently: challenges visible, every submission refused.
- **Results are not in this repository**, and on Render's free tier they do not survive a restart — the SQLite file is on ephemeral disk. Unless the day's export was taken or `DATABASE_URL` pointed at a real database, the Solves and Fails are gone. See the POST-CAMP TEARDOWN CHECKLIST at the bottom of `render.yaml`.

## Deploy to Render

A `Dockerfile` + `render.yaml` ship a one-click deploy via Render. Free-tier specifics:

- Disk is ephemeral; `bin/bootstrap.py` re-seeds the admin user, the 15 challenges and the team roster on every container start, so the deploy is self-healing. Solves/Fails history is lost between restarts. For persistent history, add a Render Postgres service and set `DATABASE_URL`.
- The service spins down after 15 min idle; cold start is ~30 s (Java warmup on first grading). `.github/workflows/keep-warm.yml` pings it during camp days — it is gated on the `KEEPWARM_ENABLED` repo variable and must be turned off afterwards.

Setup:

1. In Render: New → Web Service → connect this GitHub repo.
2. Render auto-detects `render.yaml` and picks Docker runtime.
3. The first build is ~5 min (clones CTFd, downloads Digital.jar, installs deps).
4. After deploy, the URL is your CTFd. Admin login uses the auto-generated `CTFD_ADMIN_PASSWORD` from the Render env (visible in the Render dashboard → Environment).

The two checklists at the bottom of `render.yaml` are the operational contract for running and then retiring a camp — read them before and after the event.

### Local credentials (`.env`)

Copy `.env.example` to `.env` and fill in the admin password (from Render dashboard). The `.env` file is gitignored. Tests like `tests/deploy_smoke.py` auto-load it — no need to `export` env vars manually each run.

### Demo data toggle

`bin/bootstrap.py` always seeds the 4-team roster (`1조`–`4조`) — that roster *is* the camp login list, so a free-tier cold start can never lock a team out. `CTFD_DEMO_DATA` gates only the fabricated **Solves**: `true` (the default) layers a realistic spread on top so you can preview a populated scoreboard.

For the actual camp day, set **`CTFD_DEMO_DATA=false`** in Render's Environment and redeploy; bootstrap then deletes the fabricated Solves (they carry a marker in `provided`, so real submissions are never touched) and the board starts empty. Do **not** delete the accounts via `/admin/users` — roster seeding runs unconditionally and recreates them on the next boot, and they are the teams' real logins.

## Tests

```
pip install -r requirements-dev.txt
python -m pytest tests
```

The suite is written with `unittest` but the files are named `*_test.py`, so pytest collects them — 111 tests across 12 modules as of this commit.

Three of those cases need a JRE and `Digital.jar` at the repo root, and they behave differently without them:

- `tests/grader_structure_test.py`'s component-bundle case really shells out to Digital and **fails loudly**.
- `tests/health_export_test.py`'s ready-200 case **skips silently** — it is the only check that `/health` answers 200 with an empty `failed_checks`, so on a machine without Java a green run has not exercised that assertion at all. Its admin-report case skips for the same reason.

Everything else in the suite is pure Python. `tests/canonical_self_test.py` is a script rather than a pytest case (pytest imports it and collects nothing): it grades 17 committed reference circuits — the three shared sub-circuits in `canonical/` plus one answer per submittable challenge in `solutions/2026-summer/` — against the committed secret tests with the real Digital.jar, and takes about a minute of JVM time.

```
python tests/canonical_self_test.py
```

`.github/workflows/tests.yml` runs both on push and pull request — it installs a JRE and downloads Digital.jar first, so neither Java-dependent case fails or skips there — and its header records what it deliberately leaves out (the live-deploy smoke scripts, and the generator/admin scripts under `tests/`).

## References

- [hneemann/Digital](https://github.com/hneemann/Digital) — the simulator + its CLI test harness
- [CTFd docs — Challenge Type Plugins](https://docs.ctfd.io/docs/plugins/challenge-types/)
- [ghidragolf/ctfd-fileupload](https://github.com/ghidragolf/ctfd-fileupload) — file-upload challenge type, our reference implementation
- [CTFd source](https://github.com/CTFd/CTFd) — plugin loader at `CTFd/plugins/__init__.py`, BaseChallenge at `CTFd/plugins/challenges/__init__.py`
