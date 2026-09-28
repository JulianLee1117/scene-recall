const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const compile = (file, jsx = false) => ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, ...(jsx ? { jsx: ts.JsxEmit.ReactJSX } : {}) } }).outputText;
const transitions = {}; vm.runInNewContext(compile("transitions.ts"), { exports: transitions });
const helpers = {}; vm.runInNewContext(compile("pair-timeline.ts"), { exports: helpers, require: () => transitions });
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
async function harness(overrides = {}) {
  const hooks = [], effects = [], exported = {}, selected = [], sought = [], trimmed = [], durations = [], previews = [];
  let index = 0, tree, scheduled = false;
  let props = { a: { film_id: "film-a", title: "First", source_start: 10, source_end: 13 }, b: { film_id: "film-b", title: "Second", source_start: 20, source_end: 23 },
    recipe: { id: "whip-pan", duration: .4 }, retime: transitions.DEFAULT_RETIME, selectedSide: null, position: null, disabled: false,
    onSelect: (side) => selected.push(side), onSeek: (side, time) => sought.push({ side, time }), onTrim: (side, edge, time) => trimmed.push({ side, edge, time }),
    onDuration: (value) => durations.push(value), onChoose() {}, onSelectTransition() {}, ...overrides };
  const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
  const schedule = () => { if (!scheduled) { scheduled = true; queueMicrotask(render); } };
  const react = {
    useState(initial) { const key = index++; hooks[key] ??= { value: initial }; return [hooks[key].value, (value) => { const next = typeof value === "function" ? value(hooks[key].value) : value; if (!Object.is(next, hooks[key].value)) { hooks[key].value = next; schedule(); } }]; },
    useRef(initial) { return hooks[index++] ??= { current: initial }; },
    useMemo(callback, deps) { const key = index++; if (!hooks[key] || !same(hooks[key].deps, deps)) hooks[key] = { value: callback(), deps }; return hooks[key].value; },
    useEffect(callback, deps) { const key = index++; if (!hooks[key] || !same(hooks[key].deps, deps)) { hooks[key] = { deps }; effects.push(callback); } },
  };
  vm.runInNewContext(compile("PairTimeline.tsx", true), { exports: exported, require(name) {
    if (name === "react") return react;
    if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }), Fragment: "fragment" };
    if (name === "./transitions") return transitions;
    if (name === "./pair-timeline") return helpers;
    return { default: new Proxy({}, { get: (_, key) => key }) };
  } });
  function render() { index = 0; scheduled = false; tree = exported.default(props); effects.splice(0).forEach((effect) => effect()); const canvas = nodes(tree).find((node) => node.props?.className === "canvas"); if (canvas) canvas.props.ref.current = { getBoundingClientRect: () => ({ left: 100, width: 560 }) }; }
  render(); await flush();
  return { selected, sought, trimmed, durations, previews, get nodes() { return nodes(tree); },
    label(value) { return nodes(tree).find((node) => node.props?.["aria-label"] === value); },
    async update(patch) { props = { ...props, ...patch }; schedule(); await flush(); },
  };
}
const pointer = (id, x) => ({ pointerId: id, clientX: x, button: 0, preventDefault() {}, stopPropagation() {}, currentTarget: { focus() {}, setPointerCapture() {} } });

test("clip bodies inspect their own source while the ruler can scrub the saved preview", async () => {
  const preview = [], ui = await harness({ onPreviewSeek: (value) => preview.push(value) });
  ui.label("Inspect clip B: Second").props.onClick({ detail: 1, clientX: 500 });
  assert.equal(ui.selected.at(-1), "b"); assert.ok(Math.abs(ui.sought.at(-1).time - 21.4) < 1e-8);
  ui.label("Preview timeline position").props.onChange({ target: { valueAsNumber: 1.5 } });
  assert.deepEqual(preview, [1.5]); assert.equal(ui.sought.length, 1, "preview scrubbing does not reselect a source");
});

test("the single playhead follows selected-source time ahead of stale preview time", async () => {
  const ui = await harness({ selectedSide: "b", position: { side: "b", sourceTime: 21 }, playbackTime: .2 });
  const playheads = ui.nodes.filter((node) => node.props?.className === "playhead");
  assert.equal(playheads.length, 1);
  assert.ok(Math.abs(parseFloat(playheads[0].props.style.left) - 3.6 / 5.6 * 100) < 1e-7);
});

test("trim dragging freezes scale while showing updated geometry and stops after cancellation", async () => {
  const ui = await harness();
  ui.label("Clip A out trim").props.onPointerDown(pointer(1, 400)); await flush();
  ui.label("Clip A out trim").props.onPointerMove(pointer(1, 350)); await flush();
  assert.equal(ui.trimmed.at(-1).time, 12.5);
  await ui.update({ a: { film_id: "film-a", title: "First", source_start: 10, source_end: 12.5 } });
  assert.ok(Math.abs(parseFloat(ui.label("Clip A out trim").props.style.left) - 2.5 / 5.6 * 100) < 1e-7, "handle follows the new trim on the original scale");
  ui.label("Clip A out trim").props.onPointerMove(pointer(1, 300));
  assert.equal(ui.trimmed.at(-1).time, 12, "next move uses the pointer-down source and viewport");
  ui.label("Clip A out trim").props.onPointerCancel({ pointerId: 1 }); await flush();
  const count = ui.trimmed.length;
  ui.label("Clip A out trim").props.onPointerMove(pointer(1, 250)); assert.equal(ui.trimmed.length, count);
});

test("changing films or disabling the strip invalidates an active drag", async () => {
  for (const patch of [{ disabled: true }, { a: { film_id: "new-film", title: "New", source_start: 10, source_end: 13 } }]) {
    const ui = await harness(); ui.label("Clip A out trim").props.onPointerDown(pointer(1, 400)); await flush();
    await ui.update(patch); ui.label("Clip A out trim").props.onPointerMove(pointer(1, 300));
    assert.equal(ui.trimmed.length, 0);
  }
});

test("overlap dragging has independent pointer capture and keyboard frame limits", async () => {
  const ui = await harness(), control = () => ui.label("Transition overlap duration");
  control().props.onPointerDown(pointer(2, 400)); await flush();
  control().props.onPointerMove(pointer(3, 300)); assert.equal(ui.durations.length, 0);
  control().props.onPointerMove(pointer(2, 380)); assert.ok(Math.abs(ui.durations.at(-1) - .6) < 1e-8);
  control().props.onLostPointerCapture({ pointerId: 2 }); await flush();
  control().props.onKeyDown({ key: "Home", preventDefault() {} }); assert.equal(ui.durations.at(-1), .1);
  control().props.onKeyDown({ key: "End", preventDefault() {} }); assert.equal(ui.durations.at(-1), 2);
});

test("trim handles expose keyboard bounds and honor known film duration", async () => {
  const ui = await harness({ filmDurations: { b: 25 } });
  const out = ui.label("Clip B out trim");
  assert.equal(out.props["aria-valuemax"], 25);
  out.props.onKeyDown({ key: "End", preventDefault() {} }); assert.equal(ui.trimmed.at(-1).time, 25);
  ui.label("Clip A in trim").props.onKeyDown({ key: "ArrowRight", shiftKey: false, preventDefault() {} });
  assert.equal(ui.trimmed.at(-1).time, 10.033);
});
