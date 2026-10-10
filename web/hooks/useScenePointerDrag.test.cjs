const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const source = () => ({
  kind: "source", facet: "composition",
  source: { unit_id: "red-room-4", frame_index: 1 },
  display: { filmTitle: "Twin Peaks", timestamp: 3678, keyframeUrl: "/keyframes/red-room.jpg" },
});
const event = (extra = {}) => ({
  defaultPrevented: false, stopped: false,
  preventDefault() { this.defaultPrevented = true; },
  stopPropagation() { this.stopped = true; },
  ...extra,
});
const descendants = (element) => [element, ...element.children.flatMap(descendants)];
const copy = (element) => descendants(element).map((node) => node.textContent).filter(Boolean).join(" ");
// The pressed card's own picture: 240 by 135 at (40, 40).
const PICTURE = { left: 40, top: 40, width: 240, height: 135, right: 280, bottom: 175 };

function setup(draft = source(), { picture = true } = {}) {
  const slots = [], effects = [], cache = new Map(), listeners = new Map(), timers = [];
  const notifications = [], dragging = [], captures = new Set();
  let cursor = 0, now = 1000, handlers, disposed = false;
  function element(tagName) {
    const node = {
      tagName: tagName.toUpperCase(), children: [], dataset: {}, attributes: {},
      style: { setProperty(name, value) { this[name] = value; } },
      className: "", textContent: "", offsetWidth: 240, offsetHeight: 140,
      setAttribute(name, value) { this.attributes[name] = value; },
      append(...children) { children.forEach((child) => { child.parentElement = this; this.children.push(child); }); },
      remove() {
        if (!this.parentElement) return;
        this.parentElement.children = this.parentElement.children.filter((child) => child !== this);
        this.parentElement = null;
      },
      getBoundingClientRect() {
        const left = Number.parseFloat(this.style.left) || 0, top = Number.parseFloat(this.style.top) || 0;
        return { left, top, width: this.offsetWidth, height: this.offsetHeight, right: left + this.offsetWidth, bottom: top + this.offsetHeight };
      },
    };
    const names = () => node.className.split(/\s+/).filter(Boolean);
    node.classList = {
      add: (...added) => { node.className = [...new Set([...names(), ...added])].join(" "); },
      remove: (...removed) => { node.className = names().filter((name) => !removed.includes(name)).join(" "); },
      contains: (name) => names().includes(name),
      toggle(name, force) { const on = force ?? !names().includes(name); if (on) this.add(name); else this.remove(name); return on; },
    };
    return node;
  }
  const document = {
    body: element("body"), createElement: element,
    addEventListener(name, callback) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(callback); },
    removeEventListener(name, callback) { listeners.get(name)?.delete(callback); },
    dispatchEvent(value) {
      if (value.type === "scene-recall:scene-pointer") notifications.push(value.detail);
      [...(listeners.get(value.type) ?? [])].forEach((callback) => callback(value));
      return !value.defaultPrevented;
    },
  };
  const image = { src: "http://localhost:8000/thumbs/red-room.jpg", currentSrc: "", getBoundingClientRect: () => ({ ...PICTURE }) };
  const target = {
    querySelector(selector) { return selector === "img" && picture ? image : null; },
    setPointerCapture(id) { captures.add(id); },
    hasPointerCapture(id) { return captures.has(id); },
    releasePointerCapture(id) { captures.delete(id); },
  };
  const react = {
    useRef(initial) { return slots[cursor++] ??= { current: initial }; },
    useEffect(effect, deps) {
      const slot = slots[cursor++] ??= {};
      if (deps && slot.deps && deps.length === slot.deps.length && deps.every((value, index) => Object.is(value, slot.deps[index]))) return;
      slot.deps = deps;
      effects.push(() => { slot.cleanup?.(); slot.cleanup = effect(); });
    },
  };
  const window = {
    innerWidth: 800, innerHeight: 600,
    addEventListener: document.addEventListener, removeEventListener: document.removeEventListener,
    setTimeout(callback, ms) { timers.push({ callback, ms }); return timers.length; },
    clearTimeout() {},
  };
  class CustomEvent {
    constructor(type, options) { this.type = type; this.detail = options.detail; this.cancelable = Boolean(options.cancelable); this.defaultPrevented = false; }
    preventDefault() { if (this.cancelable) this.defaultPrevented = true; }
  }
  function load(filename) {
    filename = path.resolve(filename);
    if (cache.has(filename)) return cache.get(filename);
    const exports = {};
    cache.set(filename, exports);
    const compiled = ts.transpileModule(fs.readFileSync(filename, "utf8"), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    vm.runInNewContext(compiled, {
      exports, document, window, performance: { now: () => now }, process: { env: { NEXT_PUBLIC_API_URL: "http://localhost:8000" } },
      CustomEvent, Math, Number, Boolean,
      require(name) {
        if (name === "react") return react;
        if (name.startsWith("@/")) return load(path.join(__dirname, "..", `${name.slice(2)}.ts`));
        if (name.startsWith(".")) return load(path.resolve(path.dirname(filename), `${name}.ts`));
        throw new Error(`Unexpected module: ${name}`);
      },
    });
    return exports;
  }
  const { useScenePointerDrag } = load(path.join(__dirname, "useScenePointerDrag.ts"));
  function render() {
    cursor = 0;
    handlers = useScenePointerDrag(draft, { originFacet: "composition", onDragging: (active) => dragging.push(active) });
    effects.splice(0).forEach((run) => run());
  }
  render();
  return {
    document, notifications, dragging, captures, render,
    get handlers() { return handlers; },
    get carry() { return document.body.children.find((node) => node.classList.contains("scene-carry")); },
    /** A target that takes every drop. */
    takeDrops() { document.addEventListener("scene-recall:scene-pointer", (value) => { if (value.detail.phase === "drop") value.preventDefault(); }); },
    nativePreview(options) {
      let captured;
      load(path.join(__dirname, "../lib/nativeDragPreview.ts")).setNativeDragPreview({
        setDragImage(preview, x, y) { captured = { preview, x, y }; },
      }, options);
      return captured;
    },
    pointer(extra = {}) { return event({ currentTarget: target, pointerId: 1, isPrimary: true, button: 0, clientX: 100, clientY: 100, ...extra }); },
    start() { handlers.onPointerDown(this.pointer()); handlers.onPointerMove(this.pointer({ clientX: 120 })); },
    advance(ms) { now += ms; },
    /** Lets the settled picture's removal run. */
    flush() { timers.splice(0).forEach(({ callback }) => callback()); },
    close() { if (disposed) return; disposed = true; slots.forEach((slot) => slot.cleanup?.()); },
  };
}

test("a click or slight pointer movement keeps ordinary scene interaction", (context) => {
  const ui = setup(); context.after(ui.close);
  ui.handlers.onPointerDown(ui.pointer());
  ui.handlers.onPointerMove(ui.pointer({ clientX: 104, clientY: 103 }));
  assert.equal(ui.carry, undefined);
  assert.equal(ui.notifications.length, 0);
  ui.handlers.onPointerUp(ui.pointer({ clientX: 104, clientY: 103 }));
  const click = event(); ui.handlers.onClickCapture(click);
  assert.equal(click.defaultPrevented, false);
  assert.equal(ui.captures.size, 0);
});

test("a drag lifts the scene's own picture off its card, grabbed where it was pressed, and carries it under the pointer", (context) => {
  const ui = setup(); context.after(ui.close);
  ui.start();
  const carry = ui.carry;
  assert.ok(carry, "the active gesture carries a picture");
  assert.equal(carry.attributes["aria-hidden"], "true");
  const image = descendants(carry).find((node) => node.tagName === "IMG");
  assert.equal(image?.src, "http://localhost:8000/thumbs/red-room.jpg", "the card's own picture, not a copy from the draft");
  assert.equal(image.draggable, false);
  assert.equal(carry.style.width, "240px", "laid out at the picture's size");
  assert.equal(carry.style.height, "135px");
  assert.ok(carry.style.transformOrigin.startsWith("25% 44.44"), "shrinks around the pressed point");
  assert.ok(carry.classList.contains("is-up"));
  assert.equal(carry.style["--carry-scale"], "0.7", "to hand size: 168 of 240");
  assert.equal(carry.style.left, "60px", "the pressed point stays under the pointer");
  assert.equal(carry.style.top, "40px");
  ui.render();
  ui.handlers.onPointerMove(ui.pointer({ clientX: 200, clientY: 220 }));
  assert.equal(ui.carry, carry, "rerenders keep the same gesture and picture");
  assert.equal(carry.style.left, "140px");
  assert.equal(carry.style.top, "160px");
  assert.deepEqual(ui.notifications.map(({ phase }) => phase), ["start", "move", "move"]);
  assert.ok(ui.notifications.every(({ originFacet, draft }) => originFacet === "composition" && draft.source.unit_id === "red-room-4"));
});

test("the grabbed point tracks the cursor without flipping or drifting at viewport edges", (context) => {
  const ui = setup(); context.after(ui.close);
  ui.start();
  let previous;
  for (const [x, y] of [[795, 595], [800, 600], [-20, -20], [820, 630]]) {
    ui.handlers.onPointerMove(ui.pointer({ clientX: x, clientY: y }));
    const bounds = ui.carry.getBoundingClientRect();
    assert.equal(x - bounds.left, 60);
    assert.equal(y - bounds.top, 60);
    if (previous) {
      assert.equal(bounds.left - previous.left, x - previous.x);
      assert.equal(bounds.top - previous.top, y - previous.y);
    }
    previous = { ...bounds, x, y };
  }
});

test("without a picture of its own, the carry shows the draft's keyframe at a small size", (context) => {
  const ui = setup(source(), { picture: false }); context.after(ui.close);
  ui.start();
  const carry = ui.carry;
  const image = descendants(carry).find((node) => node.tagName === "IMG");
  assert.equal(image?.src, "http://localhost:8000/keyframes/red-room.jpg");
  assert.equal(carry.style.width, "96px");
  assert.equal(carry.style.height, "54px");
  assert.equal(carry.style.transformOrigin, "50% 50%");
  assert.equal(carry.style["--carry-scale"], "1", "already small: no shrink");
  assert.equal(carry.style.left, "72px", "centred under the pointer once it moves");
  assert.equal(carry.style.top, "73px");
});

test("the native drag image still shows the scene card with its point inside the thumbnail", (context) => {
  const ui = setup(); context.after(ui.close);
  const native = ui.nativePreview({ eyebrow: "Moving Framing", title: "Twin Peaks", imageUrl: "/keyframes/red-room.jpg" });
  assert.equal(native.x, 28); assert.equal(native.y, 24);
  assert.match(native.preview.className, /native-drag-preview/);
  assert.match(copy(native.preview), /Twin Peaks/);
  assert.match(copy(native.preview), /Moving Framing/);
});

test("a drop a target takes applies once, collapses the picture into it, and suppresses the trailing click", (context) => {
  const ui = setup(); context.after(ui.close);
  ui.takeDrops();
  ui.start();
  const native = event(); ui.handlers.onDragStart(native);
  assert.equal(native.defaultPrevented, true, "a native drag cannot duplicate the pointer gesture");
  const carry = ui.carry;
  ui.handlers.onPointerUp(ui.pointer({ clientX: 330, clientY: 150 }));
  ui.handlers.onLostPointerCapture(ui.pointer());
  ui.handlers.onPointerUp(ui.pointer());
  assert.deepEqual(ui.notifications.map(({ phase }) => phase), ["start", "move", "drop", "end"]);
  const drop = ui.notifications.find(({ phase }) => phase === "drop");
  assert.equal(drop.x, 330); assert.equal(drop.y, 150);
  assert.ok(carry.classList.contains("is-taken"), "the picture collapses where it was dropped");
  assert.equal(ui.carry, carry, "still visible while it collapses");
  ui.flush();
  assert.equal(ui.carry, undefined);
  assert.equal(ui.captures.size, 0);
  assert.deepEqual(ui.dragging, [true, false]);
  const trailing = event(); ui.handlers.onClickCapture(trailing);
  assert.equal(trailing.defaultPrevented, true); assert.equal(trailing.stopped, true);
  ui.advance(300);
  const later = event(); ui.handlers.onClickCapture(later);
  assert.equal(later.defaultPrevented, false);
});

test("a drop nothing takes flies the picture back to its card", (context) => {
  const ui = setup(); context.after(ui.close);
  ui.start();
  const carry = ui.carry;
  carry.classList.add("is-over", "is-removing"); // as a target may have marked it on the way
  ui.handlers.onPointerUp(ui.pointer({ clientX: 330, clientY: 150 }));
  assert.equal(ui.notifications.filter(({ phase }) => phase === "drop").length, 1, "targets still hear the drop");
  assert.equal(carry.className, "scene-carry is-returning", "only the return remains: no shadow, no target marks");
  assert.equal(carry.style.left, "40px"); assert.equal(carry.style.top, "40px");
  ui.flush();
  assert.equal(ui.carry, undefined);
});

for (const reason of ["Escape", "cancel", "lost capture", "unmount"]) {
  test(`${reason} returns the carried picture without applying a drop`, (context) => {
    const ui = setup(); context.after(ui.close);
    ui.start();
    const carry = ui.carry;
    if (reason === "Escape") {
      const escape = event({ type: "keydown", key: "Escape" }); ui.document.dispatchEvent(escape);
      assert.equal(escape.defaultPrevented, true);
    } else if (reason === "cancel") ui.handlers.onPointerCancel(ui.pointer());
    else if (reason === "lost capture") ui.handlers.onLostPointerCapture(ui.pointer());
    else ui.close();
    if (reason === "unmount") assert.equal(ui.carry, undefined, "gone at once");
    else {
      assert.ok(carry.classList.contains("is-returning"), "flies back to its card");
      ui.flush();
      assert.equal(ui.carry, undefined);
    }
    assert.equal(ui.notifications.filter(({ phase }) => phase === "drop").length, 0);
    assert.equal(ui.notifications.filter(({ phase }) => phase === "end").length, 1);
    if (reason !== "unmount") {
      ui.handlers.onPointerUp(ui.pointer());
      assert.equal(ui.notifications.filter(({ phase }) => phase === "drop").length, 0);
      assert.equal(ui.captures.size, 0);
    }
  });
}
