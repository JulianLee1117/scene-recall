const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const compiled = ts.transpileModule(fs.readFileSync(path.join(__dirname, "DebugModeButton.tsx"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node)
  ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const same = (a, b) => a && b && a.length === b.length && a.every((value, index) => Object.is(value, b[index]));

function setup({ enabled = false, reducedMotion = false } = {}) {
  const hooks = [], changes = [], listeners = new Set();
  let cursor = 0, output, effects = [], scheduled = false, disposed = false;
  const schedule = () => { if (!scheduled && !disposed) { scheduled = true; queueMicrotask(render); } };
  const props = { enabled, onChange(next) { changes.push(next); props.enabled = next; schedule(); } };
  const media = {
    matches: reducedMotion,
    addEventListener(type, listener) { assert.equal(type, "change"); listeners.add(listener); },
    removeEventListener(type, listener) { assert.equal(type, "change"); listeners.delete(listener); },
  };
  const react = {
    useId() { const index = cursor++; return hooks[index] ??= `debug-${index}`; },
    useRef(initial) { return hooks[cursor++] ??= { current: initial }; },
    useState(initial) {
      const index = cursor++;
      hooks[index] ??= { value: typeof initial === "function" ? initial() : initial };
      return [hooks[index].value, (update) => {
        const value = typeof update === "function" ? update(hooks[index].value) : update;
        if (!Object.is(value, hooks[index].value)) { hooks[index].value = value; schedule(); }
      }];
    },
    useCallback(callback, deps) {
      const index = cursor++;
      if (!hooks[index] || !same(deps, hooks[index].deps)) hooks[index] = { value: callback, deps };
      return hooks[index].value;
    },
    useEffect(effect, deps) {
      const index = cursor++;
      if (!hooks[index] || !same(deps, hooks[index].deps)) {
        const old = hooks[index]; hooks[index] = { deps, cleanup: old?.cleanup };
        effects.push(() => { hooks[index].cleanup?.(); hooks[index].cleanup = effect(); });
      }
    },
  };
  const exports = {};
  const jsx = (type, props, key) => ({ type, props, key });
  vm.runInNewContext(compiled, { exports, window: { matchMedia(query) {
    assert.equal(query, "(prefers-reduced-motion: reduce)"); return media;
  } }, require(name) {
    if (name === "react") return react;
    if (name === "react/jsx-runtime") return { jsx, jsxs: jsx, Fragment: "fragment" };
    if (name.endsWith(".css")) return { default: new Proxy({}, { get: (_target, key) => key }) };
    throw new Error(`Unexpected module ${name}`);
  } });
  function render() {
    if (disposed) return;
    scheduled = false; cursor = 0; output = exports.default(props);
    const run = effects; effects = []; run.forEach((effect) => effect());
  }
  const flush = async () => { for (let index = 0; index < 20; index++) await Promise.resolve(); };
  render();
  return {
    changes, listeners, props, flush,
    button: () => nodes(output).find((node) => node.type === "button" && node.props["aria-label"] === "Debug mode"),
    impact: () => nodes(output).find((node) => node.props?.["data-debug-impact"]),
    keys: () => nodes(output).filter((node) => node.key != null).map((node) => node.key),
    async click() { this.button().props.onClick(); await flush(); },
    async motion(matches) { media.matches = matches; [...listeners].forEach((listener) => listener({ matches })); await flush(); },
    dispose() { disposed = true; hooks.forEach((hook) => hook?.cleanup?.()); },
  };
}

test("debug toggles immediately and rapid toggles never cancel or remount an active burst", async () => {
  const app = setup();
  try {
    app.button().props.onMouseEnter?.(); app.button().props.onPointerEnter?.(); await app.flush();
    assert.notEqual(app.button().props["data-firing"], true, "hover does not launch a burst");
    assert.deepEqual(app.changes, []);
    app.button().props.onClick();
    assert.deepEqual(app.changes, [true], "debug changes before the animation finishes");
    await app.flush();
    assert.equal(app.button().props["aria-pressed"], true);
    assert.equal(app.button().props["data-firing"], true);
    const keys = app.keys();
    await app.click();
    assert.equal(app.button().props["aria-pressed"], false);
    assert.equal(app.button().props["data-firing"], true, "disabling leaves the current burst running");
    assert.deepEqual(app.keys(), keys, "disabling must not replace keyed animated nodes");
    await app.click();
    assert.deepEqual(app.changes, [true, false, true]);
    assert.equal(app.button().props["data-firing"], true);
    assert.deepEqual(app.keys(), keys, "enabling during the burst must not restart it by replacing nodes");
  } finally { app.dispose(); }
});

test("only the terminal impact completion releases the burst for another activation", async () => {
  const app = setup();
  try {
    await app.click();
    const impact = app.impact();
    assert.ok(impact?.props.onAnimationEnd);
    impact.props.onAnimationEnd({ target: {}, currentTarget: {} }); await app.flush();
    assert.equal(app.button().props["data-firing"], true, "a bubbling child animation cannot end the whole burst");
    const terminal = {};
    impact.props.onAnimationEnd({ target: terminal, currentTarget: terminal }); await app.flush();
    assert.notEqual(app.button().props["data-firing"], true);
    assert.equal(app.button().props["aria-pressed"], true, "animation completion does not toggle debug");
    await app.click();
    assert.notEqual(app.button().props["data-firing"], true, "disabling after completion does not launch a burst");
    await app.click();
    assert.equal(app.button().props["data-firing"], true, "a later enabling can launch one new burst");
  } finally { app.dispose(); }
});

test("opening View with debug already enabled does not replay its activation animation", async () => {
  const app = setup({ enabled: true });
  try {
    await app.flush();
    assert.equal(app.button().props["aria-pressed"], true);
    assert.notEqual(app.button().props["data-firing"], true);
    assert.deepEqual(app.changes, []);
  } finally { app.dispose(); }
});

test("reduced motion suppresses and cancels bursts while debug remains usable", async () => {
  const app = setup({ reducedMotion: true });
  try {
    await app.click();
    assert.equal(app.button().props["aria-pressed"], true);
    assert.notEqual(app.button().props["data-firing"], true);
    await app.motion(false);
    assert.notEqual(app.button().props["data-firing"], true, "changing the preference does not replay an enabled button");
    await app.click(); await app.click();
    assert.equal(app.button().props["data-firing"], true);
    await app.motion(true);
    assert.notEqual(app.button().props["data-firing"], true, "enabling reduced motion stops the active burst");
    assert.equal(app.button().props["aria-pressed"], true);
    assert.equal(app.listeners.size, 1);
  } finally { app.dispose(); }
  assert.equal(app.listeners.size, 0, "closing View removes the motion preference listener");
});
