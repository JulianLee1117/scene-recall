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
  const slot = (compute, deps) => { const i = cursor++; if (!hooks[i] || !same(deps, hooks[i].deps)) hooks[i] = { value: compute(), deps }; return hooks[i].value; };
  const react = {
    useState(initial) { const i = cursor++; hooks[i] ??= { value: initial }; return [hooks[i].value, (value) => { hooks[i].value = typeof value === "function" ? value(hooks[i].value) : value; rerender(); }]; },
    useRef(value) { return hooks[cursor++] ??= { current: value }; },
    useCallback: (callback, deps) => slot(() => callback, deps),
    useMemo: (factory, deps) => slot(factory, deps),
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
  const doc = {
    activeElement: null,
    addEventListener(type, callback) { (listeners[type] ??= []).push(callback); },
    removeEventListener(type, callback) { listeners[type] = (listeners[type] ?? []).filter((item) => item !== callback); },
  };
  vm.runInNewContext(compiled, {
    exports: exported,
    require: (name) => { if (name === "react") return react; throw new Error(name); },
    document: doc,
    window: {
      innerHeight: 800,
      setTimeout(callback) { const id = ++nextTimer; timers.set(id, callback); return id; },
      clearTimeout(id) { timers.delete(id); },
      scrollBy(x, y) { scrolled.push(y); },
    },
    performance: { now: () => now },
    Math, Number,
  });
  rerender();
  const tiles = [0, 1, 2].map((index) => {
    const tile = {
      captured: null,
      // The button inside the tile that a press focuses, as in a browser.
      button: { blurred: false, blur() { this.blurred = true; doc.activeElement = null; } },
      contains(element) { return element === tile.button; },
      getAttribute: (name) => (name === "data-board-index" ? String(index) : null),
      setPointerCapture(id) { this.captured = id; },
      hasPointerCapture(id) { return this.captured === id; },
      releasePointerCapture() { this.captured = null; },
    };
    return tile;
  });
  // A press focuses the pressed tile's button, as the browser does on mousedown.
  const press = (index, extra = {}) => { doc.activeElement = tiles[index].button; return event({ index, ...extra }); };
  const event = ({ index = 0, ...extra } = {}) => ({
    pointerId: 1, pointerType: "mouse", button: 0, isPrimary: true, clientX: 0, clientY: 25, altKey: false,
    target: { handle: true }, currentTarget: tiles[index], prevented: false, stopped: false,
    preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; }, ...extra,
  });
  return {
    calls, tiles, listeners, timers, scrolled, event, press, doc,
    get lifted() { return result.lifted; },
    get props() { return result.tileProps; },
    advance(ms) { now += ms; },
    fireHold() { const pending = [...timers.values()]; timers.clear(); pending.forEach((callback) => callback()); },
  };
}

test("one stable set of handlers serves every tile, reading the tile's index from its attribute", () => {
  const app = setup();
  const before = app.props;
  app.props.onPointerDown(app.event({ index: 2, clientX: 50 }));
  app.props.onPointerMove(app.event({ index: 2, clientX: 250 }));
  assert.equal(app.lifted, 2, "the lifted index comes from the tile, not from a per-tile closure");
  assert.equal(app.props, before, "a lift does not remake the handlers");
});

test("a mouse drag lifts after a short move, reports every move, and drops on release", () => {
  const app = setup();
  app.props.onPointerDown(app.event({ index: 2, clientX: 50 }));
  assert.equal(app.tiles[2].captured, 1, "the tile owns the pointer");
  app.props.onPointerMove(app.event({ index: 2, clientX: 53 }));
  assert.deepEqual(app.calls, [], "a few pixels is not yet a drag");
  app.props.onPointerMove(app.event({ index: 2, clientX: 250 }));
  assert.equal(app.lifted, 2);
  app.props.onPointerMove(app.event({ index: 2, clientX: 120, clientY: 60 }));
  app.props.onPointerUp(app.event({ index: 2, clientX: 120, clientY: 60 }));
  assert.deepEqual(app.calls, [["lift", 2, 250, 25], ["drag", 250, 25], ["drag", 120, 60], ["drop"]]);
  assert.equal(app.lifted, null);
  assert.equal(app.tiles[2].captured, null);
  const click = app.event({ index: 2 });
  app.props.onClickCapture(click);
  assert.ok(click.prevented && click.stopped, "the release does not open the scene");
  app.advance(400);
  const later = app.event({ index: 2 });
  app.props.onClickCapture(later);
  assert.ok(!later.prevented, "a later click is a click");
});

test("Escape puts a lifted tile back, a lost pointer cancels, and nothing starts on a control or when disabled", () => {
  const app = setup();
  app.props.onPointerDown(app.event({ clientX: 50 }));
  app.props.onPointerMove(app.event({ clientX: 250 }));
  const escape = { key: "Escape", prevented: false, preventDefault() { this.prevented = true; } };
  app.listeners.keydown.forEach((callback) => callback(escape));
  assert.ok(escape.prevented); assert.equal(app.lifted, null);
  app.props.onPointerUp(app.event({ clientX: 250 }));
  assert.deepEqual(app.calls, [["lift", 0, 250, 25], ["drag", 250, 25], ["cancel"]], "a cancelled drag never drops");

  app.props.onPointerDown(app.event({ index: 1, clientX: 150 }));
  app.props.onPointerMove(app.event({ index: 1, clientX: 300 }));
  app.props.onLostPointerCapture(app.event({ index: 1, clientX: 300 }));
  assert.deepEqual(app.calls.slice(3), [["lift", 1, 300, 25], ["drag", 300, 25], ["cancel"]]);

  app.props.onPointerDown(app.event({ clientX: 50, target: { handle: false } }));
  assert.equal(app.tiles[0].captured, null, "the bookmark button is not a handle");

  const off = setup({ enabled: false });
  off.props.onPointerDown(off.event({ clientX: 50 }));
  assert.equal(off.tiles[0].captured, null);
  off.props.onKeyDown(off.event({ altKey: true, key: "ArrowRight" }));
  assert.deepEqual(off.calls, []);
});

test("a finger scrolls unless it holds first; held, it drags and the page cannot scroll meanwhile", () => {
  const app = setup();
  app.props.onPointerDown(app.event({ pointerType: "touch", clientX: 50 }));
  assert.equal(app.timers.size, 1, "the hold is pending");
  app.props.onPointerMove(app.event({ pointerType: "touch", clientX: 50, clientY: 60 }));
  assert.equal(app.lifted, null); assert.equal(app.timers.size, 0, "a swipe before the hold is a scroll");
  assert.equal(app.tiles[0].captured, null);

  app.props.onPointerDown(app.event({ index: 1, pointerType: "touch", clientX: 150 }));
  app.fireHold();
  assert.equal(app.lifted, 1, "held: lifted before it moves");
  assert.deepEqual(app.calls, [["lift", 1, 150, 25]], "lifted where the finger rests");
  const touchmove = { prevented: false, preventDefault() { this.prevented = true; } };
  app.listeners.touchmove.forEach((callback) => callback(touchmove));
  assert.ok(touchmove.prevented, "the page stays put under a lifted tile");
  app.props.onPointerMove(app.event({ index: 1, pointerType: "touch", clientX: 20 }));
  app.props.onPointerUp(app.event({ index: 1, pointerType: "touch", clientX: 20 }));
  assert.deepEqual(app.calls.slice(1), [["drag", 20, 25], ["drop"]]);
  assert.deepEqual(app.listeners.touchmove, [], "scrolling is given back");
});

test("a drag lets go of the focus its press gave the tile; a plain click keeps it", () => {
  const app = setup();
  app.props.onPointerDown(app.press(1, { clientX: 50 }));
  app.props.onPointerMove(app.event({ index: 1, clientX: 250 }));
  app.props.onPointerUp(app.event({ index: 1, clientX: 250 }));
  assert.ok(app.tiles[1].button.blurred, "after a drop the scene shows no focused controls");
  assert.equal(app.doc.activeElement, null);

  app.props.onPointerDown(app.press(2, { clientX: 50 }));
  app.props.onPointerMove(app.event({ index: 2, clientX: 250 }));
  app.listeners.keydown.forEach((callback) => callback({ key: "Escape", preventDefault() {} }));
  assert.ok(app.tiles[2].button.blurred, "a cancelled drag too");

  app.props.onPointerDown(app.press(0, { clientX: 50 }));
  app.props.onPointerUp(app.event({ index: 0, clientX: 52 }));
  assert.ok(!app.tiles[0].button.blurred, "a click is a click: its focus stays with the browser");
});

test("Alt with an arrow asks for a move one place either way", () => {
  const app = setup();
  app.props.onKeyDown(app.event({ index: 0, altKey: true, key: "ArrowRight" }));
  app.props.onKeyDown(app.event({ index: 2, altKey: true, key: "ArrowLeft" }));
  app.props.onKeyDown(app.event({ index: 1, altKey: false, key: "ArrowRight" }));
  app.props.onKeyDown(app.event({ index: 1, altKey: true, key: "ArrowUp" }));
  assert.deepEqual(app.calls, [["key", 0, 1], ["key", 2, -1]]);
});

test("a drag near the viewport edge scrolls the page along", () => {
  const app = setup();
  app.props.onPointerDown(app.event({ clientX: 50 }));
  app.props.onPointerMove(app.event({ clientX: 150, clientY: 790 }));
  app.props.onPointerMove(app.event({ clientX: 150, clientY: 10 }));
  assert.deepEqual(app.scrolled, [14, -14]);
});
