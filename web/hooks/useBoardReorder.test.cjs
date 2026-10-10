const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const compiled = ts.transpileModule(fs.readFileSync(path.join(__dirname, "useBoardReorder.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

function setup({ enabled = true } = {}) {
  const hooks = [], calls = [], listeners = {}, timers = new Map(), scrolled = [];
  let cursor = 0, result, nextTimer = 0, now = 1000;
  const effects = [];
  const run = () => { effects.splice(0).forEach((effect) => effect()); };
  const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
  const react = {
    useState(initial) { const i = cursor++; hooks[i] ??= { value: initial }; return [hooks[i].value, (value) => { hooks[i].value = typeof value === "function" ? value(hooks[i].value) : value; rerender(); }]; },
    useRef(value) { return hooks[cursor++] ??= { current: value }; },
    useCallback(callback, deps) { const i = cursor++; if (!hooks[i] || !same(deps, hooks[i].deps)) hooks[i] = { value: callback, deps }; return hooks[i].value; },
    useEffect(effect, deps) {
      const i = cursor++;
      if (!hooks[i] || !same(deps, hooks[i].deps)) { const old = hooks[i]; hooks[i] = { deps }; effects.push(() => { old?.cleanup?.(); hooks[i].cleanup = effect(); }); }
    },
  };
  const options = {
    enabled, isHandle: (target) => target.handle !== false,
    onLift: (index, x, y) => calls.push(["lift", index, x, y]),
    onDrag: (x, y) => calls.push(["drag", x, y]),
    onDrop: () => calls.push(["drop"]),
    onCancel: () => calls.push(["cancel"]),
    onKeyMove: (index, direction) => calls.push(["key", index, direction]),
  };
  const rerender = () => { cursor = 0; result = exported.useBoardReorder(options); run(); };
  const exported = {};
  vm.runInNewContext(compiled, {
    exports: exported,
    require: (name) => { if (name === "react") return react; throw new Error(name); },
    document: {
      addEventListener(type, callback) { (listeners[type] ??= []).push(callback); },
      removeEventListener(type, callback) { listeners[type] = (listeners[type] ?? []).filter((item) => item !== callback); },
    },
    window: {
      innerHeight: 800,
      setTimeout(callback) { const id = ++nextTimer; timers.set(id, callback); return id; },
      clearTimeout(id) { timers.delete(id); },
      scrollBy(x, y) { scrolled.push(y); },
    },
    performance: { now: () => now },
    Math,
  });
  rerender();
  const tile = { captured: null, setPointerCapture(id) { this.captured = id; }, hasPointerCapture(id) { return this.captured === id; }, releasePointerCapture() { this.captured = null; } };
  const event = (extra) => ({
    pointerId: 1, pointerType: "mouse", button: 0, isPrimary: true, clientX: 0, clientY: 25, altKey: false,
    target: { handle: true }, currentTarget: tile, prevented: false, stopped: false,
    preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; }, ...extra,
  });
  return {
    calls, tile, listeners, timers, scrolled, event,
    get lifted() { return result.lifted; },
    props: (index) => result.tileProps(index),
    advance(ms) { now += ms; },
    fireHold() { const pending = [...timers.values()]; timers.clear(); pending.forEach((callback) => callback()); },
  };
}

test("a mouse drag lifts after a short move, reports every move, and drops on release", () => {
  const app = setup();
  app.props(2).onPointerDown(app.event({ clientX: 50 }));
  assert.equal(app.tile.captured, 1, "the tile owns the pointer");
  app.props(2).onPointerMove(app.event({ clientX: 53 }));
  assert.deepEqual(app.calls, [], "a few pixels is not yet a drag");
  app.props(2).onPointerMove(app.event({ clientX: 250 }));
  assert.equal(app.lifted, 2);
  app.props(2).onPointerMove(app.event({ clientX: 120, clientY: 60 }));
  app.props(2).onPointerUp(app.event({ clientX: 120, clientY: 60 }));
  assert.deepEqual(app.calls, [["lift", 2, 250, 25], ["drag", 250, 25], ["drag", 120, 60], ["drop"]]);
  assert.equal(app.lifted, null);
  assert.equal(app.tile.captured, null);
  const click = app.event({});
  app.props(2).onClickCapture(click);
  assert.ok(click.prevented && click.stopped, "the release does not open the scene");
  app.advance(400);
  const later = app.event({});
  app.props(2).onClickCapture(later);
  assert.ok(!later.prevented, "a later click is a click");
});

test("Escape puts a lifted tile back, a lost pointer cancels, and nothing starts on a control or when disabled", () => {
  const app = setup();
  app.props(0).onPointerDown(app.event({ clientX: 50 }));
  app.props(0).onPointerMove(app.event({ clientX: 250 }));
  const escape = { key: "Escape", prevented: false, preventDefault() { this.prevented = true; } };
  app.listeners.keydown.forEach((callback) => callback(escape));
  assert.ok(escape.prevented); assert.equal(app.lifted, null);
  app.props(0).onPointerUp(app.event({ clientX: 250 }));
  assert.deepEqual(app.calls, [["lift", 0, 250, 25], ["drag", 250, 25], ["cancel"]], "a cancelled drag never drops");

  app.props(1).onPointerDown(app.event({ clientX: 150 }));
  app.props(1).onPointerMove(app.event({ clientX: 300 }));
  app.props(1).onPointerCancel(app.event({ clientX: 300 }));
  assert.deepEqual(app.calls.slice(3), [["lift", 1, 300, 25], ["drag", 300, 25], ["cancel"]]);

  app.props(0).onPointerDown(app.event({ clientX: 50, target: { handle: false } }));
  assert.equal(app.tile.captured, null, "the bookmark button is not a handle");

  const off = setup({ enabled: false });
  off.props(0).onPointerDown(off.event({ clientX: 50 }));
  assert.equal(off.tile.captured, null);
  off.props(0).onKeyDown(off.event({ altKey: true, key: "ArrowRight" }));
  assert.deepEqual(off.calls, []);
});

test("a finger scrolls unless it holds first; held, it drags and the page cannot scroll meanwhile", () => {
  const app = setup();
  app.props(0).onPointerDown(app.event({ pointerType: "touch", clientX: 50 }));
  assert.equal(app.timers.size, 1, "the hold is pending");
  app.props(0).onPointerMove(app.event({ pointerType: "touch", clientX: 50, clientY: 60 }));
  assert.equal(app.lifted, null); assert.equal(app.timers.size, 0, "a swipe before the hold is a scroll");
  assert.equal(app.tile.captured, null);

  app.props(1).onPointerDown(app.event({ pointerType: "touch", clientX: 150 }));
  app.fireHold();
  assert.equal(app.lifted, 1, "held: lifted before it moves");
  assert.deepEqual(app.calls, [["lift", 1, 150, 25]], "lifted where the finger rests");
  const touchmove = { prevented: false, preventDefault() { this.prevented = true; } };
  app.listeners.touchmove.forEach((callback) => callback(touchmove));
  assert.ok(touchmove.prevented, "the page stays put under a lifted tile");
  app.props(1).onPointerMove(app.event({ pointerType: "touch", clientX: 20 }));
  app.props(1).onPointerUp(app.event({ pointerType: "touch", clientX: 20 }));
  assert.deepEqual(app.calls.slice(1), [["drag", 20, 25], ["drop"]]);
  assert.deepEqual(app.listeners.touchmove, [], "scrolling is given back");
});

test("Alt with an arrow asks for a move one place either way", () => {
  const app = setup();
  app.props(0).onKeyDown(app.event({ altKey: true, key: "ArrowRight" }));
  app.props(2).onKeyDown(app.event({ altKey: true, key: "ArrowLeft" }));
  app.props(1).onKeyDown(app.event({ altKey: false, key: "ArrowRight" }));
  app.props(1).onKeyDown(app.event({ altKey: true, key: "ArrowUp" }));
  assert.deepEqual(app.calls, [["key", 0, 1], ["key", 2, -1]]);
});

test("a drag near the viewport edge scrolls the page along", () => {
  const app = setup();
  app.props(0).onPointerDown(app.event({ clientX: 50 }));
  app.props(0).onPointerMove(app.event({ clientX: 150, clientY: 790 }));
  app.props(0).onPointerMove(app.event({ clientX: 150, clientY: 10 }));
  assert.deepEqual(app.scrolled, [14, -14]);
});
