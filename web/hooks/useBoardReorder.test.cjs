const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const plain = (value) => JSON.parse(JSON.stringify(value));
const compiled = ts.transpileModule(fs.readFileSync(path.join(__dirname, "useBoardReorder.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

/** Three tiles in one row, 100px wide each, 50px tall. */
function setup({ enabled = true, count = 3 } = {}) {
  const hooks = [], moves = [], listeners = {}, timers = new Map(), scrolled = [];
  let cursor = 0, result, nextTimer = 0, now = 1000;
  const rerender = () => { cursor = 0; result = exported.useBoardReorder({ enabled, count, onMove: (from, to) => moves.push([from, to]), isHandle: (target) => target.handle !== false }); run(); };
  const effects = [];
  const run = () => { const pending = effects.splice(0); pending.forEach((effect) => effect()); };
  const react = {
    useState(initial) { const i = cursor++; hooks[i] ??= { value: initial }; return [hooks[i].value, (value) => { hooks[i].value = typeof value === "function" ? value(hooks[i].value) : value; rerender(); }]; },
    useRef(value) { return hooks[cursor++] ??= { current: value }; },
    useCallback(callback, deps) {
      const i = cursor++;
      const same = hooks[i] && deps && hooks[i].deps && deps.length === hooks[i].deps.length && deps.every((d, k) => Object.is(d, hooks[i].deps[k]));
      if (!same) hooks[i] = { value: callback, deps };
      return hooks[i].value;
    },
    useEffect(effect, deps) {
      const i = cursor++;
      const same = hooks[i] && deps && hooks[i].deps && deps.length === hooks[i].deps.length && deps.every((d, k) => Object.is(d, hooks[i].deps[k]));
      if (!same) { const old = hooks[i]; hooks[i] = { deps }; effects.push(() => { old?.cleanup?.(); hooks[i].cleanup = effect(); }); }
    },
  };
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
    Infinity, Math,
  });
  rerender();
  const tiles = Array.from({ length: count }, (_, index) => ({
    style: {}, captured: null,
    setPointerCapture(id) { this.captured = id; },
    hasPointerCapture(id) { return this.captured === id; },
    releasePointerCapture() { this.captured = null; },
    // A lifted tile travels with the pointer, as the real one does.
    getBoundingClientRect() {
      const [, dx = "0", dy = "0"] = /translate\((-?[\d.]+)px, (-?[\d.]+)px\)/.exec(this.style.transform ?? "") ?? [];
      const left = index * 100 + Number(dx), top = Number(dy);
      return { left, right: left + 100, top, bottom: top + 50, width: 100, height: 50 };
    },
  }));
  const bind = () => tiles.forEach((tile, index) => result.tileProps(index).ref(tile));
  bind();
  const event = (index, extra) => ({
    pointerId: 1, pointerType: "mouse", button: 0, isPrimary: true, clientX: 0, clientY: 25, altKey: false,
    target: { handle: true }, currentTarget: tiles[index], prevented: false, stopped: false,
    preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; }, ...extra,
  });
  return {
    moves, tiles, listeners, timers, scrolled, event,
    get gesture() { return result.gesture; },
    props: (index) => { bind(); return result.tileProps(index); },
    advance(ms) { now += ms; },
    fireHold() { const pending = [...timers.values()]; timers.clear(); pending.forEach((callback) => callback()); },
  };
}

test("a mouse drag lifts a tile after a short move, marks the drop place, and moves on release", () => {
  const app = setup();
  app.props(0).onPointerDown(app.event(0, { clientX: 50 }));
  assert.equal(app.tiles[0].captured, 1, "the tile owns the pointer");
  app.props(0).onPointerMove(app.event(0, { clientX: 53 }));
  assert.equal(app.gesture, null, "a few pixels is not yet a drag");
  app.props(0).onPointerMove(app.event(0, { clientX: 250 }));
  assert.deepEqual(plain(app.gesture), { from: 0, over: 2, before: false }, "over the right half of the last tile");
  assert.equal(app.tiles[0].style.transform, "translate(200px, 0px)", "the lifted tile follows the pointer");
  app.props(0).onPointerMove(app.event(0, { clientX: 120 }));
  assert.deepEqual(plain(app.gesture), { from: 0, over: 1, before: true });
  app.props(0).onPointerUp(app.event(0, { clientX: 120 }));
  assert.deepEqual(app.moves, [[0, 1]], "dropping before the second tile");
  assert.equal(app.gesture, null);
  assert.equal(app.tiles[0].style.transform, "", "the tile settles back into the flow");
  assert.equal(app.tiles[0].captured, null);
  const click = app.event(0);
  app.props(0).onClickCapture(click);
  assert.ok(click.prevented && click.stopped, "the release does not open the scene");
  app.advance(400);
  const later = app.event(0);
  app.props(0).onClickCapture(later);
  assert.ok(!later.prevented, "a later click is a click");
});

test("dropping past the end or back on itself is a move to the end or nothing", () => {
  const app = setup();
  app.props(0).onPointerDown(app.event(0, { clientX: 50 }));
  app.props(0).onPointerMove(app.event(0, { clientX: 340, clientY: 120 }));
  assert.deepEqual(plain(app.gesture), { from: 0, over: 2, before: false }, "below and beyond the row still finds the nearest tile");
  app.props(0).onPointerUp(app.event(0, { clientX: 340, clientY: 120 }));
  assert.deepEqual(app.moves, [[0, 3]]);
  app.props(1).onPointerDown(app.event(1, { clientX: 150 }));
  app.props(1).onPointerMove(app.event(1, { clientX: 170 }));
  app.props(1).onPointerUp(app.event(1, { clientX: 170 }));
  assert.deepEqual(app.moves, [[0, 3], [1, 2]], "the view treats a drop just after itself as no change");
});

test("Escape cancels a lift, a press on a control inside the tile never starts one, and nothing moves when disabled", () => {
  const app = setup();
  app.props(0).onPointerDown(app.event(0, { clientX: 50 }));
  app.props(0).onPointerMove(app.event(0, { clientX: 250 }));
  assert.ok(app.gesture);
  const escape = { key: "Escape", prevented: false, preventDefault() { this.prevented = true; } };
  app.listeners.keydown.forEach((callback) => callback(escape));
  assert.ok(escape.prevented); assert.equal(app.gesture, null);
  app.props(0).onPointerUp(app.event(0, { clientX: 250 }));
  assert.deepEqual(app.moves, [], "a cancelled drag drops nothing");

  app.props(0).onPointerDown(app.event(0, { clientX: 50, target: { handle: false } }));
  assert.equal(app.tiles[0].captured, null, "the bookmark button is not a handle");

  const off = setup({ enabled: false });
  off.props(0).onPointerDown(off.event(0, { clientX: 50 }));
  assert.equal(off.tiles[0].captured, null);
  off.props(0).onKeyDown(off.event(0, { altKey: true, key: "ArrowRight" }));
  assert.deepEqual(off.moves, []);
});

test("a finger scrolls unless it holds first; held, it drags like a mouse and the page cannot scroll meanwhile", () => {
  const app = setup();
  app.props(0).onPointerDown(app.event(0, { pointerType: "touch", clientX: 50 }));
  assert.equal(app.timers.size, 1, "the hold is pending");
  app.props(0).onPointerMove(app.event(0, { pointerType: "touch", clientX: 50, clientY: 60 }));
  assert.equal(app.gesture, null); assert.equal(app.timers.size, 0, "a swipe before the hold is a scroll");
  assert.equal(app.tiles[0].captured, null);

  app.props(1).onPointerDown(app.event(1, { pointerType: "touch", clientX: 150 }));
  app.fireHold();
  assert.deepEqual(plain(app.gesture), { from: 1, over: null, before: false }, "held: lifted before it moves");
  const touchmove = { prevented: false, preventDefault() { this.prevented = true; } };
  app.listeners.touchmove.forEach((callback) => callback(touchmove));
  assert.ok(touchmove.prevented, "the page stays put under a lifted tile");
  app.props(1).onPointerMove(app.event(1, { pointerType: "touch", clientX: 20 }));
  assert.deepEqual(plain(app.gesture), { from: 1, over: 0, before: true });
  app.props(1).onPointerUp(app.event(1, { pointerType: "touch", clientX: 20 }));
  assert.deepEqual(app.moves, [[1, 0]]);
  assert.deepEqual(app.listeners.touchmove, [], "scrolling is given back");
});

test("Alt with an arrow moves a tile one place either way, within the board", () => {
  const app = setup();
  app.props(0).onKeyDown(app.event(0, { altKey: true, key: "ArrowRight" }));
  app.props(0).onKeyDown(app.event(0, { altKey: true, key: "ArrowLeft" }));
  app.props(2).onKeyDown(app.event(2, { altKey: true, key: "ArrowLeft" }));
  app.props(2).onKeyDown(app.event(2, { altKey: true, key: "ArrowRight" }));
  app.props(1).onKeyDown(app.event(1, { altKey: false, key: "ArrowRight" }));
  assert.deepEqual(app.moves, [[0, 2], [2, 1]], "first right, last left; nothing off the ends or without Alt");
});

test("a drag near the viewport edge scrolls the page along", () => {
  const app = setup();
  app.props(0).onPointerDown(app.event(0, { clientX: 50 }));
  app.props(0).onPointerMove(app.event(0, { clientX: 150, clientY: 790 }));
  app.props(0).onPointerMove(app.event(0, { clientX: 150, clientY: 10 }));
  assert.deepEqual(app.scrolled, [14, -14]);
});
