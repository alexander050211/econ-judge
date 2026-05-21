# econ-judge

Auto-grader + live scoreboard for the **SNU SENS 공헌 E-CON** 논설 (logic-design) task. Built as a CTFd custom challenge type plugin: mentees upload `.dig` files (Digital simulator format) via a web UI, the server runs the file against secret testcases using Digital's CLI, partial credit is computed from pass-N-of-M, and a live scoreboard projects on the BK Hall screen.

> Status — **working slice, 18 challenges / 100 pts** as of 2026-05-21. Hierarchical submissions resolved via canonical seeding; 7-seg display sink resolved via skeleton-stamped Out pins (truth table locked).

## Why it exists

Two retrospective items from the 26 동계 공드림 E-CON cycle this directly addresses:

1. **논설 심사 had no audience or feedback.** Mentees presented their breadboards only to judges in isolation. Live scoreboard + per-testcase pass/fail visibility reframes 심사 as a public competition with built-in feedback.
2. **E-CON team committed to 회로 부분점수** for the next cycle (previously all-or-nothing 12 points). Pass-N-of-M testcase scoring is the cleanest possible form of partial credit.

Full motivation, architecture, scope, and timeline live in the spec inside the project wiki:

- `interests/snu-sens/2026-summer-camp/econ-autograder-spec.md`

## Approach (verified during spike)

CTFd is the host platform. We ship a custom challenge type plugin (id `digital`) modeled on [ghidragolf/ctfd-fileupload](https://github.com/ghidragolf/ctfd-fileupload):

- `BaseChallenge` subclass with stub `attempt()`/`solve()`/`fail()` (the default text-flag flow is not used)
- Custom Flask blueprint exposes `/api/v1/digital/challenges/<id>/attempt` accepting `multipart/form-data` with the `.dig` file via `request.files['file']`
- Endpoint subprocesses `java -cp Digital.jar CLI test -circ <upload.dig> -tests <secret-tests.dig>`, parses `<label>: passed|failed` stdout, computes partial credit, and writes a Solve / Fail record
- Hints (point-deducting, mapped to the existing 26공드림 1점 할인권 mechanic) and the scoreboard work via CTFd's built-in Hints + Solves models with no extra wiring

Synchronous grading (no RabbitMQ); single worker is enough at 40-mentee scale.

## Layout (target — not yet implemented)

```
econ-judge/
├── README.md                  ← this file
├── .gitignore
├── econ_judge/                ← the CTFd plugin module
│   ├── __init__.py            ← BaseChallenge subclass, load(), blueprint registration
│   ├── endpoints.py           ← /attempt POST handler — file → Digital CLI → Solve/Fail
│   ├── model.py               ← optional per-submission record
│   └── assets/                ← Nunjucks templates + JS for the challenge UI
├── tests/                     ← sample mentee .dig files + secret testcase files for smoke testing
├── Dockerfile                 ← CTFd base image + Java + Digital.jar + this plugin
└── docker-compose.yml         ← CTFd + reverse proxy + plugin volume mount
```

## Open questions to resolve at 5/21

(Tracked in the spec doc. The short list:)

1. BK Hall network — portable router (preferred), public cloud, or USB-fallback?
2. 김범준's appetite for owning the testcase authoring?
3. Anonymized vs named scoreboard until the last hour?
4. Partial credit mechanic: split each problem into sub-challenges (scoreboard inflation) vs custom solve model (more code)?

## Deploy to Render

A `Dockerfile` + `render.yaml` ship a one-click deploy via Render. Free-tier specifics:

- Disk is ephemeral; `bin/bootstrap.py` re-seeds the admin user + 18 challenges on every container start, so the deploy is self-healing. Solves/Fails history is lost between restarts. For persistent history, add a Render Postgres service and set `DATABASE_URL`.
- The service spins down after 15 min idle; cold start is ~30 s (Java warmup on first grading).

Setup:

1. In Render: New → Web Service → connect this GitHub repo.
2. Render auto-detects `render.yaml` and picks Docker runtime.
3. The first build is ~5 min (clones CTFd, downloads Digital.jar, installs deps).
4. After deploy, the URL is your CTFd. Admin login uses the auto-generated `CTFD_ADMIN_PASSWORD` from the Render env (visible in the Render dashboard → Environment).

## References

- [hneemann/Digital](https://github.com/hneemann/Digital) — the simulator + its CLI test harness
- [CTFd docs — Challenge Type Plugins](https://docs.ctfd.io/docs/plugins/challenge-types/)
- [ghidragolf/ctfd-fileupload](https://github.com/ghidragolf/ctfd-fileupload) — file-upload challenge type, our reference implementation
- [CTFd source](https://github.com/CTFd/CTFd) — plugin loader at `CTFd/plugins/__init__.py`, BaseChallenge at `CTFd/plugins/challenges/__init__.py`
