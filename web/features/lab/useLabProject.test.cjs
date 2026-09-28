const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const compiled = ts.transpileModule(
  fs.readFileSync(path.join(__dirname, "useLabProject.ts"), "utf8"),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
).outputText;
const directionHelpers = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "editorDirection.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: directionHelpers });
const plain = (value) => JSON.parse(JSON.stringify(value));
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};

// Exercise the actual hook's async state boundaries without a browser or server.
async function projectHarness({ active = false, saveGate, deleteConflict = false, storageError = false, cleanupPending = false, draft = false, initialDocument = {} } = {}) {
  const hooks = [];
  const effects = [];
  const requests = [];
  const clearedKeys = [];
  let cursor = 0;
  let scheduled = false;
  let state;
  let saved = {
    id: draft ? "" : "project", experiment_id: "music-sketch", name: "My edit", revision: draft ? 0 : 3,
    document: { brief: "Original brief", clips: [], passage: { start: 0, end: 30 }, ...initialDocument },
  };
  const same = (a, b) => a && b && a.length === b.length && a.every((value, index) => Object.is(value, b[index]));
  const schedule = () => {
    if (!scheduled) {
      scheduled = true;
      queueMicrotask(render);
    }
  };
  const react = {
    useState(initial) {
      const index = cursor++;
      hooks[index] ??= { value: typeof initial === "function" ? initial() : initial };
      return [hooks[index].value, (update) => {
        const next = typeof update === "function" ? update(hooks[index].value) : update;
        if (!Object.is(next, hooks[index].value)) {
          hooks[index].value = next;
          schedule();
        }
      }];
    },
    useRef(initial) { const index = cursor++; return hooks[index] ??= { current: initial }; },
    useMemo(compute, deps) {
      const index = cursor++;
      if (!hooks[index] || !same(hooks[index].deps, deps)) hooks[index] = { value: compute(), deps };
      return hooks[index].value;
    },
    useCallback(callback, deps) {
      const index = cursor++;
      if (!hooks[index] || !same(hooks[index].deps, deps)) hooks[index] = { callback, deps };
      return hooks[index].callback;
    },
    useEffect(effect, deps) {
      const index = cursor++;
      if (!hooks[index] || !same(hooks[index].deps, deps)) {
        const previous = hooks[index];
        hooks[index] = { deps, cleanup: previous?.cleanup };
        effects.push(() => {
          hooks[index].cleanup?.();
          hooks[index].cleanup = effect();
        });
      }
    },
  };
  class LabError extends Error {
    constructor(message, status) { super(message); this.status = status; }
  }
  const job = { id: "active-job", project_id: "project", kind: "rhythm", status: "running", base_revision: 3 };
  const labRequest = async (route, options = {}) => {
    requests.push({ route, ...options });
    if (options.method === "DELETE") {
      if (deleteConflict) throw new LabError("Project changed; reload before deleting", 409);
      return { deleted: "project", ...(cleanupPending ? { cleanup_pending: true } : {}) };
    }
    if (route === "/projects/project" && options.method === "PUT") {
      const body = JSON.parse(options.body);
      if (saveGate) await saveGate.promise;
      saved = { ...saved, name: body.name, document: body.document, revision: body.base_revision + 1 };
      return saved;
    }
    if (route === "/projects/project") return saved;
    if (route === "/experiments/music-sketch/draft") return saved;
    if (route === "/tracks" && options.method === "POST") return { id: "new-track", duration: 45 };
    if (route === "/projects/project/jobs" && options.method === "POST") {
      const body = JSON.parse(options.body);
      return { id: "new-job", project_id: "project", kind: body.kind, status: "queued", base_revision: body.base_revision };
    }
    if (route === "/projects/project/jobs") return { jobs: active ? [job] : [] };
    if (route === "/jobs/new-job") return new Promise(() => {});
    if (route === "/jobs/active-job") return new Promise(() => {});
    throw new Error(`Unexpected request ${route}`);
  };
  const exported = {};
  vm.runInNewContext(compiled, {
    exports: exported,
    require: (name) => {
      if (name === "react") return react;
      if (name === "@/lib/lab") return { LabError, labRequest };
      if (name === "./editorDirection") return directionHelpers;
      throw new Error(`Unexpected import ${name}`);
    },
    Error, URLSearchParams, AbortController, FormData, setTimeout, clearTimeout,
    window: {
      location: { search: draft ? "" : "?project=project" },
      addEventListener() {}, removeEventListener() {},
      localStorage: { removeItem(key) { clearedKeys.push(key); if (storageError) throw new Error("Browser storage is unavailable"); }, setItem() {} },
    },
  });
  function render() {
    cursor = 0;
    scheduled = false;
    state = exported.useLabProject("music-sketch");
    effects.splice(0).forEach((effect) => effect());
  }
  const settle = async () => {
    for (let count = 0; count < 8; count++) await new Promise(setImmediate);
  };
  render();
  await settle();
  return {
    get state() { return state; }, requests, clearedKeys, settle,
    close() { hooks.forEach((hook) => hook.cleanup?.()); },
  };
}

test("save for exit persists the current edit and confirms it is clean", async (context) => {
  const harness = await projectHarness();
  context.after(() => harness.close());
  harness.state.change((document) => ({ ...document, brief: "My changes" }));
  await harness.settle();
  assert.equal(await harness.state.saveForExit(), true);
  await harness.settle();
  assert.equal(harness.state.dirty, false);
  assert.equal(harness.state.project.revision, 4);
  assert.equal(harness.state.document.brief, "My changes");
});

test("export mode reaches the durable job request after saving the current edit", async (context) => {
  const harness = await projectHarness();
  context.after(() => harness.close());
  harness.state.change((document) => ({ ...document, passage: { start: 0, end: 600 } }));
  await harness.state.startJob("render", { mode: "export" });
  await harness.settle();
  const queued = harness.requests.find((request) => request.method === "POST");
  assert.deepEqual(JSON.parse(queued.body), { kind: "render", base_revision: 4, mode: "export" });
  const saved = harness.requests.find((request) => request.method === "PUT");
  assert.equal(JSON.parse(saved.body).document.passage.end, 600);
});

test("apply settings and regenerate freezes the new pace without clearing the previous edit", async (context) => {
  const harness = await projectHarness();
  context.after(() => harness.close());
  const clips = [{ id: "keep-until-ready", film_id: "film", source_start: 12, source_end: 42 }];
  const timeline = { slots: [{ id: "old-slot", start: 0, end: 30, clip_id: clips[0].id }] };
  harness.state.change((document) => ({ ...document, clips, music_timeline: timeline }));
  await harness.state.save();
  await harness.settle();
  // This is deliberately the same event turn as the settings modal callback.
  harness.state.change((document) => ({ ...document, planner_settings: { pacing: "kinetic", lyric_treatment: "metaphorical" } }));
  await harness.state.startJob("generate", { generate: { mode: "regenerate" } });
  await harness.settle();
  const saves = harness.requests.filter((request) => request.method === "PUT");
  const saved = JSON.parse(saves.at(-1).body);
  assert.equal(saved.document.planner_settings.pacing, "kinetic");
  assert.deepEqual(saved.document.clips, clips);
  assert.deepEqual(saved.document.music_timeline, timeline);
  const queued = harness.requests.find((request) => request.method === "POST");
  assert.deepEqual(JSON.parse(queued.body), { kind: "generate", base_revision: 5, generate: { mode: "regenerate" } });
  assert.deepEqual(plain(harness.state.document.clips), clips);
  assert.deepEqual(plain(harness.state.document.music_timeline), timeline);
  assert.equal(harness.state.activeJob, true);
});

test("save for exit stays open when an edit arrives during the save", async (context) => {
  const saveGate = deferred();
  const harness = await projectHarness({ saveGate });
  context.after(() => harness.close());
  harness.state.change((document) => ({ ...document, brief: "Saving this" }));
  await harness.settle();
  const saving = harness.state.saveForExit();
  harness.state.change((document) => ({ ...document, brief: "Keep my newer change" }));
  await harness.settle();
  saveGate.resolve();
  assert.equal(await saving, false);
  await harness.settle();
  assert.equal(harness.state.dirty, true);
  assert.equal(harness.state.document.brief, "Keep my newer change");
  assert.match(harness.state.error, /New changes were made while saving/);
});

test("successful delete uses the saved revision and clears the project and local job reference", async (context) => {
  const harness = await projectHarness();
  context.after(() => harness.close());
  harness.state.change((document) => ({ ...document, brief: "Unsaved discarded on confirmed delete" }));
  await harness.settle();
  assert.deepEqual(plain(await harness.state.remove()), { deleted: "project" });
  await harness.settle();
  assert.deepEqual(harness.requests.filter((request) => request.method === "DELETE"), [
    { route: "/projects/project?base_revision=3", method: "DELETE" },
  ]);
  assert.equal(harness.requests.some((request) => request.method === "PUT"), false);
  assert.equal(harness.state.project, null);
  assert.equal(harness.state.document, null);
  assert.equal(harness.state.canUndo, false);
  assert.deepEqual(harness.clearedKeys, ["lab-job:project"]);
});

test("server deletion clears the workspace despite browser storage failure and preserves pending-cleanup receipt", async (context) => {
  const harness = await projectHarness({ storageError: true, cleanupPending: true });
  context.after(() => harness.close());
  harness.state.change((document) => ({ ...document, brief: "Confirmed for deletion" }));
  await harness.settle();
  assert.deepEqual(plain(await harness.state.remove()), { deleted: "project", cleanup_pending: true });
  await harness.settle();
  assert.equal(harness.state.project, null);
  assert.equal(harness.state.document, null);
  assert.equal(harness.state.name, "");
  assert.equal(harness.state.job, null);
  assert.equal(harness.state.render, null);
  assert.equal(harness.state.matchJob, null);
  assert.equal(harness.state.nextSceneJob, null);
  assert.equal(harness.state.canUndo, false);
  assert.equal(harness.state.error, "");
  assert.equal(harness.state.busy, false);
  assert.equal(harness.requests.filter((request) => request.method === "DELETE").length, 1);
});

test("a revision conflict keeps local edits and history available", async (context) => {
  const harness = await projectHarness({ deleteConflict: true });
  context.after(() => harness.close());
  harness.state.change((document) => ({ ...document, brief: "Keep this unsaved work" }));
  await harness.settle();
  const before = plain(harness.state.document);
  assert.equal(await harness.state.remove(), false);
  await harness.settle();
  assert.deepEqual(plain(harness.state.document), before);
  assert.equal(harness.state.canUndo, true);
  assert.equal(harness.state.conflict, true);
  assert.deepEqual(harness.clearedKeys, []);
});

test("an active timing job prevents a delete request", async (context) => {
  const harness = await projectHarness({ active: true });
  context.after(() => harness.close());
  assert.equal(harness.state.activeJob, true);
  assert.equal(await harness.state.remove(), false);
  assert.equal(harness.requests.some((request) => request.method === "DELETE"), false);
});

test("grouped changes share one Undo and consume the latest document before a render", async (context) => {
  const harness = await projectHarness();
  context.after(() => harness.close());
  for (const letter of ["a", "b", "c"])
    harness.state.change((document) => ({ ...document, brief: document.brief + letter }), "direction");
  assert.equal(harness.state.captureUndo().length, 1);
  await harness.settle();
  assert.equal(harness.state.document.brief, "Original briefabc");
  harness.state.undo();
  await harness.settle();
  assert.equal(harness.state.document.brief, "Original brief");
  assert.equal(harness.state.canUndo, false);
});

test("ending a change, switching fields and ordinary edits create separate Undo steps", async (context) => {
  const harness = await projectHarness();
  context.after(() => harness.close());
  const change = (brief, group) => harness.state.change((document) => ({ ...document, brief }), group);
  change("A", "direction"); change("AB", "direction");
  harness.state.endChange();
  change("ABC", "direction");
  change("range", "range-1");
  change("ordinary"); change("ordinary again");
  assert.equal(harness.state.captureUndo().length, 5);
  // Multiple same-turn Undo commands must not read the previous render's stack.
  harness.state.undo(); harness.state.undo(); harness.state.undo();
  await harness.settle();
  assert.equal(harness.state.document.brief, "ABC");
  change("new range", "range-1");
  harness.state.undo();
  await harness.settle();
  assert.equal(harness.state.document.brief, "ABC");
  harness.state.undo();
  await harness.settle();
  assert.equal(harness.state.document.brief, "AB");
});

test("no-op groups do not consume Undo and a different no-op ends the previous group", async (context) => {
  const harness = await projectHarness();
  context.after(() => harness.close());
  harness.state.change((document) => document, "direction");
  assert.equal(harness.state.captureUndo().length, 0);
  harness.state.change((document) => ({ ...document, brief: "A" }), "direction");
  harness.state.change((document) => document, "other-field");
  harness.state.change((document) => ({ ...document, brief: "AB" }), "direction");
  assert.equal(harness.state.captureUndo().length, 2);
  harness.state.undo();
  await harness.settle();
  assert.equal(harness.state.document.brief, "A");
});

test("Save and job submission freeze the current grouped input and end its Undo group", async (context) => {
  const harness = await projectHarness();
  context.after(() => harness.close());
  harness.state.change((document) => ({ ...document, brief: "A" }), "direction");
  harness.state.change((document) => ({ ...document, brief: "AB" }), "direction");
  await harness.state.save();
  harness.state.change((document) => ({ ...document, brief: "ABC" }), "direction");
  await harness.state.startJob("generate", { generate: { mode: "regenerate" } });
  await harness.settle();
  const saves = harness.requests.filter((request) => request.method === "PUT");
  assert.deepEqual(saves.map((request) => JSON.parse(request.body).document.brief), ["AB", "ABC"]);
  assert.equal(harness.state.captureUndo().length, 2);
  harness.state.undo();
  await harness.settle();
  assert.equal(harness.state.document.brief, "AB");
  assert.equal(harness.state.dirty, true);
});

test("reload clears an open group so the next edit can Undo to the accepted revision", async (context) => {
  const harness = await projectHarness();
  context.after(() => harness.close());
  const endChange = harness.state.endChange;
  harness.state.change((document) => ({ ...document, brief: "Discarded" }), "direction");
  await harness.state.reload();
  await harness.settle();
  assert.equal(harness.state.endChange, endChange, "effects can depend on endChange without reopening");
  assert.equal(harness.state.captureUndo().length, 0);
  harness.state.change((document) => ({ ...document, brief: "After reload" }), "direction");
  harness.state.undo();
  await harness.settle();
  assert.equal(harness.state.document.brief, "Original brief");
});

test("track replacement keeps overall direction but clears old-song ranges as one separate Undo", async (context) => {
  const direction = { instruction: "Cool blue city images", ranges: [{ id: "r", start: 2, end: 5, instruction: "Fast here" }] };
  const voices = [{ id: "voice", film_id: "film", start: 2, source_start: 40, source_end: 42, gain_db: 12 }];
  const harness = await projectHarness({ draft: true, initialDocument: { editor_direction: direction, dialogue_clips: voices, audio_fade_out_seconds: 60 } });
  context.after(() => harness.close());
  harness.state.change((document) => ({ ...document, editor_direction: { ...document.editor_direction, instruction: "Warm city images" } }), "direction");
  await harness.state.upload(new Blob(["audio"], { type: "audio/mpeg" }));
  await harness.settle();
  assert.deepEqual(plain(harness.state.document.editor_direction), { instruction: "Warm city images", ranges: [] });
  assert.equal(harness.state.document.track.id, "new-track");
  assert.deepEqual(plain(harness.state.document.dialogue_clips), []);
  assert.ok(harness.state.document.audio_fade_out_seconds <= harness.state.document.passage.end);
  assert.equal(harness.state.captureUndo().length, 2);
  harness.state.undo();
  await harness.settle();
  assert.deepEqual(plain(harness.state.document.editor_direction), { ...direction, instruction: "Warm city images" });
  assert.deepEqual(plain(harness.state.document.dialogue_clips), voices);
});

test("track replacement preserves displayed legacy user direction before resetting the old visual plan", async (context) => {
  const visual_plan = { source: "user", arc: "Start enclosed and open into daylight", motifs: "Mirrors and windows" };
  const harness = await projectHarness({ draft: true, initialDocument: { editor_direction: null, visual_plan } });
  context.after(() => harness.close());
  await harness.state.upload(new Blob(["audio"], { type: "audio/mpeg" }));
  await harness.settle();
  assert.deepEqual(plain(harness.state.document.editor_direction), {
    instruction: "Original brief\n\nStart enclosed and open into daylight\n\nMirrors and windows", ranges: [],
  });
  assert.equal(harness.state.document.visual_plan, null);
  harness.state.undo(); await harness.settle();
  assert.deepEqual(plain(harness.state.document.visual_plan), visual_plan);
  assert.equal(harness.state.document.editor_direction, null);
});

test("draft snapshots include synchronous grouped history and restoration ends its group", async (context) => {
  const harness = await projectHarness({ draft: true });
  context.after(() => harness.close());
  harness.state.change((document) => ({ ...document, brief: "Before picker" }), "direction");
  const snapshot = harness.state.captureDraft();
  assert.equal(snapshot.document.brief, "Before picker");
  assert.equal(snapshot.history.length, 1);
  harness.state.change((document) => ({ ...document, brief: "Picker edit" }), "direction");
  assert.equal(harness.state.restoreDraft(snapshot), true);
  harness.state.change((document) => ({ ...document, brief: "After picker" }), "direction");
  harness.state.undo();
  await harness.settle();
  assert.equal(harness.state.document.brief, "Before picker");
  harness.state.undo();
  await harness.settle();
  assert.equal(harness.state.document.brief, "Original brief");
});
