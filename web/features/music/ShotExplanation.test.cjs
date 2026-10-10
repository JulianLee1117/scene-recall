const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const compiled = ts.transpileModule(fs.readFileSync(path.join(__dirname, "ShotExplanation.tsx"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const exported = {};
vm.runInNewContext(compiled, { exports: exported, require(name) {
  if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }), Fragment: "fragment" };
  if (name === "@/lib/lab") return { mediaUrl: (value) => `http://media${value}`, seconds: (value) => value.toFixed(2) };
  return { default: new Proxy({}, { get: (_, key) => key }) };
} });
const clip = { id: "c", film_id: "film", unit_id: "shot", title: "Saved description", source_start: 51, source_end: 54 };
const base = () => ({
  slot: { id: "current", start: 3, end: 6, clip_id: "c", reason: "The color echoes the previous shot.",
    search_evidence: { rank: 2, matched_frame_index: 1, matched_frame_timestamp: 52, matched_text: "Indexed red coat", matched_text_view: "visual", matches: [{ facet: "look", evidence: { text: "Red fabric", timestamp: 52 } }] } },
  clip, films: { film: { title: "Film (1999)" }, before: { title: "Before (2001)" } },
  query: "A red shape moving through the frame", direction: { purpose: "Keep the color motif", music_cue: "Piano accent", timing_note: "Leave after the held note" },
  previous: { slot: { start: 0, end: 3 }, clip: { ...clip, film_id: "before", unit_id: "before-shot" } },
  next: { slot: { start: 6, end: 9 }, clip: null },
});
const section = (tree, label) => nodes(tree).find((node) => node.type === "section" && node.props["aria-label"] === label);

test("explanation separates recorded editorial intention from selected-source evidence and current neighboring scenes", () => {
  const props = base(), before = JSON.stringify(props), tree = exported.default(props);
  const intention = text(section(tree, "Planned intention"));
  assert.ok(intention.includes(props.query)); assert.ok(intention.includes("Piano accent"));
  assert.ok(intention.includes("Leave after the held note"));
  assert.equal(intention.includes("Indexed red coat"), false);
  assert.ok(text(section(tree, "Recorded selection")).includes(props.slot.reason));
  const evidence = text(section(tree, "Source evidence"));
  assert.ok(evidence.includes("Indexed red coat")); assert.ok(evidence.includes("Visual · Red fabric"));
  assert.ok(evidence.includes("51.00–54.00")); assert.ok(evidence.includes("Saved clip description"));
  assert.equal(evidence.includes(props.query), false);
  const context = nodes(tree).find((node) => node.props?.["aria-label"] === "Current sequence context");
  assert.ok(text(context).includes("Before (2001)")); assert.ok(text(context).includes("Film (1999)"));
  assert.ok(text(context).includes("Unfilled scene"));
  assert.equal(nodes(context).find((node) => node.type === "img" && node.props.src.endsWith("shot/1")).props.alt, "");
  assert.equal(JSON.stringify(props), before, "inspection does not alter saved data or create a reason");
});

test("manual and missing-evidence placements remain inspectable without inventing transition explanations", () => {
  const props = base();
  props.slot = { id: "current", start: 0, end: 3, reason: "Chosen by you", needs_direction: true };
  props.query = ""; props.direction = null; props.previous = null; props.next = null; props.films = {};
  const tree = exported.default(props);
  assert.ok(text(tree).includes("Film title unavailable"));
  assert.ok(text(tree).includes("No direction was saved"));
  assert.ok(text(tree).includes("marked for review"));
  assert.ok(text(section(tree, "Recorded selection")).includes("Chosen by you"));
  assert.ok(text(section(tree, "Source evidence")).includes("No search evidence is attached"));
  props.slot.reason = null;
  assert.ok(text(exported.default(props)).includes("No selection explanation was saved"));
});

test("unverified requirements are visible once and malformed sample indexes cannot select arbitrary image paths", () => {
  const props = base();
  props.slot.search_evidence.matched_frame_index = -1;
  props.slot.resolved_search = { unverified_requirements: ["Verify camera movement"] };
  props.direction.search_plan = { unverified_requirements: ["Verify camera movement", "Verify the gesture completes"] };
  const tree = exported.default(props);
  assert.equal(text(tree).split("Verify camera movement").length - 1, 1);
  assert.ok(text(tree).includes("Verify the gesture completes"));
  assert.ok(nodes(tree).some((node) => node.type === "img" && node.props.src.endsWith("/shot/0")));
});
