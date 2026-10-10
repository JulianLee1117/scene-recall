const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const compiled = ts.transpileModule(fs.readFileSync(path.join(__dirname, "DirectionTimeInput.tsx"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];

function harness(overrides = {}) {
  const state = [], events = [], exported = {};
  let cursor = 0, tree, effects = [];
  const react = {
    useId: () => "time-help",
    useRef(initial) { return state[cursor++] ??= { current: initial }; },
    useState(initial) { const index = cursor++; state[index] ??= { value: initial };
      return [state[index].value, (next) => { state[index].value = next; }]; },
    useEffect(effect, deps) { const index = cursor++, previous = state[index];
      if (!previous || deps.some((value, i) => !Object.is(value, previous[i]))) effects.push(effect);
      state[index] = deps;
    },
  };
  const jsx = (type, props) => ({ type, props });
  vm.runInNewContext(compiled, { exports: exported, require(name) {
    if (name === "react") return react;
    if (name === "react/jsx-runtime") return { jsx, jsxs: jsx };
    return { default: new Proxy({}, { get: (_, key) => key }) };
  } });
  const props = { label: "In · seconds", value: 3, min: -10, max: 28, disabled: false,
    onChange(value) { events.push({ type: "change", value }); props.value = value; }, onEndChange() { events.push({ type: "end" }); }, ...overrides };
  function render() { cursor = 0; tree = exported.default(props); const pending = effects; effects = []; pending.forEach((effect) => effect()); }
  render();
  return { props, events, render, get input() { return nodes(tree).find((node) => node.type === "input").props; },
    type(value) { this.input.onChange({ target: { value } }); render(); },
  };
}

test("blank, negative and decimal typing stays visible while valid values publish immediately", () => {
  const ui = harness(); ui.input.onFocus(); ui.render();
  ui.type(""); assert.equal(ui.input.value, ""); assert.equal(ui.events.length, 0);
  ui.type("-"); assert.equal(ui.input.value, "-"); assert.equal(ui.events.length, 0);
  ui.type("-2"); assert.equal(ui.props.value, -2);
  ui.type("-2."); assert.equal(ui.input.value, "-2.");
  ui.type("-2.35"); assert.equal(ui.props.value, -2.35);
  assert.equal(ui.events.at(-1).value, -2.35, "no blur is required before Save or Generate reads the project");
  ui.input.onBlur(); ui.render();
  assert.equal(ui.input.value, "-2.35"); assert.equal(ui.events.at(-1).type, "end");
});

test("temporarily out-of-range digits are not clamped until blur", () => {
  const ui = harness({ value: 26, min: 24, max: 40 }); ui.input.onFocus(); ui.render();
  ui.type("3"); assert.equal(ui.input.value, "3"); assert.equal(ui.props.value, 26);
  assert.equal(ui.input["aria-invalid"], true);
  assert.equal(ui.input["aria-label"], "In · seconds", "the validation hint must not change the accessible input name");
  assert.equal(ui.input["aria-describedby"], "time-help");
  ui.type("35"); assert.equal(ui.props.value, 35); assert.equal(ui.input["aria-invalid"], undefined);
  ui.type("50"); assert.equal(ui.props.value, 35);
  ui.input.onBlur(); ui.render();
  assert.equal(ui.props.value, 40); assert.equal(ui.input.value, "40");
});

test("incomplete input restores the last valid value without publishing invalid state", () => {
  const ui = harness(); ui.input.onFocus(); ui.render(); ui.type("-");
  ui.input.onBlur(); ui.render();
  assert.equal(ui.input.value, "3");
  assert.deepEqual(ui.events, [{ type: "end" }]);
});

test("external Undo replaces a typing draft and arrow nudges respect exact bounds", () => {
  const ui = harness(); ui.input.onFocus(); ui.render(); ui.type("12.");
  ui.props.value = 3; ui.render(); ui.render();
  assert.equal(ui.input.value, "3");
  ui.input.onKeyDown({ key: "ArrowUp", shiftKey: true, nativeEvent: {}, preventDefault() {} }); ui.render();
  assert.equal(ui.props.value, 4);
  ui.props.max = 4.05; ui.render();
  ui.input.onKeyDown({ key: "ArrowUp", nativeEvent: {}, preventDefault() {} }); ui.render();
  assert.equal(ui.props.value, 4.05);
});
