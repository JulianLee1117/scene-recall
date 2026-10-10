const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const source = ts.transpileModule(fs.readFileSync(path.join(__dirname, "useLabProject.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const copy = (value) => JSON.parse(JSON.stringify(value));
const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };

async function setup({ pendingApply = false } = {}) {
  const hooks = [];
  let cursor = 0, scheduled = false, output, effects = [], disposed = false;
  function schedule() {
    if (!scheduled && !disposed) { scheduled = true; queueMicrotask(render); }
  }
  const react = {
    useState(initial) {
      const i = cursor++;
      hooks[i] ??= { value: typeof initial === "function" ? initial() : initial };
      return [hooks[i].value, (update) => {
        const next = typeof update === "function" ? update(hooks[i].value) : update;
        if (!Object.is(next, hooks[i].value)) { hooks[i].value = next; schedule(); }
      }];
    },
    useRef(initial) { const i = cursor++; return hooks[i] ??= { current: initial }; },
    useMemo(compute, deps) {
      const i = cursor++;
      if (!hooks[i] || !same(deps, hooks[i].deps)) hooks[i] = { value: compute(), deps };
      return hooks[i].value;
    },
    useCallback(callback, deps) {
      const i = cursor++;
      if (!hooks[i] || !same(deps, hooks[i].deps)) hooks[i] = { callback, deps };
      return hooks[i].callback;
    },
    useEffect(effect, deps) {
      const i = cursor++;
      if (!hooks[i] || !same(deps, hooks[i].deps)) {
        const previous = hooks[i];
        hooks[i] = { deps, cleanup: previous?.cleanup };
        effects.push(() => { hooks[i].cleanup?.(); hooks[i].cleanup = effect(); });
      }
    },
  };
  const initial = { id: "p", name: "Edit", experiment_id: "music-sketch", revision: 4,
    document: { clips: [{ id: "anchor" }], brief: "A quiet opening", music_timeline: { slots: [{ id: "a" }, { id: "b" }] } } };
  let project = copy(initial);
  const candidate = { id: "one", cut: 5, incoming: { source_start: 20, source_end: 24 }, preview_ready: true };
  const parent = { id: "parent", project_id: "p", kind: "next-scene", status: "completed", base_revision: 4,
    result: { contract: "test", scope: { anchor_slot_id: "a", next_slot_id: "b" }, candidates: [candidate] } };
  const preview = { ...parent, id: "preview", kind: "next-scene-preview", result: { next_scene_job_id: "parent", candidate } };
  const requests = [];
  const applied = deferred();
  const labRequest = async (url, init = {}) => {
    const body = init.body ? JSON.parse(init.body) : undefined;
    requests.push({ url, method: init.method || "GET", body });
    if (url === "/projects/p" && init.method === "PUT") {
      project = { ...project, name: body.name, document: body.document, revision: project.revision + 1 };
      return project;
    }
    if (url === "/projects/p") return project;
    if (url === "/projects/p/jobs" && init.method === "POST") return { ...parent, id: "new-parent", status: "queued", base_revision: body.base_revision };
    if (url === "/projects/p/jobs") return { jobs: [parent] };
    if (url === "/jobs/parent") return parent;
    if (url === "/jobs/new-parent") return { ...parent, id: "new-parent", base_revision: project.revision };
    if (url === "/jobs/preview") return preview;
    if (url === "/jobs/parent/next-scenes/one/preview" && init.method === "POST") return { ...preview, status: "queued" };
    if (url === "/jobs/parent/apply-next-scene") {
      const next = { ...project, revision: project.revision + 1,
        document: { ...project.document, clips: [...project.document.clips, { id: "chosen" }] } };
      if (pendingApply) return applied.promise;
      project = next;
      return next;
    }
    throw new Error(`Unexpected request ${url}`);
  };
  const exports = {};
  vm.runInNewContext(source, {
    exports, URLSearchParams, AbortController, FormData, JSON, setTimeout, clearTimeout,
    window: { location: { search: "?project=p" }, localStorage: { setItem() {}, removeItem() {} }, addEventListener() {}, removeEventListener() {} },
    require(name) {
      if (name === "react") return react;
      if (name === "@/lib/lab") return { labRequest, LabError: class extends Error {} };
      if (name === "./editorDirection") {
        const exports = {};
        vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "editorDirection.ts"), "utf8"), {
          compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
        }).outputText, { exports });
        return exports;
      }
      throw new Error(name);
    },
  });
  function render() {
    if (disposed) return;
    scheduled = false; cursor = 0;
    output = exports.useLabProject("music-sketch");
    const run = effects; effects = []; run.forEach((effect) => effect());
  }
  async function flush() { for (let i = 0; i < 20; i++) await Promise.resolve(); }
  render(); await flush();
  return {
    get state() { return output; }, initial, parent, candidate, requests, flush, applied,
    dispose() { disposed = true; hooks.forEach((hook) => hook.cleanup?.()); },
  };
}

test("recovering next-scene results never accepts a new editorial document or consumes Undo", async () => {
  const app = await setup();
  try {
    assert.equal(app.state.nextSceneJob.id, "parent");
    assert.deepEqual(copy(app.state.document), app.initial.document);
    assert.equal(app.state.canUndo, false);
    assert.equal(app.requests.filter((request) => request.url === "/projects/p").length, 1);
  } finally { app.dispose(); }
});

test("Find next saves the local document and freezes the resulting revision with explicit options", async () => {
  const app = await setup();
  try {
    app.state.change((doc) => ({ ...doc, brief: "Open the frame" })); await app.flush();
    const options = { anchor_slot_id: "a", intent: "Less literal", flexible_cut: true, inspect_frames: false };
    await app.state.startJob("next-scene", { nextScene: options }); await app.flush();
    const create = app.requests.find((request) => request.url === "/projects/p/jobs" && request.method === "POST");
    assert.deepEqual(copy(create.body), { kind: "next-scene", base_revision: 5, next_scene: options });
    assert.equal(app.state.nextSceneJob.base_revision, 5);
    assert.equal(app.state.document.brief, "Open the frame");
  } finally { app.dispose(); }
});

test("adjusted preview retains its parent and sends exact timing and crop without applying the edit", async () => {
  const app = await setup();
  try {
    const crop = { x: 0.2, y: 0.1, width: 0.6, height: 0.8 };
    await app.state.prepareNextScene("one", { source_start: 21, cut_time: 5.5, crop }); await app.flush();
    const request = app.requests.find((item) => item.method === "POST");
    assert.equal(request.url, "/jobs/parent/next-scenes/one/preview");
    assert.deepEqual(copy(request.body), { source_start: 21, cut_time: 5.5, crop });
    assert.equal(app.state.nextSceneJob.id, "parent");
    assert.equal(app.state.job.kind, "next-scene-preview");
    assert.deepEqual(copy(app.state.document), app.initial.document);
    assert.equal(app.state.canUndo, false);
  } finally { app.dispose(); }
});

test("Apply sends its prepared proof and one Undo restores the exact previous document", async () => {
  const app = await setup();
  try {
    assert.equal(await app.state.applyNextScene("one", { source_start: 21, cut_time: 5.5, crop: null, preview_job_id: "preview" }), true);
    await app.flush();
    const request = app.requests.find((item) => item.url.endsWith("apply-next-scene"));
    assert.deepEqual(copy(request.body), { base_revision: 4, candidate_id: "one", source_start: 21, cut_time: 5.5, crop: null, preview_job_id: "preview" });
    assert.equal(app.state.project.revision, 5);
    assert.equal(app.state.document.clips.length, 2);
    app.state.undo(); await app.flush();
    assert.deepEqual(copy(app.state.document), app.initial.document);
    assert.equal(app.state.canUndo, false);
    assert.equal(app.state.dirty, true);
  } finally { app.dispose(); }
});

test("local changes invalidate preview and Apply before any mutation request", async () => {
  const app = await setup();
  try {
    app.state.change((doc) => ({ ...doc, brief: "A later instruction" })); await app.flush();
    assert.equal(await app.state.prepareNextScene("one", { source_start: 20, cut_time: 5 }), null);
    assert.equal(await app.state.applyNextScene("one", { source_start: 20, cut_time: 5 }), false);
    assert.equal(app.requests.filter((request) => request.method === "POST").length, 0);
  } finally { app.dispose(); }
});

test("Apply cannot overwrite a local edit made while the server request is in flight", async () => {
  const app = await setup({ pendingApply: true });
  try {
    const pending = app.state.applyNextScene("one", { source_start: 20, cut_time: 5 });
    await app.flush();
    app.state.change((doc) => ({ ...doc, brief: "Keep this newer local change" })); await app.flush();
    app.applied.resolve({ ...app.initial, revision: 5, document: { ...app.initial.document, clips: [{ id: "server-choice" }] } });
    assert.equal(await pending, false); await app.flush();
    assert.equal(app.state.document.brief, "Keep this newer local change");
    assert.deepEqual(copy(app.state.document.clips), app.initial.document.clips);
    assert.equal(app.state.conflict, true);
    assert.match(app.state.error, /newer local changes/);
  } finally { app.dispose(); }
});
