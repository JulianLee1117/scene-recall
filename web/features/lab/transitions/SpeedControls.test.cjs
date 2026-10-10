const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const compile = (file) => ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
const helpers = {}, exported = {};
vm.runInNewContext(compile("transitions.ts"), { exports: helpers });
vm.runInNewContext(compile("SpeedControls.tsx"), { exports: exported, require(name) {
  if (name === "./transitions") return helpers;
  if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }), Fragment: "fragment" };
  return { default: new Proxy({}, { get: (_, key) => key }) };
} });
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
function harness(value = { ...helpers.DEFAULT_RETIME }, source = { film_id: "film", source_start: 10, source_end: 13 }) {
  let current = value;
  const props = () => ({ value: current, a: source, b: source, onChange: (next) => { current = next; } });
  return { get value() { return current; }, get tree() { return exported.default(props()); },
    button(label) { return nodes(this.tree).find((node) => node.type === "button" && text(node) === label); },
    control(label) { return nodes(this.tree).find((node) => node.props?.["aria-label"] === label); } };
}

test("Speed begins off and choosing a shape gives legal speeds and source-span bounds", () => {
  const ui = harness(undefined, { film_id: "short", source_start: 0, source_end: .3 });
  assert.equal(ui.button("Off").props["aria-pressed"], true);
  assert.equal(nodes(ui.tree).filter((node) => node.type === "input").length, 0);
  ui.button("Rush").props.onClick();
  assert.equal(ui.value.speed, 2);
  assert.equal(ui.value.span, .3);
  assert.equal(ui.control("Ramp window in source seconds").props.max, .3);
  assert.equal(ui.control("Edge source speed").props.min, 1);
  ui.button("Slow hit").props.onClick();
  assert.equal(ui.value.speed, .5);
  assert.equal(ui.control("Edge source speed").props.min, .25);
  assert.equal(ui.control("Edge source speed").props.max, 1);
  ui.button("Pulse").props.onClick();
  assert.equal(ui.control("Peak source speed").props.max, 4);
});

test("speed controls retain interpolation and curve while Off clears all effective retiming", () => {
  const ui = harness({ ...helpers.DEFAULT_RETIME, mode: "rush", speed: 2 });
  ui.control("Speed frame interpolation").props.onChange({ target: { value: "flow" } });
  ui.control("Speed curve").props.onChange({ target: { value: "snappy" } });
  ui.control("Ramp window in source seconds").props.onChange({ target: { value: "1.25" } });
  ui.button("Slow hit").props.onClick();
  assert.equal(ui.value.interpolation, "flow");
  assert.equal(ui.value.curve, "snappy");
  assert.equal(ui.value.span, 1.25);
  assert.match(text(ui.tree), /Slower to render; fast motion can warp/);
  assert.match(text(ui.tree), /render to see the new timing/);
  ui.button("Off").props.onClick();
  assert.deepEqual(JSON.parse(JSON.stringify(ui.value)), JSON.parse(JSON.stringify(helpers.DEFAULT_RETIME)));
});

test("duration receipts use retimed output frames and disappear for invalid source spans", () => {
  const ui = harness({ ...helpers.DEFAULT_RETIME, mode: "rush", speed: 4, span: 2 });
  assert.match(text(ui.control("Estimated clip durations after speed change")), /A 3.00s → 2.00s/);
  const invalid = harness({ ...helpers.DEFAULT_RETIME, mode: "rush", speed: 4, span: 2 }, { film_id: "short", source_start: 0, source_end: .3 });
  assert.equal(invalid.control("Estimated clip durations after speed change"), undefined);
});
