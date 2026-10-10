const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const compile = (name) => ts.transpileModule(fs.readFileSync(path.join(__dirname, name), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const compiled = compile("MusicWorkspace.tsx");
const limits = {};
vm.runInNewContext(compile("../../lib/labLimits.ts"), { exports: limits });
const musicEdit = {};
const dialogue = {};
vm.runInNewContext(compile("dialogueAudio.ts"), { exports: dialogue });
vm.runInNewContext(compile("musicEdit.ts"), { exports: musicEdit,
  require(name) { if (name === "@/lib/labLimits") return limits; if (name === "./dialogueAudio") return dialogue; throw new Error(name); } });
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children), ...nodes(node.props?.tools), ...nodes(node.props?.trailingTools)];
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const plain = (value) => JSON.parse(JSON.stringify(value));
const slot = (id, start, end, clip_id = null) => ({ id, start, end, clip_id, section_index: 0, alternatives: [] });

function harness({ document: overrides, search = "?project=project", ...stateOverrides } = {}) {
  const hooks = [], events = [], undo = [];
  let cursor = 0, tree, changed = false, effects = [];
  const browserWindow = { location: { search }, history: { state: null, replaceState(_state, _unused, query) { browserWindow.location.search = query; } },
    document: { querySelector: () => null, getElementById: () => null }, addEventListener() {}, removeEventListener() {} };
  const initialDocument = {
    track: { id: "song", name: "Song", duration: 90 }, passage: { start: 0, end: 30 }, aspect_ratio: "16:9", film_ids: [],
    clips: [{ id: "placed", film_id: "film", source_start: 12, source_end: 42, locked: false }],
    music_timeline: { track_id: "song", passage: { start: 0, end: 30 }, slots: [slot("first", 0, 30, "placed")] },
    ...overrides,
  };
  const state = {
    document: initialDocument, project: { id: "project", revision: 4, document: initialDocument },
    name: "My edit", busy: false, activeJob: false, job: null, dirty: false, canUndo: false,
    captureDraft: () => null,
    captureUndo: () => [...undo],
    endChange() {},
    save: async () => state.project,
    change(update) {
      const next = update(state.document);
      if (next !== state.document) {
        undo.push(state.document);
        state.document = next;
        state.dirty = true;
        state.canUndo = true;
      }
    },
    startJob(kind, options) { events.push({ kind, options: plain(options), document: plain(state.document) }); return Promise.resolve({ id: "requested-generation" }); },
    ...stateOverrides,
  };
  const react = {
    useEffect(effect, deps) {
      const index = cursor++, old = hooks[index];
      if (!old || deps.some((value, i) => !Object.is(value, old.deps[i]))) {
        hooks[index] = { deps };
        effects.push(() => { old?.cleanup?.(); hooks[index].cleanup = effect(); });
      }
    },
    useMemo: (compute) => compute(),
    useRef(initial) { return hooks[cursor++] ??= { current: initial }; },
    useState(initial) {
      const index = cursor++;
      hooks[index] ??= { value: typeof initial === "function" ? initial() : initial };
      return [hooks[index].value, (next) => { const value = typeof next === "function" ? next(hooks[index].value) : next;
        if (!Object.is(value, hooks[index].value)) { hooks[index].value = value; changed = true; } }];
    },
  };
  const jsx = (type, props) => ({ type, props });
  const exported = {};
  vm.runInNewContext(compiled, {
    exports: exported, window: browserWindow, URLSearchParams, AbortController, crypto: require("node:crypto").webcrypto,
    fetch: async () => ({ ok: true, json: async () => [] }),
    require(name) {
      if (name === "react") return react;
      if (name === "react/jsx-runtime") return { jsx, jsxs: jsx };
      if (name === "@/features/lab/useLabProject") return { useLabProject: () => state };
      if (name === "./useAudioWaveform") return { useAudioWaveform: () => ({ peaks: [], status: "" }) };
      if (name === "./musicEdit") return musicEdit;
      if (name === "./dialogueAudio") return dialogue;
      if (name === "@/lib/labLimits") return limits;
      if (name === "./nextScene") return { nextScenePair: () => ({ problem: null }), nextSceneResult: () => null };
      if (name === "@/lib/lab") return { seconds: (value) => value.toFixed(2), mediaUrl: (value) => value, labRequest: async () => ({ facets: [] }) };
      if (name.endsWith(".css")) return { default: new Proxy({}, { get: (_, key) => key }) };
      return { default: name };
    },
  });
  function render() { let passes = 0; do { changed = false; cursor = 0; effects = []; tree = exported.default();
    effects.forEach((effect) => effect()); if (++passes > 20) throw new Error("Render loop"); } while (changed); }
  render();
  return { events, state, undo, initialDocument, render, browserWindow,
    button(label) { return nodes(tree).find((node) => node.type === "button" && text(node).trim() === label); },
    settings() { return nodes(tree).find((node) => node.type === "./MusicDirectionPanel")?.props; },
    activeTab() { return nodes(tree).find((node) => node.props?.role === "tab" && node.props["aria-selected"])?.props.id; },
    player() { return nodes(tree).find((node) => node.type === "./MusicSequencePlayer")?.props; },
    timeline() { return nodes(tree).find((node) => node.type === "./MusicEditTimeline")?.props; },
    dialogue() { return nodes(tree).find((node) => node.type === "./DialogueEditor")?.props; },
    scenes() { return nodes(tree).find((node) => node.type === "./SceneLibraryPanel")?.props; },
    alerts() { return nodes(tree).filter((node) => node.type === "div" && node.props?.role === "alert").map(text); },
    progress() { return nodes(tree).find((node) => node.type === "@/features/lab/JobStatus")?.props; },
    picker() { return nodes(tree).find((node) => node.type === "./SongPassagePicker")?.props; },
    header() { return nodes(tree).find((node) => node.type === "@/features/lab/LabWorkspaceHeader")?.props; },
  };
}

test("Use dialogue from a locked scene or search result changes only the independent audio lane and keeps Undo", () => {
  const ui = harness({ document: { clips: [{ id: "placed", film_id: "film", source_start: 12, source_end: 16, locked: true }] } });
  const pictures = ui.state.document.clips, timeline = ui.state.document.music_timeline;
  ui.dialogue().onAddSelected(); ui.render();
  assert.equal(ui.state.document.dialogue_clips.length, 1);
  assert.equal(ui.state.document.clips, pictures); assert.equal(ui.state.document.music_timeline, timeline);
  assert.equal(ui.state.document.dialogue_clips[0].start, 0);
  assert.equal(ui.undo.at(-1).dialogue_clips, undefined);
  ui.scenes().onDialogue({ film_id: "other", source_start: 40, source_end: 42, title: "Another line" }, { matched_text_view: "dialogue", matched_text: "Stay with me" }); ui.render();
  assert.equal(ui.state.document.dialogue_clips[1].start, 0, "new voices keep their chosen start and may overlap");
  assert.equal(ui.state.document.dialogue_clips[1].text, "Stay with me");
  assert.equal(ui.dialogue().selectedId, ui.state.document.dialogue_clips[1].id);
  assert.equal(ui.undo.length, 2);
  ui.dialogue().onAuditionChange(true); ui.render(); assert.equal(ui.player().suspended, true);
  ui.dialogue().onAuditionChange(false); ui.render(); assert.equal(ui.player().suspended, false);
});

test("timeline following receives actual sequence playback and preview shares the existing film lookup", () => {
  const ui = harness();
  assert.equal(ui.timeline().playing, false);
  assert.equal(ui.player().filmTitles, ui.timeline().filmTitles);
  ui.player().onPlayingChange(true); ui.render();
  assert.equal(ui.timeline().playing, true);
  ui.player().onPlayingChange(false); ui.render();
  assert.equal(ui.timeline().playing, false);
});

test("a new music experiment starts with one song action, no automatic picker and no history", async () => {
  const document = { track: null, clips: [], music_timeline: null };
  let saves = 0;
  const snapshot = { project: { revision: 0 }, document };
  const ui = harness({ document, project: { id: "", revision: 0 }, isDraft: true,
    captureDraft: () => snapshot, restoreDraft: (value) => value === snapshot,
    save: async () => { saves++; return null; } });
  assert.equal(ui.picker(), undefined);
  assert.equal(ui.button("Create music edit"), undefined);
  assert.equal(nodes(ui.header().tools).some((node) => node.props?.label === "History"), false);
  await ui.button("Choose a song").props.onClick(); ui.render();
  assert.ok(ui.picker()); assert.equal(saves, 0);
  ui.picker().onClose(); ui.render();
  assert.equal(ui.picker(), undefined); assert.equal(saves, 0);
});

test("cancelling song selection restores the unsaved draft instead of looking up revision zero", async () => {
  const document = { track: null, clips: [], music_timeline: null };
  let restored = 0;
  const snapshot = { document, name: "Local name" };
  const ui = harness({ document, project: { id: "", revision: 0 }, isDraft: true, dirty: true,
    captureDraft: () => snapshot, restoreDraft: (value) => { assert.equal(value, snapshot); restored++; return true; },
    save: async () => assert.fail("opening a picker must not persist the draft"),
    restore: async () => assert.fail("there is no durable revision zero") });
  await ui.button("Choose a song").props.onClick(); ui.render();
  ui.picker().onClose(); ui.render(); assert.equal(restored, 1);
});

test("cancelling song selection in an existing project restores its saved revision and undo history", async () => {
  let restored;
  const history = [{ clips: ["prior"] }];
  const ui = harness({ captureUndo: () => history,
    restore: async (...args) => { restored = args; return true; } });
  ui.button("Song0.00 – 30.00 · Change section").props.onClick();
  for (let i = 0; i < 5; i++) await Promise.resolve();
  ui.render();
  assert.ok(ui.picker());
  ui.state.project.revision = 5;
  ui.picker().onClose();
  for (let i = 0; i < 5; i++) await Promise.resolve();
  ui.render();
  assert.equal(restored[0], 4); assert.equal(restored[1], history);
  assert.equal(ui.picker(), undefined);
});

test("existing edits open in Edit and opening AI direction neither changes nor generates the video", () => {
  const ui = harness();
  assert.equal(ui.activeTab(), "music-tab-edit");
  ui.button("AI direction").props.onClick(); ui.render();
  assert.equal(ui.activeTab(), "music-tab-ai");
  assert.equal(ui.settings().hasEdit, true);
  assert.equal(ui.events.length, 0);
  assert.equal(ui.state.document, ui.initialDocument);
  assert.equal(ui.timeline().canFillGaps, false);
  assert.equal(ui.player().suspended, true);
  assert.match(ui.browserWindow.location.search, /project=project&view=ai/);
});

test("Export video requests export quality and keeps unfilled edits disabled", () => {
  const ui = harness();
  ui.button("Export video").props.onClick();
  assert.equal(ui.events[0].kind, "render");
  assert.deepEqual(ui.events[0].options, { mode: "export" });
  const incomplete = harness({ document: { clips: [], music_timeline: { slots: [slot("gap", 0, 30)] } } });
  assert.equal(incomplete.button("Export video").props.disabled, true);
});

test("a failed job has one progress error while distinct errors and revision conflicts remain visible", () => {
  const error = "The AI chose timing outside the available footage.";
  const job = { id: "failed", kind: "generate", status: "failed", error };
  const ui = harness({ job, error });
  assert.equal(ui.progress().job.error, error);
  assert.deepEqual(ui.alerts(), []);
  ui.state.error = "Could not save the project.";
  ui.render();
  assert.deepEqual(ui.alerts(), ["Could not save the project."]);
  ui.state.error = error;
  ui.state.conflict = true;
  ui.render();
  assert.equal(ui.alerts().length, 1);
  assert.ok(ui.button("Load saved revision"));
});

test("generation uses the last direction edit while retaining scenes, timing and reasons", async () => {
  const ui = harness();
  ui.button("AI direction").props.onClick(); ui.render();
  ui.settings().onChange((current) => ({ ...current, editor_direction: { instruction: "Surreal red imagery", ranges: [] },
    planner_settings: { pacing: "kinetic", lyric_treatment: "metaphorical" } }), "direction");
  ui.render();
  ui.settings().onGenerate(); await Promise.resolve(); ui.render();
  assert.equal(ui.events.length, 1);
  assert.deepEqual(ui.events[0].options, { generate: { mode: "regenerate" } });
  assert.equal(ui.events[0].document.planner_settings.pacing, "kinetic");
  assert.equal(ui.events[0].document.editor_direction.instruction, "Surreal red imagery");
  assert.deepEqual(ui.events[0].document.clips, ui.initialDocument.clips);
  assert.deepEqual(ui.events[0].document.music_timeline, ui.initialDocument.music_timeline);
  assert.equal(ui.undo[0], ui.initialDocument);
  assert.equal(ui.activeTab(), "music-tab-ai");
});

test("an all-abstained generated edit still offers regeneration and fill gaps separately", () => {
  const ui = harness({ document: { clips: [], analysis: { draft: { selected_count: 0 } },
    music_timeline: { slots: [slot("gap", 0, 30)] } } });
  assert.equal(ui.settings().hasEdit, true);
  ui.settings().onGenerate();
  assert.deepEqual(ui.events[0].options, { generate: { mode: "regenerate" } });
});

test("fill gaps keeps existing scenes and timing and uses the fill mode", () => {
  const ui = harness({ document: { music_timeline: { slots: [slot("first", 0, 15, "placed"), slot("gap", 15, 30)] } } });
  ui.timeline().onFillGaps();
  assert.deepEqual(ui.events[0].options, { generate: { mode: "fill" } });
  assert.equal(ui.state.document, ui.initialDocument);
});

test("fill gaps freezes the displayed arrangement of a legacy project before submitting", () => {
  const ui = harness({ document: { music_timeline: null,
    clips: [{ id: "placed", film_id: "film", source_start: 12, source_end: 24, locked: false }],
  } });
  const displayed = plain(ui.timeline().plan);
  ui.timeline().onFillGaps();
  assert.deepEqual(ui.events[0].document.music_timeline, displayed);
  assert.deepEqual(ui.events[0].options, { generate: { mode: "fill" } });
  assert.equal(ui.state.document.clips, ui.initialDocument.clips);
});

test("placed locks explain and block regeneration but a lock in saved clips does not block it", () => {
  for (const placed of [true, false]) {
    const ui = harness({ document: {
      clips: [
        { id: "placed", film_id: "film", source_start: 12, source_end: 42, locked: placed },
        { id: "saved", film_id: "film", source_start: 100, source_end: 110, locked: !placed },
      ],
    } });
    if (placed) assert.match(ui.settings().generationProblem, /Unlock the clips/);
    else assert.equal(ui.settings().generationProblem, undefined);
    ui.settings().onGenerate();
    assert.equal(ui.events.length, placed ? 0 : 1);
    if (placed) assert.equal(ui.state.document, ui.initialDocument);
  }
});

test("first generation explicitly rebuilds and settings edits alone never generate", () => {
  const ui = harness({ document: { clips: [], music_timeline: { slots: [slot("gap", 0, 30)] } } });
  assert.equal(ui.activeTab(), "music-tab-ai");
  assert.equal(ui.settings().hasEdit, false);
  ui.settings().onChange((current) => ({ ...current, planner_settings: { pacing: "kinetic", lyric_treatment: "ignore" } })); ui.render();
  assert.equal(ui.events.length, 0);
  assert.equal(ui.state.document.planner_settings.pacing, "kinetic");
  ui.settings().onGenerate();
  assert.deepEqual(ui.events[0].options, { generate: { mode: "regenerate" } });
});

test("tabs preserve direction and selection, honor a saved view, and suspend the hidden timeline", () => {
  const ui = harness({ search: "?project=project&view=ai" });
  assert.equal(ui.activeTab(), "music-tab-ai");
  assert.equal(ui.timeline().disabled, true);
  ui.settings().onChange((current) => ({ ...current, editor_direction: { instruction: "Keep returning to mirrors", ranges: [] } }));
  ui.button("Edit").props.onClick(); ui.render();
  ui.timeline().onSelect("first"); ui.render();
  ui.button("AI direction").props.onClick(); ui.render();
  ui.button("Edit").props.onClick(); ui.render();
  assert.equal(ui.timeline().selectedId, "first");
  assert.equal(ui.settings().document.editor_direction.instruction, "Keep returning to mirrors");
  assert.equal(ui.events.length, 0);
});

test("only this page's successful applied generation opens Edit", async () => {
  for (const result of ["success", "failed", "cancelled", "stale", "older"]) {
    const ui = harness({ search: "?project=project&view=ai" });
    ui.settings().onGenerate();
    for (let i = 0; i < 5; i++) await Promise.resolve();
    ui.render();
    ui.state.job = { id: result === "older" ? "old-job" : "requested-generation", kind: "generate",
      status: ["failed", "cancelled"].includes(result) ? result : "completed",
      result: { applied: result !== "stale", revision: 5 } };
    ui.state.project.revision = 5; ui.state.dirty = false; ui.render();
    assert.equal(ui.activeTab(), result === "success" ? "music-tab-edit" : "music-tab-ai", result);
  }
});

test("removing a scene preserves its interval, and hidden or locked scenes cannot be removed", () => {
  const ui = harness();
  ui.timeline().onRemoveScene("first"); ui.render();
  assert.equal(ui.state.document.music_timeline.slots[0].clip_id, null);
  assert.equal(ui.state.document.music_timeline.slots[0].end, 30);
  assert.equal(ui.undo[0], ui.initialDocument);
  const hidden = harness({ search: "?view=ai" });
  hidden.timeline().onRemoveScene("first");
  assert.equal(hidden.state.document, hidden.initialDocument);
  const locked = harness(); locked.state.document.clips[0].locked = true;
  locked.timeline().onRemoveScene("first");
  assert.equal(locked.state.document, locked.initialDocument);
});
