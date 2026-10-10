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
vm.runInNewContext(compile("../kit/editorDirection.ts"), { exports: helpers });
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);

function harness(overrides = {}) {
  const events = [], exported = {};
  const jsx = (type, props) => ({ type, props });
  vm.runInNewContext(compile("MusicDirectionPanel.tsx"), { exports: exported, crypto: { randomUUID: () => "cue" }, require(name) {
    if (name === "react") return { useId: () => "direction" };
    if (name === "react/jsx-runtime") return { jsx, jsxs: jsx };
    if (name === "@/lib/lab") return { seconds: (value) => value.toFixed(2) };
    if (name === "@/features/lab/kit/editorDirection") return helpers;
    if (name.endsWith(".css")) return { default: new Proxy({}, { get: (_, key) => key }) };
    return { default: name };
  } });
  const props = { document: { track: { id: "song", duration: 90 }, passage: { start: 10, end: 40 }, film_ids: [], brief: "",
      clips: [{ id: "scene" }], music_timeline: { slots: [{ id: "slot", clip_id: "scene", needs_direction: false }] } },
    active: true, disabled: false, playhead: 14, hasEdit: true, onSeek() {},
    onChange(update, group) { props.document = update(props.document); events.push({ type: "change", group }); },
    onEndChange: () => events.push({ type: "end" }), onGenerate: () => events.push({ type: "generate" }), ...overrides };
  let tree;
  function render() { tree = exported.default(props); }
  render();
  return { props, events, render, get nodes() { return nodes(tree); },
    button(label) { return nodes(tree).find((node) => node.type === "button" && text(node).trim() === label); } };
}

test("typing and pace selection update only canonical inputs and never generate", () => {
  const ui = harness();
  const timeline = ui.props.document.music_timeline;
  ui.nodes.find((node) => node.type === "textarea" && node.props.maxLength === 24000).props.onChange({ target: { value: "Surreal color" } });
  ui.render();
  ui.nodes.find((node) => node.type === "input" && node.props.value === "rapid").props.onChange();
  assert.equal(ui.props.document.editor_direction.instruction, "Surreal color");
  assert.equal(ui.props.document.planner_settings.pacing, "rapid");
  assert.equal(ui.props.document.music_timeline, timeline);
  assert.ok(ui.events.every((event) => event.type !== "generate"));
  assert.equal(ui.events[0].group, "editor-instruction");
});

test("example buttons append and only the explicit Generate action triggers generation", () => {
  const ui = harness({ hasEdit: false });
  ui.nodes.find((node) => node.type === "textarea" && node.props.maxLength === 24000).props.onChange({ target: { value: "Keep this." } });
  ui.render();
  ui.button("Color & surrealism").props.onClick();
  assert.match(ui.props.document.editor_direction.instruction, /^Keep this\.\n\nSurreal/);
  ui.render(); ui.button("Generate edit").props.onClick();
  assert.equal(ui.events.filter((event) => event.type === "generate").length, 1);
  assert.equal(ui.events.at(-2).type, "end");
});

test("Undo or reload updates the displayed settings without a stale modal draft", () => {
  const ui = harness();
  ui.props.document = { ...ui.props.document, editor_direction: { instruction: "Restored", ranges: [] }, planner_settings: { pacing: "patient", lyric_treatment: "literal" } };
  ui.render();
  assert.equal(ui.nodes.find((node) => node.type === "textarea" && node.props.maxLength === 24000).props.value, "Restored");
  assert.equal(ui.nodes.find((node) => node.type === "input" && node.props.checked).props.value, "patient");
  assert.equal(ui.nodes.find((node) => node.type === "select").props.value, "literal");
});

test("locked, inactive, busy, and songless generation is blocked without mutating the document", () => {
  for (const override of [{ generationProblem: "Unlock clips first." }, { active: false }, { disabled: true },
    { document: { track: null, passage: { start: 0, end: 30 }, film_ids: [], brief: "" } }]) {
    const ui = harness(override);
    const generate = ui.button("Regenerate edit");
    assert.equal(generate.props.disabled, true);
    generate.props.onClick();
    assert.equal(ui.events.length, 0);
  }
});

test("notes and new lyric cues stay song-scoped without invalidating existing footage", () => {
  const ui = harness();
  const clips = ui.props.document.clips;
  ui.nodes.find((node) => node.type === "textarea" && node.props.maxLength === 4000).props.onChange({ target: { value: "Leaving home" } });
  ui.render(); ui.button("Cue at playhead").props.onClick();
  assert.equal(ui.props.document.song_context.track_id, "song");
  assert.equal(ui.props.document.song_context.lyrics[0].start, 14);
  assert.equal(ui.props.document.song_context.lyrics[0].end, 18);
  assert.equal(ui.props.document.clips, clips);
});
