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
const textContent = (node) => node == null ? "" : Array.isArray(node) ? node.map(textContent).join("")
  : typeof node === "object" ? textContent(node.props?.children) : String(node);
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
    if (name === "./DebugCinemaArt") return { CinemaFrame: "CinemaFrame", CinemaSlate: "CinemaSlate" };
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
    impact: (direction) => nodes(output).find((node) => node.props?.["data-debug-impact"] === direction),
    status: () => nodes(output).find((node) => node.props?.["data-debug-state"]),
    hint: () => nodes(output).find((node) => node.props?.id === nodes(output).find((item) => item.type === "button")?.props["aria-describedby"]),
    keys: () => nodes(output).filter((node) => node.key != null).map((node) => node.key),
    async click() { this.button().props.onClick(); await flush(); },
    async end(direction, { bubbles = false } = {}) {
      const terminal = {};
      this.impact(direction).props.onAnimationEnd({ target: bubbles ? {} : terminal, currentTarget: terminal });
      await flush();
    },
    async motion(matches) { media.matches = matches; [...listeners].forEach((listener) => listener({ matches })); await flush(); },
    dispose() { disposed = true; hooks.forEach((hook) => hook?.cleanup?.()); },
  };
}

test("each debug toggle updates the setting immediately and animates its own direction", async () => {
  const app = setup();
  try {
    app.button().props.onMouseEnter?.(); app.button().props.onPointerEnter?.(); await app.flush();
    assert.ok(!app.button().props["data-motion"], "hover does not launch a burst");
    assert.deepEqual(app.changes, []);
    assert.equal(textContent(app.status()), "OFF");
    assert.equal(String(app.status().props["aria-hidden"]), "true", "aria-pressed provides the accessible state");
    const descriptionId = app.button().props["aria-describedby"];
    assert.ok(descriptionId);
    assert.equal(textContent(app.hint()), "Match scores & descriptions");
    app.button().props.onClick();
    assert.deepEqual(app.changes, [true], "debug changes before the animation finishes");
    await app.flush();
    assert.equal(app.button().props["aria-pressed"], true);
    assert.equal(app.button().props["data-motion"], "on");
    assert.equal(textContent(app.status()), "ON");
    await app.end("on");
    assert.ok(!app.button().props["data-motion"]);
    app.button().props.onClick();
    assert.deepEqual(app.changes, [true, false], "disabling also updates before its animation finishes");
    await app.flush();
    assert.equal(app.button().props["aria-pressed"], false);
    assert.equal(app.button().props["data-motion"], "off");
    assert.equal(textContent(app.status()), "OFF");
    assert.equal(app.button().props["aria-describedby"], descriptionId, "the description stays connected in either state");
    await app.end("off");
    assert.ok(!app.button().props["data-motion"]);
    assert.equal(app.button().props["aria-pressed"], false, "animation completion never changes the setting");
  } finally { app.dispose(); }
});

test("rapid toggles retarget the animation to the latest setting without replacing keyed nodes", async () => {
  const app = setup();
  try {
    const keys = app.keys();
    for (const direction of ["on", "off"]) assert.ok(app.impact(direction), `${direction} completion group stays mounted`);
    await app.click();
    assert.equal(app.button().props["data-motion"], "on");
    await app.click();
    assert.equal(app.button().props["data-motion"], "off", "disabling immediately replaces activation with shutdown");
    assert.deepEqual(app.keys(), keys);
    await app.click();
    assert.equal(app.button().props["data-motion"], "on", "the newest activation wins over shutdown");
    assert.equal(app.button().props["aria-pressed"], true);
    assert.deepEqual(app.keys(), keys);
    for (const direction of ["on", "off"]) assert.ok(app.impact(direction), `${direction} completion group stays mounted`);
    await app.end("on");
    await app.flush();
    assert.ok(!app.button().props["data-motion"], "no older animation is queued after the latest one completes");
    assert.deepEqual(app.changes, [true, false, true]);
  } finally { app.dispose(); }
});

for (const enabled of [false, true]) {
  const direction = enabled ? "on" : "off";
  const previousDirection = enabled ? "off" : "on";
  test(`only the current ${direction} terminal completion can settle a retargeted animation`, async () => {
    const app = setup({ enabled });
    try {
      await app.click();
      const oldImpact = app.impact(previousDirection);
      await app.click();
      assert.equal(app.button().props["data-motion"], direction);
      await app.end(direction, { bubbles: true });
      assert.equal(app.button().props["data-motion"], direction, "a bubbling child cannot end the sequence");
      const oldTerminal = {};
      oldImpact.props.onAnimationEnd({ target: oldTerminal, currentTarget: oldTerminal });
      await app.flush();
      assert.equal(app.button().props["data-motion"], direction, "a stale opposite completion cannot settle the current direction");
      await app.end(direction);
      assert.ok(!app.button().props["data-motion"]);
      assert.equal(app.button().props["aria-pressed"], enabled);
      assert.deepEqual(app.changes, [!enabled, enabled]);
    } finally { app.dispose(); }
  });
}

test("opening or reopening View preserves either setting without playing an animation", async () => {
  for (const enabled of [false, true]) {
    for (let opening = 0; opening < 2; opening++) {
      const app = setup({ enabled });
      try {
        await app.flush();
        assert.equal(app.button().props["aria-pressed"], enabled);
        assert.equal(textContent(app.status()), enabled ? "ON" : "OFF");
        assert.ok(!app.button().props["data-motion"]);
        assert.deepEqual(app.changes, []);
      } finally { app.dispose(); }
    }
  }
});

test("reduced motion suppresses both directions while debug and its status remain usable", async () => {
  const app = setup({ reducedMotion: true });
  try {
    await app.click();
    assert.equal(app.button().props["aria-pressed"], true);
    assert.equal(textContent(app.status()), "ON");
    assert.ok(!app.button().props["data-motion"]);
    await app.click();
    assert.equal(app.button().props["aria-pressed"], false);
    assert.equal(textContent(app.status()), "OFF");
    assert.ok(!app.button().props["data-motion"]);
    assert.deepEqual(app.changes, [true, false]);
    await app.motion(false);
    assert.ok(!app.button().props["data-motion"], "removing the preference does not replay the current state");
    await app.click();
    assert.equal(app.button().props["data-motion"], "on", "later clicks may animate again");
  } finally { app.dispose(); }
});

test("enabling reduced motion cancels either direction and closing View cleans up its listener", async () => {
  for (const enabled of [false, true]) {
    const app = setup({ enabled });
    try {
      await app.click();
      assert.equal(app.button().props["data-motion"], enabled ? "off" : "on");
      await app.motion(true);
      assert.ok(!app.button().props["data-motion"], "changing preference settles the active animation");
      assert.equal(app.button().props["aria-pressed"], !enabled);
      await app.motion(false);
      assert.ok(!app.button().props["data-motion"], "restoring motion never replays a canceled sequence");
      assert.equal(app.listeners.size, 1);
    } finally { app.dispose(); }
    assert.equal(app.listeners.size, 0, "closing View removes the motion preference listener");
  }
});
