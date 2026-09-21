/* Runs econ_judge/assets/landing.js and round-ui.js in node for
   tests/landing_clock_test.py, which owns every expectation. The test writes
   one JSON object to stdin — the scenario's name, the two script paths and the
   scenario's inputs — and reads one JSON value back from stdout.

   Each scenario loads the real scripts into a vm context holding a small
   stand-in for the page: the landing markup INDEX_CONTENT serves (the test
   checks the two describe the same page), a navbar, a fake clock with fake
   timers, and a fake server behind fetch(). Nothing here knows the schedule:
   every payload and instant arrives from the test, built by competition.py. */
"use strict";

const fs = require("fs");
const vm = require("vm");

const INPUT = JSON.parse(fs.readFileSync(0, "utf8"));

// Output is escaped to ASCII so it survives any console code page.
function emit(value) {
  const json = JSON.stringify(value === undefined ? null : value);
  process.stdout.write(json.replace(/[^\x00-\x7e]/g, (c) => "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0")));
}

// Lets every pending promise chain (fetch → json → render) run to its end.
const flush = async () => { for (let i = 0; i < 8; i += 1) await new Promise((resolve) => setImmediate(resolve)); };

function makeEvent(type, init = {}) {
  return {
    type, key: "", repeat: false, isComposing: false, ...init,
    defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; },
  };
}

/* ── A small DOM ──────────────────────────────────────────────────────────
   Only the surface the two scripts touch. Selectors are single compound
   selectors (tag, #id, .class, [attr], [attr="value"]); anything else throws,
   so a script that starts relying on more fails loudly here instead of
   quietly finding nothing. Every write is counted, so a test can tell a tick
   that changed nothing from one that rewrote the page. */
const COMPOUND = /^([a-z][a-z0-9]*)?((?:#[\w-]+|\.[\w-]+|\[[\w-]+(?:="[^"]*")?\])*)$/i;

function parseSelector(selector) {
  const text = String(selector).trim();
  const match = text && COMPOUND.exec(text);
  if (!match) throw new Error(`the stub page cannot answer the selector ${JSON.stringify(selector)}`);
  const parts = { tag: match[1] ? match[1].toUpperCase() : null, id: null, classes: [], attrs: [] };
  for (const token of match[2].match(/#[\w-]+|\.[\w-]+|\[[^\]]+\]/g) || []) {
    if (token[0] === "#") parts.id = token.slice(1);
    else if (token[0] === ".") parts.classes.push(token.slice(1));
    else {
      const attr = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(token);
      parts.attrs.push([attr[1], attr[2] === undefined ? null : attr[2]]);
    }
  }
  return parts;
}

class Element {
  constructor(document, tag, attributes = {}) {
    this.ownerDocument = document;
    this.tagName = tag.toUpperCase();
    this.parentNode = null;
    this.children = [];
    this.attrs = new Map();
    this.classes = new Set();
    this.listeners = new Map();
    this.own = "";
    this.dataset = {};
    this.hidden = false;
    this.checked = false;
    this.defaultChecked = false;
    this.writes = 0;
    const el = this;
    const props = new Map();
    this.props = props;
    this.style = {
      getPropertyValue: (name) => (props.has(name) ? props.get(name) : ""),
      setProperty: (name, value) => { el.writes += 1; props.set(name, String(value)); },
      removeProperty: (name) => { el.writes += 1; const old = props.get(name) || ""; props.delete(name); return old; },
    };
    this.classList = {
      contains: (name) => el.classes.has(name),
      toggle: (name, force) => {
        el.writes += 1;
        const on = force === undefined ? !el.classes.has(name) : Boolean(force);
        if (on) el.classes.add(name); else el.classes.delete(name);
        return on;
      },
    };
    for (const [name, value] of Object.entries(attributes)) this.setAttribute(name, value);
    this.checked = this.defaultChecked;
    this.writes = 0;
  }

  get id() { return this.attrs.get("id") || ""; }
  set id(value) { this.setAttribute("id", value); }
  get className() { return [...this.classes].join(" "); }
  set className(value) { this.setAttribute("class", value); }
  get type() { return this.attrs.get("type") || ""; }
  get name() { return this.attrs.get("name") || ""; }
  get form() {
    for (let node = this.parentNode; node; node = node.parentNode) if (node.tagName === "FORM") return node;
    return null;
  }

  hasAttribute(name) { return this.attrs.has(name); }
  getAttribute(name) { return this.attrs.has(name) ? this.attrs.get(name) : null; }
  setAttribute(name, value) {
    this.writes += 1;
    const text = String(value);
    this.attrs.set(name, text);
    if (name === "class") { this.classes.clear(); text.split(/\s+/).filter(Boolean).forEach((c) => this.classes.add(c)); }
    if (name === "checked") this.defaultChecked = true;
  }
  removeAttribute(name) {
    this.writes += 1;
    this.attrs.delete(name);
    if (name === "class") this.classes.clear();
  }

  get textContent() { return this.own + this.children.map((child) => child.textContent).join(""); }
  set textContent(value) {
    this.writes += 1;
    this.children.forEach((child) => { child.parentNode = null; });
    this.children = [];
    this.own = String(value);
  }
  // round-ui.js rebuilds the pill with innerHTML and then looks its parts up
  // by class. Its markup is one flat level, so that is all this parses.
  set innerHTML(html) {
    this.textContent = "";
    for (const match of String(html).matchAll(/<([a-z][a-z0-9]*)((?:\s+[\w-]+="[^"]*")*)\s*>/gi)) {
      const attributes = {};
      for (const attr of match[2].matchAll(/([\w-]+)="([^"]*)"/g)) attributes[attr[1]] = attr[2];
      this.appendChild(new Element(this.ownerDocument, match[1], attributes));
    }
  }

  appendChild(child) {
    if (child.parentNode) child.parentNode.children = child.parentNode.children.filter((node) => node !== child);
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  contains(other) {
    for (let node = other; node; node = node.parentNode) if (node === this) return true;
    return false;
  }
  descendants() { return this.children.flatMap((child) => [child, ...child.descendants()]); }
  matches(selector) {
    if (selector === ":focus-visible") return this.ownerDocument.activeElement === this && this.ownerDocument.focusVisible;
    const want = parseSelector(selector);
    return (!want.tag || want.tag === this.tagName)
      && (!want.id || want.id === this.id)
      && want.classes.every((c) => this.classes.has(c))
      && want.attrs.every(([name, value]) => this.attrs.has(name) && (value === null || this.attrs.get(name) === value));
  }
  querySelectorAll(selector) { parseSelector(selector); return this.descendants().filter((node) => node.matches(selector)); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }
  dispatchEvent(event) {
    event.target = event.target || this;
    for (const listener of this.listeners.get(event.type) || []) listener.call(this, event);
    return !event.defaultPrevented;
  }

  // What a click does to the three kinds of control the toy has.
  click() {
    if (this.tagName === "INPUT" && this.type === "checkbox") {
      this.checked = !this.checked;
      this.dispatchEvent(makeEvent("input"));
      this.dispatchEvent(makeEvent("change"));
    } else if (this.tagName === "INPUT" && this.type === "radio" && !this.checked) {
      const scope = this.form || this.ownerDocument.documentElement;
      scope.querySelectorAll("input").filter((input) => input.name === this.name).forEach((input) => { input.checked = false; });
      this.checked = true;
      this.dispatchEvent(makeEvent("change"));
    } else if (this.tagName === "BUTTON" && this.type === "reset" && this.form) {
      this.form.reset();
    }
  }
  // A form's reset: the event first, then every control back to its default.
  reset() {
    const event = makeEvent("reset", { cancelable: true });
    this.dispatchEvent(event);
    if (event.defaultPrevented) return;
    this.querySelectorAll("input").forEach((input) => { input.checked = input.defaultChecked; });
    // On the page the verdict holding the reset button is display:none once
    // .run is unchecked, and Chrome drops the button's focus to <body>.
    const active = this.ownerDocument.activeElement;
    if (active && active.tagName === "BUTTON" && this.contains(active)) this.ownerDocument.blur();
  }
  focus(options) {
    this.ownerDocument.activeElement = this;
    this.ownerDocument.focusLog.push({ id: this.id, options: options ? JSON.parse(JSON.stringify(options)) : null });
  }

  serialize() {
    return {
      tag: this.tagName.toLowerCase(),
      attrs: Object.fromEntries(this.attrs),
      text: this.own,
      children: this.children.map((child) => child.serialize()),
    };
  }
  // The attributes a script may write (class is reported as classes), the
  // custom properties on its style, and its classes.
  snapshot() {
    const attrs = Object.fromEntries([...this.attrs].filter(([name]) => name !== "class"));
    return { attrs, props: Object.fromEntries(this.props), classes: [...this.classes].sort() };
  }
}

class Document {
  constructor() {
    this.readyState = "complete";
    this.hidden = false;
    this.listeners = new Map();
    this.focusLog = [];
    this.focusVisible = false;
    this.documentElement = new Element(this, "html");
    this.head = this.documentElement.appendChild(new Element(this, "head"));
    this.body = this.documentElement.appendChild(new Element(this, "body"));
    this.activeElement = this.body;
  }
  createElement(tag) { return new Element(this, tag); }
  getElementById(id) { return this.documentElement.descendants().find((node) => node.id === id) || null; }
  querySelector(selector) { return this.documentElement.querySelector(selector); }
  querySelectorAll(selector) { return this.documentElement.querySelectorAll(selector); }
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }
  listenerTypes() { return [...this.listeners.keys()].flatMap((type) => this.listeners.get(type).map(() => type)); }
  fire(type) { for (const listener of this.listeners.get(type) || []) listener(makeEvent(type)); }
  blur() { this.activeElement = this.body; }
  // Keyboard focus shows a ring (:focus-visible); a mouse click's does not.
  focusOn(element, visible) { this.activeElement = element; this.focusVisible = visible; }
}

function h(document, tag, attributes = {}, ...kids) {
  const el = new Element(document, tag, attributes);
  for (const kid of kids) {
    if (typeof kid === "string") el.own = kid;
    else el.appendChild(kid);
  }
  return el;
}

/* The landing markup, cut down to what landing.js reads and writes. The test
   describes this and INDEX_CONTENT the same way and requires the two to match:
   one of each clock slot with its served text, one name slot, the status line,
   and the toy's form with its row-0 answers, the 채점 box and the three
   verdict cards' resets (partial, fail, pass). */
function landingMarkup(document, served, row0) {
  const answer = (letter, value) => h(document, "input", {
    class: `v i0 o${value}`, type: "radio", name: "econ-toy-q0", id: `econ-toy-q0${letter}`,
    ...(row0 === letter ? { checked: "" } : {}),
  });
  return h(document, "div", { class: "econ-landing", id: "econ-landing" },
    h(document, "div", { class: "stage" },
      h(document, "p", { class: "vh", id: "econ-landing-status", role: "status", "aria-live": "polite" }),
      h(document, "div", { class: "clock", id: "econ-landing-clock", role: "timer" },
        h(document, "span", { class: "hh", "data-econ-clock": "hh" }),
        h(document, "span", { class: "mm", "data-econ-clock": "mm" }, "--"),
        h(document, "span", { class: "ss", "data-econ-clock": "ss" }, ":--")),
      h(document, "span", { "data-econ-user": "" }),
      h(document, "b", { "data-econ-clock": "elapsed" }),
      h(document, "span", { "data-econ-clock": "total" },
        ...served.total.map((text, index) => h(document, "span", { class: `ph round${index + 1}` }, text))),
      h(document, "form", { class: "toy" },
        answer("a", 0),
        answer("b", 1),
        h(document, "input", { class: "run", type: "checkbox", id: "econ-toy-run", "aria-label": "채점하기" }),
        h(document, "button", { class: "lbtn sm", type: "reset" }, "처음부터"),
        h(document, "button", { class: "lbtn sm", type: "reset" }, "처음부터"),
        h(document, "a", { class: "lbtn sm po", href: "/login" }, "로그인하고 기다리기"),
        h(document, "button", { class: "lbtn sm", type: "reset" }, "처음부터"))));
}

/* One page: the two scripts, as THEME_HEADER_CSS loads them, over the stub
   DOM, a fake clock and a fake server. Timers never run by themselves: a
   scenario either fires one kind (fire) or lets fake time pass (advance). */
function boot({
  roundUi = false, landing = true, navbar = true, init = {}, now = 0,
  payload = null, timeline = null, served = INPUT.served, row0 = "a",
} = {}) {
  const clock = { now };
  const log = { fetches: [], events: [], frames: 0 };
  const document = new Document();
  if (navbar) document.body.appendChild(h(document, "nav", { class: "navbar" }));
  const root = landing ? document.body.appendChild(landingMarkup(document, served, row0)) : null;
  const server = { payload, timeline, status: 200, offline: false, hold: false, held: [] };
  const timers = [];
  const frames = [];
  let owner = "page";
  const addTimer = (fn, delay, every) => {
    timers.push({ id: timers.length + 1, fn, delay, next: clock.now + delay, owner, every });
    return timers.length;
  };
  const clearTimer = (id) => { if (timers[id - 1]) timers[id - 1].cleared = true; };
  // What the server says at this instant: a timeline of [from, payload] pairs
  // lets a scenario flip the phase at a chosen moment.
  const answerAt = (at) => {
    if (!server.timeline) return server.payload;
    let current = null;
    for (const [from, body] of server.timeline) if (from <= at) current = body;
    return current;
  };
  const sandbox = {
    document,
    init,
    location: { pathname: "/" },
    __clock: clock,
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options && options.detail; } },
    setInterval: (fn, delay) => addTimer(fn, delay, true),
    clearInterval: clearTimer,
    setTimeout: (fn, delay = 0) => addTimer(fn, delay, false),
    clearTimeout: clearTimer,
    requestAnimationFrame: (fn) => { log.frames += 1; frames.push(fn); return log.frames; },
    dispatchEvent: (event) => { log.events.push([event.type, event.detail ? event.detail.phase : null]); return true; },
    fetch: (url, options) => {
      const body = answerAt(clock.now);
      log.fetches.push({
        url,
        options: options ? JSON.parse(JSON.stringify(options)) : null,
        at: clock.now,
        phase: body && typeof body === "object" ? body.phase : null,
      });
      if (server.offline) return Promise.reject(new Error("offline"));
      const ok = server.status >= 200 && server.status < 300;
      const reply = () => ({ ok, redirected: false, json: async () => ({ success: true, data: body }) });
      if (server.hold) return new Promise((resolve) => { server.held.push(() => resolve(reply())); });
      return Promise.resolve(reply());
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext("Date.now = () => __clock.now;", sandbox);
  if (roundUi) {
    owner = "round-ui";
    vm.runInContext(fs.readFileSync(INPUT.roundUi, "utf8"), sandbox, { filename: "round-ui.js" });
  }
  owner = "landing";
  vm.runInContext(fs.readFileSync(INPUT.landing, "utf8"), sandbox, { filename: "landing.js" });
  owner = "page";

  const byId = (id) => document.getElementById(id);
  const slot = (name) => (root ? root.querySelectorAll(`[data-econ-clock="${name}"]`) : []);
  const stage = root ? root.querySelector(".stage") : null;
  const page = {
    sandbox, document, log, server, clock, timers, root, stage, byId,
    status: byId("econ-landing-status"),
    hero: byId("econ-landing-clock"),
    setNow: (value) => { clock.now = value; },
    intervals: () => timers.filter((t) => t.every).map((t) => t.delay).sort((a, b) => a - b),
    fire: (who, delay) => timers
      .filter((t) => !t.cleared && t.every && t.owner === who && t.delay === delay)
      .forEach((t) => t.fn()),
    frames: () => { frames.splice(0).forEach((fn) => fn()); },
    pill: () => byId("econ-round-countdown"),
    // Everything landing.js writes, as the page would show it.
    view: () => ({
      stage: stage.snapshot(),
      classes: page.hero.snapshot().classes,
      hh: slot("hh")[0].textContent,
      mm: slot("mm")[0].textContent,
      ss: slot("ss")[0].textContent,
      elapsed: slot("elapsed").map((el) => el.textContent),
      total: slot("total").map((el) => el.textContent),
      user: root.querySelectorAll("[data-econ-user]").map((el) => el.textContent),
      status: page.status.textContent,
    }),
    landingWrites: () => [root, ...root.descendants()].reduce((sum, el) => sum + el.writes, 0),
    // Lets fake time pass: every timer due in the window fires in order, and
    // each one's promises settle before the next, as they would in a browser.
    async advance(ms, observe) {
      const end = clock.now + ms;
      for (;;) {
        const due = timers.filter((t) => !t.cleared && t.next <= end).sort((a, b) => a.next - b.next || a.id - b.id)[0];
        if (!due) break;
        clock.now = due.next;
        if (due.every) due.next += due.delay; else due.cleared = true;
        due.fn();
        await flush();
        if (observe) observe();
      }
      clock.now = end;
    },
  };
  return page;
}

function pillText(page) {
  const pill = page.pill();
  const time = pill && pill.querySelector(".econ-round-countdown-time");
  return time ? time.textContent : null;
}

const SCENARIOS = {};

// The pure helpers, over tables the test builds.
SCENARIOS.compute = async (input) => {
  const page = boot({ landing: false, navbar: false });
  const { compute, announcement, userLabel } = page.sandbox.econLanding;
  return {
    views: input.cases.map(([payload, now, signedIn]) => compute(payload, now, signedIn)),
    labels: input.names.map((name) => userLabel(name)),
    news: input.news.map(([previous, payload, now]) => announcement(previous, compute(payload, now, false))),
  };
};

// The stub landing markup, for the test to compare with INDEX_CONTENT.
SCENARIOS.page = async () => boot().root.serialize();

// Everywhere but the landing page the script must stop at its gate.
SCENARIOS.gate = async (input) => {
  const page = boot({ landing: false, init: { userId: 3, userName: "2조" }, payload: input.payload, now: input.now });
  await flush();
  return {
    timers: page.timers.length,
    fetches: page.log.fetches.length,
    listeners: page.document.listenerTypes().length,
    hasCompute: typeof page.sandbox.econLanding.compute === "function",
  };
};

// round-ui.js absent: landing.js polls on its own, on round-ui.js's cadence.
SCENARIOS.fallback = async (input) => {
  const out = {};
  const page = boot({ init: input.init, payload: input.mid.payload, now: input.mid.now });
  out.firstPaint = page.view();
  out.fetchOptions = page.log.fetches.map((entry) => [entry.url, entry.options]);
  out.intervals = page.intervals();
  out.listeners = page.document.listenerTypes();
  await flush();
  out.live = page.view();
  // A failing poll keeps the last good display.
  page.server.status = 500;
  page.fire("landing", 15000);
  await flush();
  page.server.status = 200;
  page.server.offline = true;
  page.fire("landing", 15000);
  await flush();
  page.server.offline = false;
  out.afterFailures = page.view();
  page.server.payload = input.brk.payload;
  page.setNow(input.brk.now);
  page.fire("landing", 15000);
  await flush();
  out.afterFlip = page.view();
  return out;
};

// Fallback at zero with this machine's clock ahead of the server's: the
// server's answers follow input.timeline while fake time passes.
SCENARIOS.fallbackZero = async (input) => {
  const page = boot({ timeline: input.timeline, now: input.boot });
  await flush();
  const changes = [];
  let last = page.stage.getAttribute("data-phase");
  const observe = () => {
    const phase = page.stage.getAttribute("data-phase");
    if (phase !== last) { changes.push([page.clock.now - input.zero, phase]); last = phase; }
  };
  for (const [step, value] of input.steps) {
    if (step === "advance") await page.advance(value, observe);
    else if (step === "hide") page.document.hidden = true;
    else if (step === "show") { page.document.hidden = false; page.document.fire("visibilitychange"); await flush(); observe(); }
  }
  return { fetches: page.log.fetches.map((entry) => entry.at - input.zero), changes, view: page.view() };
};

// Fallback: an answer overtaken by a newer one is dropped when it lands.
SCENARIOS.fallbackOrder = async (input) => {
  const page = boot({ payload: input.r1.payload, now: input.r1.now });
  await flush();
  page.server.hold = true;
  page.fire("landing", 15000); // asked while the server still says round 1 …
  page.server.hold = false;
  page.server.payload = input.brk.payload;
  page.setNow(input.brk.now);
  page.document.fire("visibilitychange"); // … and overtaken by one that says break
  await flush();
  const overtaken = page.view();
  page.server.held.splice(0).forEach((release) => release());
  await flush();
  page.fire("landing", 250);
  return {
    overtaken,
    after: page.view(),
    fetches: page.log.fetches.map((entry) => entry.phase),
    statusWrites: page.status.writes,
  };
};

// The real round-ui.js and landing.js together, as THEME_HEADER_CSS loads them.
SCENARIOS.shared = async (input) => {
  const out = {};
  const page = boot({ roundUi: true, init: input.init, payload: input.mid.payload, now: input.mid.now });
  out.hasShared = typeof page.sandbox.econRoundState === "object";
  out.firstPaint = page.view();
  out.fetchesAtBoot = page.log.fetches.length;
  out.intervals = page.intervals();
  await flush();
  out.live = page.view();
  out.pill = pillText(page);
  out.get = page.sandbox.econRoundState.get();
  // An unchanged tick writes nothing.
  page.fire("landing", 250);
  const settled = page.landingWrites();
  for (let i = 0; i < 4; i += 1) page.fire("landing", 250);
  out.idleWrites = page.landingWrites() - settled;
  // Hero clock and navbar pill agree to the second, alarms included.
  out.sweep = input.sweep.map((now) => {
    page.setNow(now);
    page.fire("round-ui", 250);
    page.fire("landing", 250);
    const pill = page.pill();
    const pillClasses = pill.snapshot().classes.filter((c) => c.startsWith("is-"));
    return { now, pill: pillText(page), pillClasses, hero: page.view() };
  });
  // A subscriber that throws never stops the navbar clock or the landing page.
  page.sandbox.econRoundState.subscribe(() => { throw new Error("bad subscriber"); });
  page.server.payload = input.late.payload;
  page.setNow(input.late.now);
  await page.sandbox.econRoundState.refresh();
  await flush();
  out.afterBadSubscriber = { pill: pillText(page), hero: page.view() };
  // At zero landing.js leaves the asking to round-ui.js's own tick, however
  // many of its own ticks pass.
  const refresh = page.sandbox.econRoundState.refresh;
  let asked = 0;
  page.sandbox.econRoundState.refresh = () => { asked += 1; return refresh(); };
  page.setNow(input.zeroNow);
  for (let i = 0; i < 12; i += 1) page.fire("landing", 250);
  await flush();
  for (let i = 0; i < 12; i += 1) page.fire("landing", 250);
  await flush();
  out.zeroRefreshes = asked;
  out.atZero = page.view();
  page.server.payload = input.brk.payload;
  page.setNow(input.brk.now);
  page.fire("round-ui", 15000);
  await flush();
  out.afterFlip = page.view();
  out.pillPhase = page.pill().dataset.phase;
  out.events = page.log.events.slice();
  out.frames = page.log.frames;
  return out;
};

// round-ui.js loaded but with no navbar to live in: it publishes nothing, and
// landing.js runs its own poll instead of waiting on one that never comes.
SCENARIOS.noNavbar = async (input) => {
  const page = boot({ roundUi: true, navbar: false, init: input.init, payload: input.mid.payload, now: input.mid.now });
  const out = { shared: typeof page.sandbox.econRoundState, fetches: page.log.fetches.length, intervals: page.intervals() };
  await flush();
  out.live = page.view();
  return out;
};

// round-ui.js's export on its own, on a page without the landing markup.
SCENARIOS.roundState = async (input) => {
  const out = {};
  const page = boot({ roundUi: true, landing: false, payload: input.payloads[0], now: input.now });
  const state = page.sandbox.econRoundState;
  out.intervals = page.intervals();
  out.getBefore = state.get();
  const heard = { early: [], late: [], bad: 0, gone: [] };
  state.subscribe((payload) => heard.early.push(payload.phase));
  const early = heard.early.length;
  await flush();
  out.earlyBeforeFirstPayload = early;
  out.getAfter = state.get();
  state.subscribe((payload) => heard.late.push(payload.phase));
  out.lateImmediate = heard.late.slice();
  state.subscribe(() => { heard.bad += 1; throw new Error("bad subscriber"); });
  const leave = state.subscribe((payload) => heard.gone.push(payload.phase));
  // Same phase again: every listener hears it, and no change event fires.
  const pending = state.refresh();
  out.refreshReturnsPromise = Boolean(pending) && typeof pending.then === "function";
  await pending;
  await flush();
  out.eventsAfterSamePhase = page.log.events.slice();
  leave();
  // New phase: listeners hear it and the event fires exactly once.
  page.server.payload = input.payloads[1];
  await state.refresh();
  await flush();
  // A failed refresh tells nobody and keeps the last payload.
  page.server.status = 503;
  await state.refresh();
  await flush();
  out.heard = heard;
  out.events = page.log.events.slice();
  out.getLast = state.get();
  out.pill = pillText(page);
  return out;
};

// round-ui.js's poll: what it asks for, when, and what it tells the page.
SCENARIOS.roundPoll = async (input) => {
  const page = boot({ roundUi: true, landing: false, timeline: input.timeline, now: input.boot });
  await flush();
  const count = () => page.log.fetches.length;
  const out = {
    first: [page.log.fetches[0].url, page.log.fetches[0].options],
    intervals: page.intervals(),
    listeners: page.document.listenerTypes(),
  };
  let before = count();
  page.document.hidden = true;
  page.fire("round-ui", 15000);
  await flush();
  out.pollWhileHidden = count() - before;
  before = count();
  page.document.hidden = false;
  page.document.fire("visibilitychange");
  await flush();
  out.onReturn = count() - before;
  before = count();
  page.fire("round-ui", 15000);
  await flush();
  out.pollWhileVisible = count() - before;
  // Fake time runs across the target; the server flips a moment late.
  before = count();
  await page.advance(input.window);
  out.aroundZero = page.log.fetches.slice(before).map((entry) => [entry.at - input.zero, entry.phase]);
  out.events = page.log.events.slice();
  out.pill = pillText(page);
  out.pillPhase = page.pill().dataset.phase;
  return out;
};

// round-ui.js: a reply asked for before the boundary that lands after a newer
// one is dropped, on the pill, the subscribers and the landing page alike.
SCENARIOS.roundOrder = async (input) => {
  const page = boot({ roundUi: true, payload: input.r1.payload, now: input.r1.now });
  await flush();
  const state = page.sandbox.econRoundState;
  page.server.hold = true;
  const first = state.refresh(); // asked while the server still says round 1 …
  page.server.hold = false;
  page.server.payload = input.brk.payload;
  page.setNow(input.brk.now);
  await state.refresh(); // … and overtaken by one that says break
  await flush();
  const overtaken = { phase: state.get().phase, view: page.view() };
  page.server.held.splice(0).forEach((release) => release());
  await first;
  await flush();
  page.fire("round-ui", 250);
  page.fire("landing", 250);
  return {
    overtaken,
    phase: state.get().phase,
    view: page.view(),
    pillPhase: page.pill().dataset.phase,
    events: page.log.events.slice(),
    statusWrites: page.status.writes,
  };
};

// The status line: what it says, and how often it is written, as the page
// moves through input.steps (each a new instant, and optionally a new answer
// from the server, delivered by landing.js's own poll).
SCENARIOS.announce = async (input) => {
  const [first, ...rest] = input.steps;
  const page = boot({ payload: first.payload, now: first.now });
  await flush();
  const said = [];
  const record = () => said.push({
    status: page.status.textContent,
    writes: page.status.writes,
    phase: page.stage.getAttribute("data-phase"),
    tail: page.stage.getAttribute("data-tail"),
    classes: page.hero.snapshot().classes,
  });
  record();
  for (const step of rest) {
    page.setNow(step.now);
    if ("payload" in step) {
      page.server.payload = step.payload;
      page.fire("landing", 15000);
      await flush();
    }
    for (let i = 0; i < (step.ticks || 1); i += 1) page.fire("landing", 250);
    record();
  }
  return said;
};

// The toy's 채점 box and resets, driven the way a keyboard and a mouse would.
SCENARIOS.toy = async (input) => {
  const page = boot({ row0: input.row0 });
  const { document, log } = page;
  const run = page.byId("econ-toy-run");
  const form = run.form;
  // The fail card's 처음부터 (the middle one) is the same button again.
  const [resetInPartial, , resetInPass] = form.querySelectorAll("button");
  const state = () => ({ checked: run.checked, name: run.getAttribute("aria-label") });
  const key = (init) => {
    const event = makeEvent("keydown", init);
    run.dispatchEvent(event);
    return { prevented: event.defaultPrevented, ...state() };
  };
  const answer = () => ["econ-toy-q0a", "econ-toy-q0b"].find((id) => page.byId(id).checked);
  const out = { atStart: { ...state(), writes: run.writes } };
  out.enter = key({ key: "Enter" });
  out.heldEnter = key({ key: "Enter", repeat: true });
  out.composingEnter = key({ key: "Enter", isComposing: true });
  out.space = key({ key: " " });
  out.enterAgain = key({ key: "Enter" });
  run.click(); // the label, clicked
  out.clicked = state();
  out.framesBeforeReset = log.frames;

  // 처음부터 by keyboard, after changing row 0's answer.
  page.byId(input.row0 === "a" ? "econ-toy-q0b" : "econ-toy-q0a").click();
  document.focusOn(resetInPartial, true);
  resetInPartial.click();
  out.keyboardResetBeforeFrame = { ...state(), active: document.activeElement.tagName, focusCalls: document.focusLog.length };
  page.frames();
  out.keyboardReset = { ...state(), answer: answer(), focus: document.focusLog.slice(), active: document.activeElement.id };

  // 처음부터 by mouse: the button takes focus, but no ring.
  run.click();
  document.focusLog = [];
  document.focusOn(resetInPass, false);
  resetInPass.click();
  page.frames();
  out.pointerReset = { ...state(), focus: document.focusLog.slice() };

  // A reset with focus somewhere else on the page moves nothing.
  run.click();
  document.focusLog = [];
  document.blur();
  form.reset();
  page.frames();
  out.unfocusedReset = { ...state(), focus: document.focusLog.slice() };

  const submit = makeEvent("submit", { cancelable: true });
  form.dispatchEvent(submit);
  out.submitPrevented = submit.defaultPrevented;
  out.frames = log.frames;
  return out;
};

const scenario = SCENARIOS[INPUT.scenario];
if (!scenario) {
  process.stderr.write(`no scenario ${JSON.stringify(INPUT.scenario)}`);
  process.exit(2);
}
scenario(INPUT).then(emit).catch((error) => {
  process.stderr.write(String((error && error.stack) || error));
  process.exit(1);
});
