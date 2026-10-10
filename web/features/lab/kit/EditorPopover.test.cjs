const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const compile = (file) => ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const positions = {};
vm.runInNewContext(compile("editorPopoverPosition.ts"), { exports: positions });

test("panels fit below their anchor without changing the page and flip above a low anchor", () => {
  const below = positions.editorPopoverPosition({ left: 500, right: 540, top: 40, bottom: 70 }, 180, { width: 900, height: 700 }, 360, "end");
  assert.equal(below.left, 180);
  assert.equal(below.top, 77);
  const above = positions.editorPopoverPosition({ left: 500, right: 540, top: 610, bottom: 640 }, 180, { width: 900, height: 700 }, 360, "end");
  assert.equal(above.top, 423);
});

test("narrow and short viewports constrain width and use internal scrolling", () => {
  const panel = positions.editorPopoverPosition({ left: 300, right: 358, top: 120, bottom: 152 }, 1000, { width: 390, height: 400 }, 520, "start");
  assert.equal(panel.width, 366);
  assert.equal(panel.left, 12);
  assert.equal(panel.top, 159);
  assert.equal(panel.maxHeight, 229);
  assert.ok(panel.left + panel.width <= 378);
  assert.ok(panel.top + panel.maxHeight <= 388);
});

async function setup() {
  const hooks = [], effects = [], changes = [], listeners = new Map();
  let cursor = 0, scheduled = false, tree;
  class FakeNode {
    constructor() { this.open = false; this.style = {}; this.scrollHeight = 240; this.focusCalls = []; }
    matches() { return this.open; }
    showPopover() { this.open = true; }
    hidePopover() { this.open = false; }
    contains(node) { return node === this || node.parent === this; }
    closest() { return this; }
    getBoundingClientRect() { return { left: 200, right: 240, top: 30, bottom: 60 }; }
    focus(options) { this.focusCalls.push(options); }
  }
  const button = new FakeNode(), panel = new FakeNode();
  const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
  const schedule = () => { if (!scheduled) { scheduled = true; queueMicrotask(render); } };
  const react = {
    useId: () => "popover",
    useRef(initial) { return hooks[cursor++] ??= { current: initial }; },
    useState(initial) { const i = cursor++; hooks[i] ??= { value: initial }; return [hooks[i].value, (value) => { if (hooks[i].value !== value) { hooks[i].value = value; schedule(); } }]; },
    useLayoutEffect(effect, deps) {
      const i = cursor++;
      if (!hooks[i] || !same(hooks[i].deps, deps)) {
        const before = hooks[i]; hooks[i] = { deps, cleanup: before?.cleanup };
        effects.push(() => { hooks[i].cleanup?.(); hooks[i].cleanup = effect(); });
      }
    },
  };
  const exports = {};
  vm.runInNewContext(compile("EditorPopover.tsx"), {
    exports, Node: FakeNode, ResizeObserver: class { observe() {} disconnect() {} },
    window: { innerWidth: 800, innerHeight: 600, addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: (name) => listeners.delete(name) },
    require(name) {
      if (name === "react") return react;
      if (name === "react/jsx-runtime") return { Fragment: "fragment", jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
      if (name === "./editorPopoverPosition") return positions;
      return { default: name };
    },
  });
  function render() {
    scheduled = false; cursor = 0;
    tree = exports.default({ title: "Tools", label: "More", onOpenChange: (open) => changes.push(open), children: "Settings" });
    tree.props.children[0].props.ref.current = button;
    tree.props.children[1].props.ref.current = panel;
    effects.splice(0).forEach((effect) => effect());
  }
  const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
  render(); await flush();
  return { button, panel, changes, listeners, flush,
    get triggerProps() { return tree.props.children[0].props; },
    get panelProps() { return tree.props.children[1].props; },
    cleanup() { hooks.forEach((hook) => hook.cleanup?.()); },
  };
}

test("opening positions the native top-layer panel and Escape restores trigger focus without scrolling", async () => {
  const app = await setup();
  try {
    assert.equal(app.triggerProps["aria-expanded"], false);
    app.triggerProps.onClick(); await app.flush();
    assert.equal(app.panel.open, true);
    assert.equal(app.triggerProps["aria-expanded"], true);
    assert.equal(app.panel.style.top, "67px");
    assert.equal(app.panel.focusCalls[0].preventScroll, true);
    let prevented = false, stopped = false;
    app.panelProps.onKeyDown({ key: "Escape", target: app.panel, preventDefault() { prevented = true; }, stopPropagation() { stopped = true; } }); await app.flush();
    assert.equal(app.panel.open, false);
    assert.equal(prevented, true);
    assert.equal(stopped, true);
    assert.equal(app.button.focusCalls.at(-1).preventScroll, true);
    assert.equal(app.listeners.size, 0);
  } finally { app.cleanup(); }
});

test("native outside dismissal and tabbing away close without stealing the next control’s focus", async () => {
  const app = await setup();
  try {
    app.triggerProps.onClick(); await app.flush();
    app.panel.hidePopover();
    app.panelProps.onToggle({ currentTarget: app.panel }); await app.flush();
    assert.equal(app.triggerProps["aria-expanded"], false);
    assert.equal(app.button.focusCalls.length, 0);
    app.triggerProps.onClick(); await app.flush();
    app.panelProps.onBlur({ relatedTarget: app.button, currentTarget: app.panel }); await app.flush();
    assert.equal(app.panel.open, true, "returning to the trigger must not race its toggle click");
    const next = Object.create(Object.getPrototypeOf(app.button));
    app.panelProps.onBlur({ relatedTarget: next, currentTarget: app.panel }); await app.flush();
    assert.equal(app.panel.open, false);
    assert.equal(app.button.focusCalls.length, 0);
  } finally { app.cleanup(); }
});
