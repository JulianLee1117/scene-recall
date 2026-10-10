const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const compiled = ts.transpileModule(fs.readFileSync(path.join(__dirname, "SceneLibraryPanel.tsx"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const source = { id: "saved", film_id: "film", unit_id: "shot", source_start: 10, source_end: 16, locked: false, reference_time: 15 };
function setup(overrides = {}) {
  let saved = false, tree;
  const exported = {}, calls = [];
  const props = {
    slot: { id: "slot", start: 0, end: 3, alternatives: [{ clip: { ...source, id: "result" }, search_evidence: { rank: 1 } }] },
    currentClip: null, savedClips: [source], query: "A person walks", films: { film: { title: "Film" } },
    disabled: false, searching: false, searchProblem: null, searchOptions: "Canonical clues",
    onQuery: (value) => calls.push(["query", value]), onSearch: () => calls.push(["search"]), onPlan() {},
    onPreview: (clip, evidence) => calls.push(["preview", clip, evidence]), onChoose() {}, onDragScene() {}, onDropScene() {},
    onClearSaved: () => calls.push(["clear"]), ...overrides,
  };
  vm.runInNewContext(compiled, { exports: exported, require(name) {
    if (name === "react") return { useState: () => [saved, (value) => { saved = value; }], useEffect() {} };
    if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }), Fragment: "fragment" };
    return { default: name };
  } });
  const render = () => { tree = exported.default(props); };
  render();
  return { props, calls, render, get tree() { return tree; },
    find: (predicate) => nodes(tree).find(predicate),
    button: (label) => nodes(tree).find((node) => node.type === "button" && text(node).startsWith(label)),
  };
}

test("saved clips use the same choices with an exact placement window and no old search evidence", () => {
  const app = setup();
  app.button("Saved clips").props.onClick(); app.render();
  const choices = app.find((node) => node.type === "./MusicSceneChoices").props;
  const clip = choices.alternatives[0].clip;
  assert.equal(clip.source_start, 10); assert.equal(clip.source_end, 13);
  assert.equal(clip.reference_time, null);
  assert.equal(source.source_end, 16, "preview fitting must not shorten retained footage");
  choices.onPreview(clip);
  assert.equal(app.calls[0][2], null);
  assert.equal(choices.onChoose, app.props.onChoose);
  assert.equal(choices.onDropScene, app.props.onDropScene);
});

test("typing or searching from saved clips returns to the current query results", () => {
  const app = setup();
  app.button("Saved clips").props.onClick(); app.render();
  app.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} }); app.render();
  assert.equal(app.find((node) => node.type === "./MusicSceneChoices").props.alternatives[0].clip.id, "result");
  assert.deepEqual(app.calls, [["search"]]);
  app.button("Saved clips").props.onClick(); app.render();
  app.find((node) => node.type === "textarea").props.onChange({ target: { value: "New direction" } }); app.render();
  assert.equal(app.button("Results").props["aria-pressed"], true);
  assert.deepEqual(app.calls.at(-1), ["query", "New direction"]);
});

test("saved access appears only when needed and clearing respects locked footage", () => {
  assert.equal(setup({ savedClips: [] }).button("Saved clips"), undefined);
  const app = setup({ savedClips: [{ ...source, locked: true, source_end: 11 }] });
  app.button("Saved clips").props.onClick(); app.render();
  assert.equal(app.button("Clear").props.disabled, true);
  assert.equal(app.find((node) => node.type === "./MusicSceneChoices").props.alternatives[0].clip.source_end, 11, "short saved footage must not be extended");
  app.props.savedClips = []; app.render();
  assert.equal(app.find((node) => node.type === "./MusicSceneChoices").props.alternatives[0].clip.id, "result");
});

test("an unfilled clip explains abstention before search and results without duplicating the reason", () => {
  const app = setup();
  app.props.slot.search_error = "The available scenes do not show the requested separation.";
  app.render();
  const status = app.tree.props.children[0];
  assert.equal(status.props.role, "status");
  assert.equal(text(status), `No scene selected${app.props.slot.search_error}`);
  assert.equal(app.tree.props.children[1].type, "form");
  assert.equal(text(app.tree).split(app.props.slot.search_error).length - 1, 1);
  assert.ok(app.find((node) => node.type === "./MusicSceneChoices"), "alternatives remain available for an explicit choice");
  app.button("Saved clips").props.onClick(); app.render();
  assert.equal(text(app.tree.props.children[0]), text(status), "saved results do not hide the timeline gap's explanation");
});

test("a failed replacement keeps its existing clip and separate search error", () => {
  const app = setup({ currentClip: source });
  app.props.slot.search_error = "No suitable replacement was found.";
  app.render();
  assert.equal(text(app.tree).includes("No scene selected"), false);
  const status = app.find((node) => node.props?.role === "status");
  assert.equal(status.type, "p");
  assert.equal(text(status), app.props.slot.search_error);
  assert.equal(app.find((node) => node.type === "./MusicSceneChoices").props.currentClip, source);
});

test("Why this shot keeps recorded context behind an anchored panel and respects locked direction controls", () => {
  const previous = { slot: { id: "before", start: 0, end: 1 }, clip: source };
  const app = setup({ previous, currentClip: { ...source, locked: true } });
  const popover = app.find((node) => node.type === "@/features/lab/EditorPopover" && node.props.label === "Why this shot");
  assert.ok(popover);
  assert.equal(app.find((node) => node.type === "./ShotExplanation"), undefined, "details are not inserted into the main page");
  const panel = popover.props.children(() => {});
  const explanation = nodes(panel).find((node) => node.type === "./ShotExplanation");
  assert.equal(explanation.props.previous, previous);
  assert.equal(explanation.props.slot, app.props.slot);
  assert.equal(explanation.props.clip, app.props.currentClip);
  assert.equal(nodes(panel).find((node) => node.type === "button").props.disabled, true);
});
