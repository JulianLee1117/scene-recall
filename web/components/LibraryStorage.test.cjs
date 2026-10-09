const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const compiled = ts.transpileModule(fs.readFileSync(path.join(__dirname, "LibraryStorage.tsx"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const snapshot = (total = 820e9) => ({
  measured_at: "2026-09-15T12:00:00Z", total_bytes: total, file_count: 300, incomplete: false,
  categories: [
    { id: "source_films", label: "Source films", bytes: 780e9, file_count: 200, paths: ["V:/films"], incomplete: false },
    { id: "derived_assets", label: "Derived assets", bytes: 24e9, file_count: 90, paths: ["C:/assets"], incomplete: false },
    { id: "incoming", label: "Downloads", bytes: 12e9, file_count: 8, paths: ["V:/incoming"], incomplete: false },
    { id: "managed_archive", label: "Release archives", bytes: 4e9, file_count: 2, paths: ["V:/evidence"], incomplete: false },
  ],
  volumes: [
    { id: "C", label: "C:\\", bytes: 24e9, file_count: 90, free_bytes: 100e9, total_capacity_bytes: 1e12, incomplete: false },
    { id: "V", label: "V:\\", bytes: 796e9, file_count: 210, free_bytes: null, total_capacity_bytes: null, incomplete: false },
  ],
  issues: [], excluded: ["unrelated model cache entries"],
});
function setup() {
  const hooks = [], requests = [], timers = new Map(), props = { refreshKey: 0 };
  let cursor = 0, nextTimer = 0, scheduled = false, disposed = false, tree, effects = [];
  const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
  const schedule = () => { if (!scheduled && !disposed) { scheduled = true; queueMicrotask(render); } };
  const react = {
    useState(initial) {
      const i = cursor++;
      hooks[i] ??= { value: typeof initial === "function" ? initial() : initial };
      return [hooks[i].value, (update) => {
        const next = typeof update === "function" ? update(hooks[i].value) : update;
        if (!Object.is(next, hooks[i].value)) { hooks[i].value = next; schedule(); }
      }];
    },
    useRef(initial) { return hooks[cursor++] ??= { current: initial }; },
    useEffect(effect, deps) {
      const i = cursor++;
      if (!hooks[i] || !same(deps, hooks[i].deps)) {
        const previous = hooks[i]; hooks[i] = { deps, cleanup: previous?.cleanup };
        effects.push(() => { hooks[i].cleanup?.(); hooks[i].cleanup = effect(); });
      }
    },
  };
  const exported = {};
  vm.runInNewContext(compiled, {
    exports: exported, AbortController, Error, process: { env: {} },
    window: { setTimeout(fn) { timers.set(++nextTimer, fn); return nextTimer; }, clearTimeout(id) { timers.delete(id); } },
    fetch(url, init) {
      let resolve, reject;
      const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
      requests.push({ url, init, resolve, reject });
      return promise;
    },
    require(name) {
      if (name === "react") return react;
      if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }), Fragment: "fragment" };
      if (name.endsWith(".module.css")) return { default: new Proxy({}, { get: (_, key) => key }) };
      throw new Error(`Unexpected import ${name}`);
    },
  });
  function render() {
    if (disposed) return;
    scheduled = false; cursor = 0;
    tree = exported.default(props);
    const pending = effects; effects = []; pending.forEach((effect) => effect());
  }
  render();
  return {
    requests, timers, props, render,
    get text() { return text(tree); },
    find: (predicate) => nodes(tree).find(predicate),
    refresh: () => nodes(tree).find((node) => node.type === "button"),
    resolve(index, status, value, error = null) { requests[index].resolve({ ok: true, json: async () => ({ status, snapshot: value, started_at: null, error }) }); },
    async flush() { for (let i = 0; i < 30; i++) await Promise.resolve(); },
    tick() { const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach((fn) => fn()); },
    dispose() { disposed = true; hooks.forEach((hook) => hook.cleanup?.()); },
  };
}

test("initial scanning never fabricates a size, polls cached status, and stops on a complete snapshot", async () => {
  const app = setup();
  try {
    assert.match(app.text, /Measuring storage/);
    assert.doesNotMatch(app.text, /0 B/);
    assert.equal(app.refresh().props.disabled, true);
    assert.equal(app.requests[0].url, "/library/storage");
    app.resolve(0, "scanning", null); await app.flush();
    assert.equal(app.timers.size, 1);
    app.tick(); await app.flush();
    assert.equal(app.requests[1].url, "/library/storage", "polling does not restart a scan");
    app.resolve(1, "ready", snapshot()); await app.flush();
    assert.match(app.text, /820 GB storage/);
    assert.match(app.text, /C: 24 GB/);
    assert.match(app.text, /V: 796 GB/);
    assert.match(app.text, /Films 780 GB/);
    assert.match(app.text, /Supporting 40 GB/);
    assert.equal(app.timers.size, 0);
    assert.equal(app.refresh().props.disabled, false);
    const breakdown = app.find((node) => node.props?.title?.includes("Downloads:"));
    assert.match(breakdown.props.title, /Downloads: 12 GB/);
    assert.match(breakdown.props.title, /Release archives: 4 GB/);
    const measurement = app.find((node) => node.props?.title?.includes("Combined file sizes"));
    assert.match(measurement.props.title, /Combined file sizes; shared files counted once/);
    assert.match(measurement.props.title, /Excludes: unrelated model cache entries/);
    assert.match(app.find((node) => node.props?.title?.includes("V: 796")).props.title, /Free space unavailable/);
  } finally { app.dispose(); }
});

test("an initial request failure stays local, exposes retry, and never starts a polling loop", async () => {
  const app = setup();
  try {
    app.requests[0].resolve({ ok: false, status: 503 }); await app.flush();
    assert.match(app.text, /Storage unavailable/);
    assert.equal(text(app.find((node) => node.type === "summary")), "Storage unavailable");
    assert.doesNotMatch(app.text, /0 B/);
    assert.equal(app.timers.size, 0);
    assert.equal(app.refresh().props.disabled, false);
    app.refresh().props.onClick(); await app.flush();
    assert.equal(app.requests[1].url, "/library/storage?refresh=true");
    app.resolve(1, "ready", snapshot()); await app.flush();
    assert.match(app.text, /820 GB storage/);
  } finally { app.dispose(); }
});

test("storage starts collapsed with only its total, while measurement details and refresh stay inside", async () => {
  const app = setup();
  try {
    app.resolve(0, "ready", snapshot()); await app.flush();
    const disclosure = app.find((node) => node.type === "details");
    const summary = app.find((node) => node.type === "summary");
    assert.ok(disclosure);
    assert.equal(disclosure.props.open, undefined);
    assert.equal(text(summary), "820 GB storage");
    assert.equal(nodes(summary).some((node) => node.type === "button"), false);
    assert.equal(app.refresh().props["aria-label"], "Refresh storage measurement");
    assert.match(app.text, /100 GB free · 1 TB capacity/);
    assert.doesNotMatch(app.text, /Shared files counted once|Excludes:/i);
    assert.match(app.text, /Measured .* · 300 files/);
    assert.equal(nodes(summary).some((node) => node.props?.title?.includes("Shared files") || node.props?.["aria-label"]?.includes("Shared files")), false);

    app.refresh().props.onClick(); await app.flush();
    app.resolve(1, "error", null, "Scan failed"); await app.flush();
    assert.match(text(app.find((node) => node.type === "summary")), /820 GB storageUpdate unavailable/);
    assert.match(app.text, /Scan failed/);
  } finally { app.dispose(); }
});

test("refresh preserves the last measurement, and newer refresh keys cancel stale requests", async () => {
  const app = setup();
  try {
    app.resolve(0, "ready", snapshot()); await app.flush();
    app.refresh().props.onClick(); await app.flush();
    assert.match(app.text, /820 GB storage/);
    assert.match(app.text, /Updating/);
    assert.equal(app.refresh().props.disabled, true);
    app.props.refreshKey += 1; app.render(); await app.flush();
    assert.equal(app.requests[1].init.signal.aborted, true);
    assert.equal(app.requests[2].url, "/library/storage?refresh=true");
    app.resolve(2, "ready", snapshot(850e9)); await app.flush();
    app.resolve(1, "ready", snapshot(1e9)); await app.flush();
    assert.match(app.text, /850 GB storage/);
    assert.doesNotMatch(app.text, /1 GB storage/);
    assert.equal(app.timers.size, 0);
    app.props.refreshKey += 1; app.render(); await app.flush();
    app.resolve(3, "error", null, "Scan failed"); await app.flush();
    assert.match(app.text, /850 GB storage/);
    assert.match(app.text, /Update unavailable/);
    assert.equal(app.refresh().props.disabled, false);
  } finally { app.dispose(); }
});

test("incomplete measurements disclose lower bounds and unmount cancels pending scan work", async () => {
  const app = setup();
  const partial = snapshot();
  partial.incomplete = true;
  partial.issues = [{ path: "V:/incoming", reason: "Access denied" }];
  partial.categories[2].incomplete = true;
  partial.volumes[1].incomplete = true;
  app.resolve(0, "ready", partial); await app.flush();
  assert.match(app.text, /At least 820 GB storage/);
  assert.match(app.text, /Some locations unavailable/);
  assert.match(text(app.find((node) => node.type === "summary")), /At least 820 GB storageSome locations unavailable/);
  assert.match(app.find((node) => node.props?.title?.includes("Combined file sizes")).props["aria-label"], /V:\/incoming: Access denied/);
  assert.match(app.text, /Supporting ≥ 40 GB/);
  app.refresh().props.onClick(); await app.flush();
  app.resolve(1, "scanning", partial); await app.flush();
  assert.equal(app.timers.size, 1);
  app.dispose();
  assert.equal(app.requests[1].init.signal.aborted, true);
  assert.equal(app.timers.size, 0);
  app.tick();
  assert.equal(app.requests.length, 2);
});
