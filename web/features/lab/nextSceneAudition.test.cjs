const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const compile = (file) => ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const helpers = {};
vm.runInNewContext(compile("nextScene.ts"), { exports: helpers });
const clone = (value) => JSON.parse(JSON.stringify(value));
const equalDeps = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));

async function setup() {
  const hooks = [];
  let cursor = 0, scheduled = false, effects = [], tree, disposed = false;
  const schedule = () => { if (!scheduled && !disposed) { scheduled = true; queueMicrotask(render); } };
  const react = {
    useState(initial) {
      const index = cursor++;
      hooks[index] ??= { value: typeof initial === "function" ? initial() : initial };
      return [hooks[index].value, (update) => {
        const value = typeof update === "function" ? update(hooks[index].value) : update;
        if (!Object.is(value, hooks[index].value)) { hooks[index].value = value; schedule(); }
      }];
    },
    useRef(initial) { return hooks[cursor++] ??= { current: initial }; },
    useEffect(effect, deps) {
      const index = cursor++;
      if (!hooks[index] || !equalDeps(deps, hooks[index].deps)) {
        const previous = hooks[index];
        hooks[index] = { deps, cleanup: previous?.cleanup };
        effects.push(() => { hooks[index].cleanup?.(); hooks[index].cleanup = effect(); });
      }
    },
  };
  const exports = {};
  vm.runInNewContext(compile("NextSceneAudition.tsx"), { exports, require(name) {
    if (name === "react") return react;
    if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
    if (name === "./nextScene") return helpers;
    if (name === "@/lib/lab") return { seconds: (value) => value.toFixed(2) };
    return { default: name };
  } });
  const candidate = { id: "one", cut: 25, incoming: { film_id: "film", source_start: 100, source_end: 105, crop: null }, incoming_authority: { t_start: 90, t_end: 120 }, preview_ready: true, preview_url: "/initial" };
  const selection = { candidate, scope: { t0: 20, t2: 30, current_cut: 25, cut_min: 23, cut_max: 27, passage_start: 0 }, jobId: "parent", option: 1, revision: 4, initialView: "adjust" };
  const requests = [], applied = [];
  let resolvePrepare;
  const props = { selection, outputRatio: 16 / 9, latestJob: null, busy: false, suspended: false,
    onClose() {}, onTimeChange() {}, onCutChange() {}, onCancel() {},
    onApply(id, adjustment) { applied.push({ id, adjustment }); },
    onPrepare(id, adjustment) { requests.push({ id, adjustment }); return new Promise((resolve) => { resolvePrepare = resolve; }); },
  };
  function render() {
    if (disposed) return;
    scheduled = false; cursor = 0;
    tree = exports.default(props);
    const run = effects; effects = []; run.forEach((effect) => effect());
  }
  function all(node) {
    if (!node || typeof node !== "object") return [];
    if (Array.isArray(node)) return node.flatMap(all);
    return [node, ...all(node.props?.children)];
  }
  const flush = async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); };
  render(); await flush();
  return { props, candidate, requests, applied, flush,
    get source() { return all(tree).find((node) => node.type === "./SourceSceneEditor"); },
    get pair() { return all(tree).find((node) => node.type === "./NextScenePairPlayer"); },
    button(label) { return all(tree).find((node) => node.type === "button" && node.props.children === label); },
    update(value) { Object.assign(props, value); render(); },
    resolve(value) { resolvePrepare(value); },
    dispose() { disposed = true; hooks.forEach((hook) => hook.cleanup?.()); },
  };
}

test("source draft survives pending preview, then Use requires the completed exact crop and timing proof", async () => {
  const app = await setup();
  try {
    const crop = { x: 0.2, y: 0, width: 0.6, height: 1 };
    app.source.props.onChange({ source_start: 101, crop }); await app.flush();
    assert.equal(app.button("Use scene").props.disabled, true);
    app.button("Preview with music").props.onClick(); await app.flush();
    assert.deepEqual(clone(app.requests[0]), { id: "one", adjustment: { source_start: 101, cut_time: 25, crop } });
    assert.ok(app.source, "preparation does not remove the raw monitor or draft");
    assert.equal(app.source.props.disabled, true);
    assert.equal(app.source.props.value.source_start, 101);
    app.resolve({ id: "preview", kind: "next-scene-preview", status: "queued" }); await app.flush();
    app.update({ busy: true, latestJob: { id: "preview", status: "running" } });
    assert.deepEqual(clone(app.source.props.value.crop), crop);
    const prepared = { ...app.candidate, incoming: { ...app.candidate.incoming, source_start: 101, source_end: 106, crop }, preview_url: "/prepared" };
    app.update({ busy: false, latestJob: { id: "preview", kind: "next-scene-preview", status: "completed", result: { next_scene_job_id: "parent", candidate: prepared } } }); await app.flush();
    assert.ok(app.pair, "completion moves to rendered A→B with music");
    assert.equal(app.button("Use scene").props.disabled, false);
    app.button("Use scene").props.onClick();
    assert.deepEqual(clone(app.applied[0]), { id: "one", adjustment: { source_start: 101, cut_time: 25, crop, preview_job_id: "preview" } });
    app.button("Adjust scene").props.onClick(); await app.flush();
    app.source.props.onChange({ source_start: 101, crop: null }); await app.flush();
    assert.equal(app.button("Use scene").props.disabled, true, "resetting full frame invalidates the cropped preview");
  } finally { app.dispose(); }
});

for (const status of ["failed", "interrupted", "cancelled"]) test(`${status} preview preserves the current moment and crop for an explicit retry`, async () => {
  const app = await setup();
  try {
    app.source.props.onChange({ source_start: 103, crop: null }); await app.flush();
    app.button("Preview with music").props.onClick(); await app.flush();
    app.resolve({ id: "preview", status: "queued" }); await app.flush();
    app.update({ latestJob: { id: "preview", status, error: "Source temporarily unavailable" } }); await app.flush();
    assert.equal(app.source.props.value.source_start, 103);
    assert.equal(app.source.props.disabled, false);
    assert.equal(app.button("Use scene").props.disabled, true);
    assert.equal(app.button("Preview with music").props.disabled, false);
  } finally { app.dispose(); }
});
