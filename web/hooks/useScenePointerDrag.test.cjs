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

function setup(draft = source()) {
  const slots = [], effects = [], cache = new Map(), listeners = new Map();
  const notifications = [], dragging = [], captures = new Set();
  let cursor = 0, now = 1000, handlers, disposed = false;
  function element(tagName) {
    return {
      tagName: tagName.toUpperCase(), children: [], style: {}, dataset: {}, attributes: {},
      className: "", textContent: "", offsetWidth: 240, offsetHeight: 140,
      get classList() { return { add: (...names) => { this.className = [...new Set([...this.className.split(/\s+/), ...names])].join(" "); } }; },
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
  }
  const document = {
    body: element("body"), createElement: element,
    addEventListener(name, callback) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(callback); },
    removeEventListener(name, callback) { listeners.get(name)?.delete(callback); },
    dispatchEvent(value) {
      if (value.type === "scene-recall:scene-pointer") notifications.push(value.detail);
      [...(listeners.get(value.type) ?? [])].forEach((callback) => callback(value));
    },
  };
  const target = {
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
    setTimeout, clearTimeout,
  };
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
      CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
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
    get preview() { return document.body.children.find((node) => node.className.includes("scene-pointer-ghost")); },
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
    close() { if (disposed) return; disposed = true; slots.forEach((slot) => slot.cleanup?.()); },
  };
}

test("a click or slight pointer movement keeps ordinary scene interaction", (context) => {
  const ui = setup(); context.after(ui.close);
  ui.handlers.onPointerDown(ui.pointer());
  ui.handlers.onPointerMove(ui.pointer({ clientX: 104, clientY: 103 }));
  assert.equal(ui.preview, undefined);
  assert.equal(ui.notifications.length, 0);
  ui.handlers.onPointerUp(ui.pointer({ clientX: 104, clientY: 103 }));
  const click = event(); ui.handlers.onClickCapture(click);
  assert.equal(click.defaultPrevented, false);
  assert.equal(ui.captures.size, 0);
});

test("dragging carries the actual scene thumbnail, identity, time and category under the pointer", (context) => {
  const ui = setup(); context.after(ui.close);
  ui.start();
  const preview = ui.preview;
  assert.ok(preview, "the active gesture renders a visible scene preview");
  const image = descendants(preview).find((node) => node.tagName === "IMG");
  assert.equal(image?.src, "http://localhost:8000/keyframes/red-room.jpg");
  assert.equal(image.draggable, false);
  assert.match(copy(preview), /Twin Peaks/);
  assert.match(copy(preview), /1:01:18/);
  assert.match(copy(preview), /Moving Framing/);
  assert.equal(preview.attributes["aria-hidden"], "true");
  assert.equal(preview.style.left, "92px");
  assert.equal(preview.style.top, "76px");
  ui.render();
  ui.handlers.onPointerMove(ui.pointer({ clientX: 200, clientY: 220 }));
  assert.equal(ui.preview, preview, "rerenders keep the same gesture and thumbnail");
  assert.equal(preview.style.left, "172px");
  assert.equal(preview.style.top, "196px");
  assert.deepEqual(ui.notifications.map(({ phase }) => phase), ["start", "move", "move"]);
  assert.ok(ui.notifications.every(({ originFacet, draft }) => originFacet === "composition" && draft.source.unit_id === "red-room-4"));
});

test("the picked-up point tracks the cursor without flipping or drifting at viewport edges", (context) => {
  const ui = setup(); context.after(ui.close);
  ui.start();
  let previous;
  for (const [x, y] of [[795, 595], [800, 600], [-20, -20], [820, 630]]) {
    ui.handlers.onPointerMove(ui.pointer({ clientX: x, clientY: y }));
    const bounds = ui.preview.getBoundingClientRect();
    assert.equal(x - bounds.left, 28);
    assert.equal(y - bounds.top, 24);
    if (previous) {
      assert.equal(bounds.left - previous.left, x - previous.x);
      assert.equal(bounds.top - previous.top, y - previous.y);
    }
    previous = { ...bounds, x, y };
  }
});

test("native and pointer previews use the same point inside the thumbnail, including a missing-image fallback", (context) => {
  for (const imageUrl of ["/keyframes/red-room.jpg", undefined]) {
    const draft = source(); draft.display.keyframeUrl = imageUrl;
    const ui = setup(draft); context.after(ui.close);
    ui.start();
    const native = ui.nativePreview({ eyebrow: "Moving Framing", title: "Twin Peaks", imageUrl });
    assert.equal(native.x, 28); assert.equal(native.y, 24);
    const bounds = ui.preview.getBoundingClientRect();
    assert.equal(120 - bounds.left, native.x);
    assert.equal(100 - bounds.top, native.y);
    assert.match(native.preview.className, /native-drag-preview/);
    assert.match(copy(native.preview), /Twin Peaks/);
  }
});

test("one release applies one drop, removes the preview, and suppresses the trailing click", (context) => {
  const ui = setup(); context.after(ui.close);
  ui.start();
  const native = event(); ui.handlers.onDragStart(native);
  assert.equal(native.defaultPrevented, true, "a native drag cannot duplicate the pointer gesture");
  ui.handlers.onPointerUp(ui.pointer({ clientX: 330, clientY: 150 }));
  ui.handlers.onLostPointerCapture(ui.pointer());
  ui.handlers.onPointerUp(ui.pointer());
  assert.equal(ui.preview, undefined);
  assert.equal(ui.captures.size, 0);
  assert.deepEqual(ui.notifications.map(({ phase }) => phase), ["start", "move", "drop", "end"]);
  const drop = ui.notifications.find(({ phase }) => phase === "drop");
  assert.equal(drop.x, 330); assert.equal(drop.y, 150);
  assert.deepEqual(ui.dragging, [true, false]);
  const trailing = event(); ui.handlers.onClickCapture(trailing);
  assert.equal(trailing.defaultPrevented, true); assert.equal(trailing.stopped, true);
  ui.advance(300);
  const later = event(); ui.handlers.onClickCapture(later);
  assert.equal(later.defaultPrevented, false);
});

for (const reason of ["Escape", "cancel", "lost capture", "unmount"]) {
  test(`${reason} removes the carried scene without applying a drop`, (context) => {
    const ui = setup(); context.after(ui.close);
    ui.start();
    if (reason === "Escape") {
      const escape = event({ type: "keydown", key: "Escape" }); ui.document.dispatchEvent(escape);
      assert.equal(escape.defaultPrevented, true);
    } else if (reason === "cancel") ui.handlers.onPointerCancel(ui.pointer());
    else if (reason === "lost capture") ui.handlers.onLostPointerCapture(ui.pointer());
    else ui.close();
    assert.equal(ui.preview, undefined);
    assert.equal(ui.notifications.filter(({ phase }) => phase === "drop").length, 0);
    assert.equal(ui.notifications.filter(({ phase }) => phase === "end").length, 1);
    if (reason !== "unmount") {
      ui.handlers.onPointerUp(ui.pointer());
      assert.equal(ui.notifications.filter(({ phase }) => phase === "drop").length, 0);
    }
  });
}

test("unrelated pointer events cannot move, replace or cancel the scene being carried", (context) => {
  const ui = setup(); context.after(ui.close);
  ui.start();
  const preview = ui.preview;
  ui.handlers.onPointerDown(ui.pointer({ pointerId: 2 }));
  ui.handlers.onPointerMove(ui.pointer({ pointerId: 2, clientX: 400 }));
  ui.handlers.onPointerUp(ui.pointer({ pointerId: 2 }));
  ui.handlers.onPointerCancel(ui.pointer({ pointerId: 2 }));
  ui.handlers.onLostPointerCapture(ui.pointer({ pointerId: 2 }));
  assert.equal(ui.preview, preview);
  assert.equal(preview.style.left, "92px");
  assert.equal(ui.notifications.length, 2);
  ui.handlers.onPointerUp(ui.pointer({ clientX: 300 }));
  assert.equal(ui.notifications.filter(({ phase }) => phase === "drop").length, 1);
  assert.equal(ui.preview, undefined);
});
