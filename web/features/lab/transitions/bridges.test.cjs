const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const helpers = {};
const directions = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "directions.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: directions });
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "bridges.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: helpers, require: () => directions });
const plain = value => JSON.parse(JSON.stringify(value));
const request = (start = "", end = "", length = "") => helpers.bridgeRequest("parent-render", start, end, length, " Provider ", " Model ", " Prompt ");

test("blank trims preserve the original bridge duration without implicit retiming", () => {
  assert.deepEqual(plain(request()), { parent_render_id: "parent-render", trim_start: 0,
    provenance: { provider: "Provider", model: "Model", prompt: "Prompt" } });
  assert.equal(Object.hasOwn(request(), "playback_duration"), false);
  assert.equal(Object.hasOwn(request(), "trim_end"), false);
});
test("explicit trims and playback length remain separate from provenance", () => {
  assert.deepEqual(plain(request("1.25", "3.25", "0.3")), { parent_render_id: "parent-render",
    trim_start: 1.25, trim_end: 3.25, playback_duration: .3,
    provenance: { provider: "Provider", model: "Model", prompt: "Prompt" } });
  assert.deepEqual(plain(helpers.bridgeRequest("other", "", "", "", " ", "", "").provenance),
    { provider: null, model: null, prompt: null });
});
test("bridge trims reject invalid numbers and out-of-range or reversed windows", () => {
  for (const start of ["NaN", "Infinity", "-1", "30", "word"]) assert.throws(() => request(start));
  for (const end of ["NaN", "Infinity", "30.1", "0", "1", "1.079"]) assert.throws(() => request("1", end));
  assert.equal(request("1", "1.08").trim_end, 1.08);
  for (const length of ["NaN", "Infinity", "0", "-1", "0.079", "30.1"]) assert.throws(() => request("", "", length));
  assert.equal(request("", "", ".08").playback_duration, .08);
  assert.equal(request("", "", "30").playback_duration, 30);
});
test("only queued and running bridge jobs stay in the polling set", () => {
  for (const status of ["queued", "running"]) assert.equal(helpers.bridgePending({ status }), true);
  for (const status of ["completed", "failed", "cancelled", "interrupted"]) assert.equal(helpers.bridgePending({ status }), false);
});

test("prompt building preserves explicit source motion notes and bounds their size", () => {
  const notes = "A moves left; B moves left more slowly. Keep the doorway centered.";
  const result = helpers.buildBridgePrompt("occlusion", "  " + notes + "  ");
  assert.ok(result.endsWith(notes));
  assert.ok(result.includes("foreground surface"));
  assert.equal(helpers.buildBridgePrompt("occlusion", "  "), helpers.BRIDGE_PROMPTS.find(p => p.id === "occlusion").text);
  assert.throws(() => helpers.buildBridgePrompt("unknown"));
  assert.throws(() => helpers.buildBridgePrompt("whip", "x".repeat(2001)));
  assert.ok(helpers.buildBridgePrompt("whip", "x".repeat(2000)).length < 15000);
});

test("AI timing guard leaves historical and Speed off parents available", () => {
  assert.equal(helpers.bridgeSourceTimingProblem(), "");
  assert.equal(helpers.bridgeSourceTimingProblem({ mode: "off", speed: 3 }), "");
  for (const mode of ["rush", "slow-hit", "pulse"])
    assert.match(helpers.bridgeSourceTimingProblem({ mode }), /Render a version with Speed off/);
});

test("AI starters and source changes preserve custom writing until explicitly applied", () => {
  const hooks = [], component = {};
  let cursor = 0, tree;
  let props = { job: null, active: true };
  const render = () => { cursor = 0; tree = component.default(props); };
  const nodes = node => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
  const text = node => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "AIHandoff.tsx"), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText, { exports: component, require(name) {
    if (name === "react") return { useState(initial) { const i = cursor++; if (!(i in hooks)) hooks[i] = typeof initial === "function" ? initial() : initial; return [hooks[i], value => { hooks[i] = value; render(); }]; } };
    if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
    if (name === "./bridges") return helpers;
    if (name === "./GenerationPanel") return { default: "GenerationPanel" };
    if (name === "./BridgeImports") return { default: "BridgeImports" };
    if (name === "@/lib/lab") return { mediaUrl: value => value, seconds: value => String(value) };
    return { default: new Proxy({}, { get: (_, key) => key }) };
  } });
  const field = label => nodes(tree).find(node => node.props?.["aria-label"] === label);
  const button = label => nodes(tree).find(node => node.type === "button" && text(node) === label);
  render();
  assert.equal(field("AI bridge energy").props.value, "restrained");
  assert.equal(field("AI transition prompt").props.value, helpers.buildBridgePrompt("whip", "", { energy: "restrained", direction: "auto" }));
  field("AI transition prompt").props.onChange({ target: { value: "My precise camera move" } });
  field("AI bridge technique").props.onChange({ target: { value: "occlusion" } });
  field("Source motion notes").props.onChange({ target: { value: "Travel left past a column" } });
  assert.equal(field("AI transition prompt").props.value, "My precise camera move");
  assert.match(text(tree), /Direction changed/);
  button("Apply direction to prompt").props.onClick();
  assert.match(field("AI transition prompt").props.value, /Travel left past a column/);
  const applied = field("AI transition prompt").props.value;
  props = { ...props, job: { id: "retimed", request: { outgoing: { source_start: 1, source_end: 3 }, incoming: { source_start: 4, source_end: 6 }, retime: { mode: "rush" } }, result: { frame_a_url: "/a", frame_b_url: "/b" } } }; render();
  assert.equal(field("AI transition prompt").props.value, applied);
  assert.match(text(tree), /Render a version with Speed off/);
  assert.equal(button("Download prompt & handoff receipt").props.disabled, false);
  props = { ...props, sourcePairChanged: true, job: { ...props.job, request: { ...props.job.request, retime: { mode: "off" } } } }; render();
  assert.equal(field("AI transition prompt").props.value, applied);
  assert.match(text(tree), /Saved pair · differs from working clips/);
  field("AI bridge travel direction").props.onChange({ target: { value: "left" } });
  field("AI bridge technique").props.onChange({ target: { value: "focus" } });
  assert.equal(field("AI bridge travel direction").props.value, "auto");
  assert.equal(field("AI transition prompt").props.value, applied);
  const method = nodes(tree).find(node => node.props?.id === "ai-import-tab"); method.props.onClick();
  assert.equal(nodes(tree).find(node => node.props?.id === "ai-generate-panel").props.hidden, true);
  assert.equal(nodes(tree).find(node => node.props?.id === "ai-import-panel").props.hidden, false);
  assert.equal(nodes(tree).find(node => node.type === "GenerationPanel").props.active, false);
  assert.equal(nodes(tree).find(node => node.type === "BridgeImports").props.active, true);
  assert.equal(field("AI transition prompt").props.value, applied);
  field("AI bridge technique").props.onChange({ target: { value: "portal" } });
  assert.match(text(tree), /Experimental direction/);
  const saved = { id: "saved-ai", request: { parent_render_id: props.job.id, prompt: "Saved generation direction", model: "h3_max", seed: 42 } };
  nodes(tree).find(node => node.type === "GenerationPanel").props.onAdjustTiming(saved);
  assert.equal(nodes(tree).find(node => node.props?.id === "ai-import-panel").props.hidden, false);
  assert.equal(nodes(tree).find(node => node.type === "BridgeImports").props.generationSource.id, saved.id);
  assert.equal(nodes(tree).find(node => node.type === "BridgeImports").props.generationSource.request.prompt, "Saved generation direction");
  assert.equal(field("AI transition prompt").props.value, applied, "timing adjustment never rewrites the draft generation prompt");
  assert.equal(field("AI provider and model"), undefined, "the loaded generation uses its saved notes instead of external draft fields");
  nodes(tree).find(node => node.type === "BridgeImports").props.onExternalFile();
  assert.equal(nodes(tree).find(node => node.type === "BridgeImports").props.generationSource, null);
  assert.ok(field("AI provider and model"));
});
