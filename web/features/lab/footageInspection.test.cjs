const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const compiled = (file) => ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const inspection = {};
vm.runInNewContext(compiled("footageInspection.ts"), { exports: inspection });
const parse = inspection.readFootageInspection;
const observation = {
  artifact_id: "evidence-1", unit_id: "film-shot-9", summary: "The hand reaches the handle in the sampled frames.",
  uncertainty: "Contact between samples is unclear.",
  events: [{ start: 60, end: 62.5, before: "Hand beside the door", completion: "Hand reaches the handle", after: "Door remains closed" }],
};
const target = (overrides = {}) => ({ slot_id: "slot-4", position: 4, hint: "action_timing", status: "reviewed", reason: "The selected moment includes the reach.",
  changed: true, observations: [observation], ...overrides });
const receipt = (overrides = {}) => ({ contract: "targeted-footage-inspection-v1", status: "partial", eligible_count: 3, inspected_count: 1,
  window_count: 2, cache_hits: 1, changed_count: 1, targets: [target(), target({ slot_id: "slot-8", position: 8, hint: "visual_fit", status: "unavailable", changed: false, observations: [], reason: "The sample could not be decoded." })],
  warning: "Some samples were unavailable; the other choices were kept.", ...overrides });

test("only the known optional inspection receipt is accepted", () => {
  for (const value of [undefined, null, {}, [], receipt({ contract: "unknown" }), receipt({ status: "running" })]) assert.equal(parse(value), null);
  assert.equal(parse(receipt()).status, "partial");
  assert.equal(parse(receipt({ status: "completed" })).status, "completed");
});

test("coverage means successful targets, not attempted windows or all flagged clips", () => {
  const parsed = parse(receipt());
  assert.equal(parsed.eligible_count, 3);
  assert.equal(parsed.inspected_count, 1);
  assert.equal(parsed.window_count, 2);
  assert.equal(parsed.cache_hits, 1);
  assert.equal(parsed.changed_count, 1);
  assert.equal(parsed.targets[1].status, "unavailable");
});

test("malformed counts stay unknown without false zero or full-coverage claims", () => {
  const parsed = parse(receipt({ eligible_count: "3", inspected_count: NaN, changed_count: -1, window_count: false, cache_hits: .5 }));
  for (const key of ["eligible_count", "inspected_count", "changed_count", "window_count", "cache_hits"]) assert.equal(parsed[key], null);
  assert.equal(parse(receipt({ inspected_count: 4 })).inspected_count, null);
  assert.equal(parse(receipt({ changed_count: 2 })).changed_count, null);
});

test("valid observations and uncertainty survive, malformed targets and event times are omitted", () => {
  const parsed = parse(receipt({ targets: [null, target({ position: 0 }), target({ slot_id: null }), target({ hint: "other" }),
    target({ changed: "false", observations: [null, { ...observation, artifact_id: null }, { ...observation, events: [
      null, { ...observation.events[0], start: -1 }, { ...observation.events[0], end: 59 }, observation.events[0],
    ] }] })] }));
  assert.equal(parsed.targets.length, 1);
  assert.equal(parsed.targets[0].changed, null);
  assert.deepEqual(JSON.parse(JSON.stringify(parsed.targets[0].observations)), [observation]);
});

const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const exported = {};
vm.runInNewContext(compiled("FootageInspectionDetails.tsx"), { exports: exported, require(name) {
  if (name === "./footageInspection") return inspection;
  if (name === "@/lib/lab") return { seconds: (value) => value.toFixed(2) };
  if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
  return { default: {} };
} });
const render = (value) => exported.default({ value });

test("partial findings are collapsed by one-based clip number with source times and uncertainty", () => {
  const tree = render(receipt()), content = text(tree);
  assert.match(content, /Partial coverage/);
  assert.match(content, /1 of 3 flagged clips inspected · 1 changed/);
  assert.match(content, /Sampled evidence, not continuous playback/);
  assert.match(content, /Clip 4 · Action timing · Changed/);
  assert.match(content, /Clip 8 · Visual fit · Unavailable/);
  assert.match(content, /Source 60.00 – 62.50/);
  assert.match(content, /CompletionHand reaches the handle/);
  assert.match(content, /Uncertainty: Contact between samples is unclear/);
  assert.match(content, /Evidence evidence-1 · Source film-shot-9/);
  assert.match(content, /Some samples were unavailable/);
  assert.ok(nodes(tree).filter(node => node.type === "details").every(node => !node.props.open));
});

test("unavailable and not-needed outcomes never imply successful playback verification", () => {
  const unavailable = text(render(receipt({ status: "unavailable", inspected_count: 0, changed_count: 0, targets: [] })));
  assert.match(unavailable, /Unavailable0 of 3 flagged clips inspected · 0 changed/);
  const notNeeded = text(render(receipt({ status: "not-needed", eligible_count: 0, inspected_count: 0, changed_count: 0, window_count: 0, cache_hits: 0, targets: [], warning: undefined })));
  assert.match(notNeeded, /No clips were flagged for inspection/);
  assert.doesNotMatch(notNeeded, /Findings by clip/);
  const missing = text(render(receipt({ eligible_count: null, inspected_count: null })));
  assert.match(missing, /Inspection coverage was not recorded/);
  assert.doesNotMatch(missing, /0 of 0/);
  assert.equal(render(null), null);
});
