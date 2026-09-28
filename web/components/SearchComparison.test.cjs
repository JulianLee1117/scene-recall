const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
const compiled = ts.transpileModule(fs.readFileSync(path.join(__dirname, "SearchComparison.tsx"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const report = (status = "completed") => ({
  variants: Object.fromEntries(["normal", "jev", "fixed"].map((name) => [name, { results: [{ unit_id: name }] }])),
  provider: { status },
});

function harness() {
  const hooks = [], requests = [], updates = [];
  const timers = new Map();
  let timerId = 0;
  let cursor = 0, tree, effects = [], scheduled = false, disposed = false;
  const props = { query: "two people almost kissing", filmIds: ["before-sunrise"], onResultsChange: (rows) => updates.push(rows) };
  const schedule = () => { if (!scheduled && !disposed) { scheduled = true; queueMicrotask(render); } };
  const react = {
    useState(initial) {
      const index = cursor++;
      hooks[index] ??= { value: initial };
      return [hooks[index].value, (next) => {
        if (!Object.is(next, hooks[index].value)) { hooks[index].value = next; schedule(); }
      }];
    },
    useRef(initial) { return hooks[cursor++] ??= { current: initial }; },
    useEffect(effect, deps) {
      const index = cursor++;
      if (!hooks[index] || !same(deps, hooks[index].deps)) {
        hooks[index] = { deps, cleanup: hooks[index]?.cleanup };
        effects.push(() => { hooks[index].cleanup?.(); hooks[index].cleanup = effect(); });
      }
    },
  };
  const exports = {};
  vm.runInNewContext(compiled, {
    exports, process: { env: {} }, AbortController,
    setTimeout(callback, milliseconds) { const id = ++timerId; timers.set(id, { callback, milliseconds }); return id; },
    clearTimeout(id) { timers.delete(id); },
    fetch(url, init) { return new Promise((resolve, reject) => requests.push({ url, init, resolve, reject })); },
    require(name) {
      if (name === "react") return react;
      if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }), Fragment: "fragment" };
      if (name.endsWith(".css")) return { default: new Proxy({}, { get: (_, key) => key }) };
      throw new Error(`Unexpected module ${name}`);
    },
  });
  function render() {
    if (disposed) return;
    scheduled = false; cursor = 0; tree = exports.default(props);
    const pending = effects; effects = []; pending.forEach((effect) => effect());
  }
  const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
  const find = (predicate) => nodes(tree).find(predicate);
  render();
  return {
    requests, updates, flush, find, timers,
    async expire() { for (const [id, timer] of [...timers]) { timers.delete(id); timer.callback(); } await flush(); },
    get text() { return text(tree); },
    button: (label) => find((node) => node.type === "button" && (text(node) === label || node.props["aria-label"] === label)),
    async update(next) { Object.assign(props, next); render(); await flush(); },
    async resolve(index, body, ok = true, status = ok ? 200 : 500) { requests[index].resolve({ ok, status, json: async () => body }); await flush(); },
    dispose() { disposed = true; hooks.forEach((hook) => hook?.cleanup?.()); },
  };
}

test("comparison is explicit, scopes the unchanged query, and prevents duplicate in-flight provider calls", async () => {
  const app = harness();
  try {
    assert.equal(app.requests.length, 0, "mount never makes a paid request");
    await app.update({ filmIds: ["before-sunrise"] });
    assert.equal(app.requests.length, 0, "equivalent scope props do not start a request");
    const start = app.button("Compare Jev");
    start.props.onClick(); start.props.onClick(); await app.flush();
    assert.equal(app.requests.length, 1);
    assert.equal(app.requests[0].url, "/search/intent/compare-hosted");
    assert.deepEqual(JSON.parse(app.requests[0].init.body), { q: "two people almost kissing", film_ids: ["before-sunrise"], limit: 200 });
    assert.equal(app.button("Comparing…").props.disabled, true);
    assert.equal(app.updates.length, 0, "ordinary results remain visible during comparison");
  } finally { app.dispose(); }
});

test("all three variants switch cached footage without requests and close restores the original stream", async () => {
  const app = harness();
  try {
    app.button("Compare Jev").props.onClick(); await app.flush();
    const data = report(); await app.resolve(0, data);
    assert.equal(app.button("Jev guided").props["aria-pressed"], true);
    assert.equal(app.updates.at(-1), data.variants.jev.results);
    for (const [label, variant] of [["Ordinary", "normal"], ["Fixed expansion", "fixed"], ["Jev guided", "jev"]]) {
      app.button(label).props.onClick(); await app.flush();
      assert.equal(app.updates.at(-1), data.variants[variant].results);
      assert.equal(app.button(label).props["aria-pressed"], true);
    }
    assert.equal(app.requests.length, 1);
    app.button("Close comparison").props.onClick(); await app.flush();
    assert.equal(app.updates.at(-1), null);
    assert.ok(app.button("Compare Jev"));
    assert.equal(app.requests.length, 1);
  } finally { app.dispose(); }
});

test("cached Jev decisions select guided results, while provider failure visibly selects ordinary", async () => {
  for (const [status, selected] of [["cached", "Jev guided"], ["failed", "Ordinary"], ["budget_exhausted", "Ordinary"]]) {
    const app = harness();
    try {
      app.button("Compare Jev").props.onClick(); await app.flush();
      const data = report(status); await app.resolve(0, data);
      assert.equal(app.button(selected).props["aria-pressed"], true);
      assert.equal(app.updates.at(-1), selected === "Ordinary" ? data.variants.normal.results : data.variants.jev.results);
      if (selected === "Ordinary") assert.match(app.text, /Jev unavailable/);
    } finally { app.dispose(); }
  }
});

test("HTTP or malformed responses leave ordinary results intact and allow an explicit retry", async () => {
  for (const [body, ok] of [[{}, false], [{ variants: { normal: { results: [] } } }, true]]) {
    const app = harness();
    try {
      app.button("Compare Jev").props.onClick(); await app.flush();
      await app.resolve(0, body, ok);
      assert.equal(app.updates.length, 0);
      assert.match(app.text, /ordinary results are still here/);
      assert.equal(app.button("Compare Jev").props.disabled, false);
      app.button("Compare Jev").props.onClick(); await app.flush();
      assert.equal(app.requests.length, 2);
      await app.resolve(1, report());
      assert.equal(app.updates.at(-1)[0].unit_id, "jev");
      assert.doesNotMatch(app.text, /unavailable/);
    } finally { app.dispose(); }
  }
});

test("query and film-scope changes invalidate comparisons and cannot receive a late response", async () => {
  for (const next of [{ query: "face in blue light" }, { filmIds: ["moonlight"] }]) {
    const app = harness();
    try {
      app.button("Compare Jev").props.onClick(); await app.flush();
      await app.update(next);
      assert.equal(app.requests[0].init.signal.aborted, true);
      assert.equal(app.updates.at(-1), null);
      await app.resolve(0, report());
      assert.equal(app.updates.at(-1), null, "late footage cannot replace the changed query");
      assert.ok(app.button("Compare Jev"));
      assert.equal(app.requests.length, 1, "changing input never starts another paid call");
      app.button("Compare Jev").props.onClick(); await app.flush();
      await app.resolve(1, report());
      await app.update({ query: "another new query" });
      assert.ok(app.button("Compare Jev"));
      assert.equal(app.updates.at(-1), null, "completed cached comparisons also reset");
    } finally { app.dispose(); }
  }
});

test("leaving the comparison aborts pending work and restores ordinary results for navigation", async () => {
  for (const completed of [false, true]) {
    const app = harness();
    app.button("Compare Jev").props.onClick(); await app.flush();
    if (completed) await app.resolve(0, report());
    app.dispose();
    assert.equal(app.updates.at(-1), null, "returning to Search cannot show a guided list without its controls");
    if (!completed) {
      assert.equal(app.requests[0].init.signal.aborted, true);
      await app.resolve(0, report());
      assert.equal(app.updates.at(-1), null);
    }
  }
});

test("deadline recovers a stalled fetch or response body even when abort never rejects", async () => {
  for (const stalledBody of [false, true]) {
    const app = harness();
    try {
      app.button("Compare Jev").props.onClick(); await app.flush();
      let finishBody;
      if (stalledBody) {
        app.requests[0].resolve({ ok: true, json: () => new Promise((resolve) => { finishBody = resolve; }) });
        await app.flush();
      }
      assert.equal([...app.timers.values()][0].milliseconds, 50_000);
      await app.expire();
      assert.equal(app.requests[0].init.signal.aborted, true);
      assert.equal(app.button("Compare Jev").props.disabled, false);
      assert.match(app.text, /took too long/);
      assert.equal(app.updates.length, 0);
      app.button("Compare Jev").props.onClick(); await app.flush();
      if (stalledBody) finishBody(report()); else await app.resolve(0, report());
      await app.flush();
      assert.ok(app.button("Comparing…"), "late completion must not reset the new request");
      assert.equal(app.updates.length, 0);
      await app.resolve(1, report());
      assert.equal(app.updates.at(-1)[0].unit_id, "jev");
      assert.equal(app.timers.size, 0);
    } finally { app.dispose(); }
  }
});

test("Cancel immediately restores controls, clears the deadline, and ignores late results", async () => {
  const app = harness();
  try {
    app.button("Compare Jev").props.onClick(); await app.flush();
    app.button("Cancel").props.onClick(); await app.flush();
    assert.equal(app.requests[0].init.signal.aborted, true);
    assert.equal(app.button("Compare Jev").props.disabled, false);
    assert.equal(app.timers.size, 0);
    await app.resolve(0, report());
    assert.equal(app.updates.length, 0);
    assert.doesNotMatch(app.text, /unavailable|took too long/);
  } finally { app.dispose(); }
});

test("server deadline and busy responses explain recovery without automatically retrying", async () => {
  for (const [status, message] of [[504, /took too long/], [429, /still finishing/]]) {
    const app = harness();
    try {
      app.button("Compare Jev").props.onClick(); await app.flush();
      await app.resolve(0, {}, false, status);
      assert.match(app.text, message);
      assert.equal(app.requests.length, 1);
      assert.equal(app.updates.length, 0);
      assert.equal(app.timers.size, 0);
      assert.equal(app.button("Compare Jev").props.disabled, false);
    } finally { app.dispose(); }
  }
});

test("unmount and query changes clear old deadlines", async () => {
  const app = harness();
  app.button("Compare Jev").props.onClick(); await app.flush();
  await app.update({ query: "another scene" });
  assert.equal(app.timers.size, 0);
  app.button("Compare Jev").props.onClick(); await app.flush();
  app.dispose();
  assert.equal(app.timers.size, 0);
});
