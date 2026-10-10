const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
const compile = (file, jsx) => ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: jsx ? ts.JsxEmit.ReactJSX : undefined },
}).outputText;
const reasons = {};
vm.runInNewContext(compile("../lib/matchReasons.ts"), { exports: reasons, require: () => ({ formatTime: String }) });
const compiled = compile("ShotDialogue.tsx", true);
const shot = (id = "a") => ({ unit_id: `${id}_1`, film_id: id, t_start: 20, t_end: 40, caption: "A room", matches: [] });
const line = (time, content = `Line at ${time}`) => ({ line_id: `l-${time}`, t_start: time, t_end: time + 1, text: content, source: "subtitle" });
const response = (lines, id = "a") => ({ unit_id: `${id}_1`, film_id: id, status: "available", lines, truncated: false });
const quote = (text = "Come with me.", start = 24, end = 26) => ({ clause_id: "main", facet: "all", rank: 1,
  evidence: { type: "text", view: "dialogue", text, source: "quote", score: .95, t_start: start, t_end: end } });

function harness(initial = shot()) {
  const hooks = [], requests = [], seeks = [];
  let cursor = 0, output, scheduled = false, disposed = false, effects = [];
  const props = { shot: initial, canSeek: true, onSeek: (time) => seeks.push(time) };
  const schedule = () => { if (!scheduled && !disposed) { scheduled = true; queueMicrotask(render); } };
  const react = {
    useId() { cursor++; return "dialogue-list"; },
    useState(initial) {
      const i = cursor++; hooks[i] ??= { value: typeof initial === "function" ? initial() : initial };
      return [hooks[i].value, (update) => {
        const value = typeof update === "function" ? update(hooks[i].value) : update;
        if (!Object.is(value, hooks[i].value)) { hooks[i].value = value; schedule(); }
      }];
    },
    useEffect(effect, deps) {
      const i = cursor++;
      if (!hooks[i] || !same(deps, hooks[i].deps)) {
        const old = hooks[i]; hooks[i] = { deps, cleanup: old?.cleanup };
        effects.push(() => { hooks[i].cleanup?.(); hooks[i].cleanup = effect(); });
      }
    },
  };
  const exports = {};
  vm.runInNewContext(compiled, {
    exports, AbortController, process: { env: { NEXT_PUBLIC_API_URL: "http://api.invalid" } },
    fetch(url, init) { return new Promise((resolve, reject) => requests.push({ url, init, resolve, reject })); },
    require(name) {
      if (name === "react") return react;
      if (name === "react/jsx-runtime") return { Fragment: "fragment", jsx: (type, props, key) => ({ type, props, key }), jsxs: (type, props, key) => ({ type, props, key }) };
      if (name === "@/lib/format") return { formatTime: (time) => `${time}s` };
      if (name === "@/lib/matchReasons") return reasons;
      throw new Error(`Unexpected import: ${name}`);
    },
  });
  function render() {
    if (disposed) return;
    scheduled = false; cursor = 0; output = exports.default(props);
    const run = effects; effects = []; run.forEach((effect) => effect());
  }
  const flush = async () => { for (let i = 0; i < 25; i++) await Promise.resolve(); };
  render();
  return {
    requests, seeks, flush,
    get state() { return output; },
    find: (predicate) => nodes(output).find(predicate),
    button: (label) => nodes(output).find((node) => node.type === "button" && text(node) === label),
    lines: () => nodes(output).filter((node) => node.type === "li"),
    async resolve(index, data, ok = true) { requests[index].resolve({ ok, json: async () => data }); await flush(); },
    async update(next) { Object.assign(props, next); render(); await flush(); },
    dispose() { disposed = true; hooks.forEach((hook) => hook?.cleanup?.()); },
  };
}

test("ordinary results load bounded source dialogue, expand and seek to actual cue times", async () => {
  const app = harness();
  try {
    assert.equal(text(app.state), "");
    assert.equal(app.requests[0].url, "http://api.invalid/library/shot/a_1/dialogue");
    await app.resolve(0, response([line(21), line(23), line(25), line(27), line(29)]));
    assert.equal(app.lines().length, 3);
    assert.equal(app.find((node) => node.type === "mark"), undefined);
    app.button("23s").props.onClick();
    assert.deepEqual(app.seeks, [23]);
    app.button("Show all dialogue").props.onClick(); await app.flush();
    assert.equal(app.lines().length, 5);
    assert.equal(app.button("Show less").props["aria-expanded"], true);
    assert.equal(app.requests.length, 1, "expansion does not refetch or reindex");
    await app.update({ canSeek: false });
    assert.equal(app.button("23s").props.disabled, true);
  } finally { app.dispose(); }
});

test("matched passage is immediately visible and replaces overlapping cues without duplicates", async () => {
  const app = harness({ ...shot(), matches: [quote("Come with me. Now.", 24, 28)] });
  try {
    assert.match(text(app.state), /Come with me. Now./);
    app.button("24s").props.onClick();
    assert.deepEqual(app.seeks, [24]);
    await app.resolve(0, response([line(21, "Before"), line(24, "Come with me."), line(26, "Now."), line(29, "After"), line(32, "Later")]));
    assert.deepEqual(app.lines().map(text), ["21sBefore", "24sCome with me. Now.", "29sAfter"]);
    assert.equal(text(app.find((node) => node.type === "mark")), "Come with me. Now.");
  } finally { app.dispose(); }
});

test("changing shots aborts stale requests, resets expansion and never shows another shot's dialogue", async () => {
  const app = harness();
  try {
    await app.resolve(0, response([line(21), line(23), line(25), line(27)]));
    app.button("Show all dialogue").props.onClick(); await app.flush();
    await app.update({ shot: shot("b") });
    assert.equal(app.requests[0].init.signal.aborted, true);
    assert.equal(text(app.state), "");
    await app.update({ shot: shot("c") });
    await app.resolve(2, response([line(21, "Correct film")], "c"));
    await app.resolve(1, response([line(21, "Stale film")], "b"));
    assert.match(text(app.state), /Correct film/);
    assert.doesNotMatch(text(app.state), /Stale film/);
    assert.equal(app.requests.length, 3);
  } finally { app.dispose(); }
});

test("missing dialogue is omitted without claiming silence; network failures can retry", async () => {
  const app = harness();
  try {
    await app.resolve(0, { ...response([]), status: "unavailable" });
    assert.equal(text(app.state), "");
    await app.update({ shot: shot("b") });
    await app.resolve(1, {}, false);
    assert.match(text(app.state), /Dialogue could not be loaded/);
    app.button("Retry").props.onClick(); await app.flush();
    await app.resolve(2, response([line(22, "Recovered")], "b"));
    assert.match(text(app.state), /Recovered/);
    assert.doesNotMatch(text(app.state), /could not/);
  } finally { app.dispose(); }
});

test("on-screen evidence stays separate from dialogue and untimed semantic text never invents a seek time", async () => {
  const app = harness({ ...shot(), matches: [{ clause_id: "main", facet: "all", rank: 1,
    evidence: { type: "text", view: "ocr", text: "EXIT", source: "semantic" } }] });
  try {
    assert.match(text(app.state), /On screen“EXIT”/);
    await app.resolve(0, response([line(22, "Over here.")]));
    assert.match(text(app.state), /Dialogue22sOver here./);
    assert.equal(app.find((node) => node.type === "mark"), undefined);
    await app.update({ shot: { ...shot("b"), matches: [{ clause_id: "main", facet: "all", rank: 1,
      evidence: { type: "text", view: "dialogue", text: "A remembered line", source: "semantic" } }] } });
    assert.equal(app.lines().length, 1);
    assert.equal(nodes(app.lines()[0]).find((node) => node.type === "button"), undefined);
  } finally { app.dispose(); }
});

test("partial words never replace the actual semantic passage, and clipped text always has an expansion", async () => {
  const app = harness({ ...shot(), matches: [{ clause_id: "main", facet: "all", rank: 1,
    evidence: { type: "text", view: "dialogue", text: "I know.", source: "semantic" } }] });
  try {
    await app.resolve(0, response([line(22, "No.")]));
    assert.equal(text(app.find((node) => node.type === "mark")), "I know.");
    assert.equal(app.lines().length, 2);
    await app.update({ shot: shot("b") });
    await app.resolve(1, response([line(22, "One\nTwo\nThree\nFour")], "b"));
    assert.equal(app.find((node) => node.props?.className === "modal-dialogue-text is-long"), undefined);
    await app.update({ shot: shot("c") });
    await app.resolve(2, response([line(22, "A long source passage. ".repeat(15))], "c"));
    assert.ok(app.find((node) => node.props?.className === "modal-dialogue-text is-long"));
    assert.ok(app.button("Show all dialogue"));
  } finally { app.dispose(); }
});
