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
const plain = (value) => JSON.parse(JSON.stringify(value));
function deferred() {
  let resolve, reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
const template = () => ({
  schema_version: 1, track: null, passage: { start: 0, end: 30 }, brief: "",
  film_ids: [], analysis: null, rhythm: null, clips: [], aspect_ratio: "16:9", fps: 24,
});

// Execute the actual hook while controlling only React scheduling and HTTP.
// Gates deliberately leave requests in flight across later edits and navigation.
async function harness({ initialProject = null, createGate, uploadGate, loadGate, failFirstSave = false } = {}) {
  const hooks = [], effects = [], requests = [], urls = [], listeners = new Map();
  let cursor = 0, scheduled = false, closed = false, state, saved = initialProject, creations = 0;
  const same = (a, b) => a && b && a.length === b.length && a.every((value, index) => Object.is(value, b[index]));
  const schedule = () => {
    if (!scheduled && !closed) { scheduled = true; queueMicrotask(render); }
  };
  const react = {
    useState(initial) {
      const index = cursor++;
      hooks[index] ??= { value: typeof initial === "function" ? initial() : initial };
      return [hooks[index].value, (update) => {
        const next = typeof update === "function" ? update(hooks[index].value) : update;
        if (!Object.is(next, hooks[index].value)) { hooks[index].value = next; schedule(); }
      }];
    },
    useRef(initial) { return hooks[cursor++] ??= { current: initial }; },
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
        hooks[index] = { deps, effect, cleanup: previous?.cleanup };
        effects.push(() => { hooks[index].cleanup?.(); hooks[index].cleanup = effect(); });
      }
    },
  };
  class LabError extends Error { constructor(message, status) { super(message); this.status = status; } }
  const labRequest = async (route, options = {}) => {
    requests.push({ route, ...options });
    if (route === "/experiments/music-sketch/draft") return {
      id: "", revision: 0, name: "Untitled music edit", experiment_id: "music-sketch", document: template(), created_at: 0, updated_at: 0,
    };
    if (route === "/projects" && options.method === "POST") {
      creations++;
      if (failFirstSave && creations === 1) throw new LabError("Could not save project", 503);
      if (createGate) await createGate.promise;
      const body = JSON.parse(options.body);
      saved = { ...body, id: `saved-${creations}`, revision: 1, created_at: 1, updated_at: 1 };
      return saved;
    }
    if (route === "/tracks" && options.method === "POST") {
      if (uploadGate) await uploadGate.promise;
      return { id: "audio-sha", name: "Song.wav", duration: 80 };
    }
    if (route === `/projects/${initialProject?.id}` && !options.method) {
      if (loadGate) await loadGate.promise;
      return initialProject;
    }
    if (route === `/projects/${saved?.id}` && options.method === "PUT") {
      const body = JSON.parse(options.body);
      saved = { ...saved, name: body.name, document: body.document, revision: body.base_revision + 1 };
      return saved;
    }
    if (route === `/projects/${saved?.id}/jobs`) {
      if (options.method !== "POST") return { jobs: [] };
      const body = JSON.parse(options.body);
      return { id: "job", project_id: saved.id, kind: body.kind, status: "queued", base_revision: body.base_revision };
    }
    if (route === "/jobs/job") return new Promise(() => {});
    throw new Error(`Unexpected ${options.method ?? "GET"} ${route}`);
  };
  const exported = {};
  const location = { search: initialProject ? `?project=${initialProject.id}` : "", pathname: "/lab/music-sketch", hash: "" };
  vm.runInNewContext(compiled, {
    exports: exported, Error, URLSearchParams, URL, AbortController, setTimeout, clearTimeout,
    FormData, File, crypto,
    require(name) {
      if (name === "react") return react;
      if (name === "@/lib/lab") return { LabError, labRequest };
      if (name === "./editorDirection") {
        const exports = {};
        vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "editorDirection.ts"), "utf8"), {
          compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
        }).outputText, { exports });
        return exports;
      }
      throw new Error(`Unexpected import ${name}`);
    },
    window: {
      location,
      history: { replaceState(_state, _unused, url) { urls.push(url); location.search = new URL(url, "http://localhost/lab/music-sketch").search; } },
      addEventListener(type, listener) { listeners.set(type, listener); },
      removeEventListener(type, listener) { if (listeners.get(type) === listener) listeners.delete(type); },
      localStorage: { setItem() {}, removeItem() {} },
    },
  });
  function render() {
    if (closed) return;
    cursor = 0; scheduled = false;
    state = exported.useLabProject("music-sketch");
    effects.splice(0).forEach((effect) => effect());
  }
  const settle = async () => { for (let count = 0; count < 8; count++) await new Promise(setImmediate); };
  const close = () => { closed = true; hooks.forEach((hook) => hook.cleanup?.()); };
  render(); await settle();
  return { get state() { return state; }, requests, urls, listeners, settle, close,
    mutations() { return requests.filter((request) => request.method && request.method !== "GET"); },
    replayEffects() { hooks.filter((hook) => hook.effect).forEach((hook) => { hook.cleanup?.(); hook.cleanup = hook.effect(); }); },
  };
}
async function draft(context, options) {
  const ui = await harness(options);
  context.after(ui.close);
  await ui.state.create("Untitled music edit"); await ui.settle();
  return ui;
}

test("opening and leaving an untouched experiment never creates a saved project", async (context) => {
  const ui = await draft(context);
  assert.equal(ui.state.isDraft, true);
  assert.equal(ui.state.project.id, "");
  assert.equal(ui.state.project.revision, 0);
  assert.equal(ui.state.dirty, false);
  assert.deepEqual(ui.requests.map((request) => request.route), ["/experiments/music-sketch/draft"]);
  assert.deepEqual(plain(ui.state.document), template());
  assert.equal(await ui.state.save(), null);
  assert.equal(await ui.state.saveForExit(), true);
  assert.equal(ui.mutations().length, 0);
  assert.equal(ui.listeners.has("beforeunload"), false);
  assert.equal(ui.urls.some((url) => new URL(url, "http://localhost").searchParams.has("project")), false);
});

test("editing and undoing back to the opening draft leaves no saved project", async (context) => {
  const ui = await draft(context);
  ui.state.change((document) => ({ ...document, brief: "A new idea" })); await ui.settle();
  assert.equal(ui.state.dirty, true);
  assert.equal(ui.listeners.has("beforeunload"), true);
  ui.state.undo(); await ui.settle();
  assert.equal(ui.state.dirty, false);
  assert.equal(await ui.state.saveForExit(), true);
  assert.equal(ui.mutations().length, 0);
});

test("first save creates revision one with the edited document and name atomically", async (context) => {
  const ui = await draft(context);
  ui.state.change((document) => ({ ...document, brief: "Keep this edit" }));
  ui.state.setName("My first edit"); await ui.settle();
  const saved = await ui.state.save(); await ui.settle();
  assert.equal(saved.revision, 1);
  assert.equal(ui.state.isDraft, false);
  assert.equal(ui.state.dirty, false);
  assert.deepEqual(ui.mutations().map(({ route, method }) => ({ route, method })), [{ route: "/projects", method: "POST" }]);
  const body = JSON.parse(ui.mutations()[0].body);
  assert.equal(body.document.brief, "Keep this edit");
  assert.equal(body.name, "My first edit");
  assert.equal(body.experiment_id, "music-sketch");
  assert.match(ui.urls.at(-1), /project=saved-1/);
  assert.equal(await ui.state.saveForExit(), true);
  assert.equal(ui.mutations().length, 1, "clean saved projects need no new revision");
});

test("concurrent first saves share one create request and one durable identity", async (context) => {
  const createGate = deferred();
  const ui = await draft(context, { createGate });
  ui.state.change((document) => ({ ...document, brief: "Shared snapshot" })); await ui.settle();
  const first = ui.state.save(), second = ui.state.save();
  await ui.settle();
  assert.equal(ui.mutations().length, 1);
  createGate.resolve();
  const [a, b] = await Promise.all([first, second]); await ui.settle();
  assert.equal(a.id, b.id);
  assert.equal(ui.state.project.id, a.id);
  assert.equal(ui.mutations().length, 1);
});

test("first save preserves newer local edits and prevents exit until they are saved", async (context) => {
  const createGate = deferred();
  const ui = await draft(context, { createGate });
  ui.state.change((document) => ({ ...document, brief: "First version" })); await ui.settle();
  const exiting = ui.state.saveForExit();
  ui.state.change((document) => ({ ...document, brief: "Newer version" }));
  ui.state.setName("Newer name"); await ui.settle();
  createGate.resolve();
  assert.equal(await exiting, false); await ui.settle();
  assert.equal(ui.state.document.brief, "Newer version");
  assert.equal(ui.state.name, "Newer name");
  assert.equal(ui.state.project.document.brief, "First version");
  assert.equal(ui.state.dirty, true);
  assert.equal(await ui.state.saveForExit(), true); await ui.settle();
  assert.equal(ui.state.project.revision, 2);
  assert.deepEqual(ui.mutations().map((request) => request.method), ["POST", "PUT"]);
});

test("failed first save keeps the local draft and retry creates no placeholder project", async (context) => {
  const ui = await draft(context, { failFirstSave: true });
  ui.state.change((document) => ({ ...document, brief: "Do not lose this" })); await ui.settle();
  assert.equal(await ui.state.save(), null); await ui.settle();
  assert.equal(ui.state.isDraft, true);
  assert.equal(ui.state.project.id, "");
  assert.equal(ui.state.dirty, true);
  assert.equal(ui.state.document.brief, "Do not lose this");
  assert.match(ui.state.error, /Could not save/);
  const saved = await ui.state.save(); await ui.settle();
  assert.equal(saved.revision, 1);
  assert.equal(ui.state.dirty, false);
  assert.equal(ui.mutations().every((request) => request.route === "/projects" && request.method === "POST"), true);
});

test("uploading into a draft imports original audio without saving a project", async (context) => {
  const ui = await draft(context);
  await ui.state.upload(new File(["audio bytes"], "Song.wav")); await ui.settle();
  assert.equal(ui.state.isDraft, true);
  assert.equal(ui.state.dirty, true);
  assert.deepEqual(plain(ui.state.document.track), { id: "audio-sha", name: "Song.wav", duration: 80 });
  assert.deepEqual(ui.mutations().map(({ route, method }) => ({ route, method })), [{ route: "/tracks", method: "POST" }]);
  assert.equal(ui.mutations()[0].body.has("base_revision"), false);
});

test("cancelling a draft import restores the opening draft and its undo history", async (context) => {
  const ui = await draft(context);
  const baseline = ui.state.captureDraft();
  assert.ok(baseline);
  await ui.state.upload(new File(["audio bytes"], "Song.wav")); await ui.settle();
  ui.state.restoreDraft(baseline); await ui.settle();
  assert.equal(ui.state.document.track, null);
  assert.equal(ui.state.dirty, false);
  assert.equal(ui.state.canUndo, false);
  assert.equal(await ui.state.saveForExit(), true);
  assert.equal(ui.mutations().some((request) => request.route.startsWith("/projects")), false);
});

test("cancelling a draft reimport keeps edits made before opening the picker", async (context) => {
  const ui = await draft(context);
  ui.state.change((document) => ({ ...document, brief: "Keep my direction" }));
  ui.state.setName("Keep my name"); await ui.settle();
  const before = ui.state.captureDraft();
  await ui.state.upload(new File(["audio bytes"], "Song.wav")); await ui.settle();
  ui.state.restoreDraft(before); await ui.settle();
  assert.equal(ui.state.document.brief, "Keep my direction");
  assert.equal(ui.state.name, "Keep my name");
  assert.equal(ui.state.document.track, null);
  assert.equal(ui.state.dirty, true);
  assert.equal(ui.state.canUndo, true);
  ui.state.undo(); await ui.settle();
  assert.equal(ui.state.document.brief, "");
  assert.equal(ui.state.document.track, null);
});

test("jobs persist an edited draft before queuing its exact revision", async (context) => {
  const ui = await draft(context);
  await ui.state.upload(new File(["audio bytes"], "Song.wav")); await ui.settle();
  const job = await ui.state.startJob("rhythm"); await ui.settle();
  assert.equal(job.project_id, "saved-1");
  assert.equal(job.base_revision, 1);
  assert.deepEqual(ui.mutations().map((request) => request.route), ["/tracks", "/projects", "/projects/saved-1/jobs"]);
  assert.equal(JSON.parse(ui.mutations()[1].body).document.track.id, "audio-sha");
});

test("clean draft actions never send requests with an empty project ID", async (context) => {
  const ui = await draft(context);
  assert.equal(await ui.state.startJob("rhythm"), null);
  await ui.state.reload();
  assert.equal(await ui.state.restore(0), false);
  await ui.state.remove(); await ui.settle();
  assert.equal(ui.requests.some((request) => request.route.startsWith("/projects/")), false);
  assert.equal(ui.mutations().length, 0);
});

test("opening a project in the wrong experiment never accepts it or loads its jobs", async (context) => {
  const initialProject = { id: "wrong", experiment_id: "visual-rhymes", name: "Wrong lab", revision: 1, document: template() };
  const ui = await harness({ initialProject }); context.after(ui.close);
  assert.equal(ui.state.project, null);
  assert.match(ui.state.error, /different experiment/);
  assert.equal(ui.requests.length, 1);
});

test("a project response arriving after unmount cannot start follow-up job requests", async () => {
  const loadGate = deferred();
  const initialProject = { id: "saved", experiment_id: "music-sketch", name: "Existing", revision: 1, document: template() };
  const ui = await harness({ initialProject, loadGate });
  ui.close(); loadGate.resolve(); await ui.settle();
  assert.equal(ui.requests.length, 1);
});

test("StrictMode effect replay only loads jobs for the accepted saved-project response", async (context) => {
  const loadGate = deferred();
  const initialProject = { id: "saved", experiment_id: "music-sketch", name: "Existing", revision: 1, document: template() };
  const ui = await harness({ initialProject, loadGate }); context.after(ui.close);
  ui.replayEffects(); loadGate.resolve(); await ui.settle();
  assert.deepEqual(ui.requests.map((request) => request.route), ["/projects/saved", "/projects/saved", "/projects/saved/jobs"]);
  assert.equal(ui.mutations().length, 0);
  assert.equal(ui.state.project.id, "saved");
});

test("first save finishing after unmount cannot rewrite the next screen's URL", async (context) => {
  const createGate = deferred();
  const ui = await draft(context, { createGate });
  ui.state.change((document) => ({ ...document, brief: "Save before route changes" })); await ui.settle();
  const saving = ui.state.save();
  ui.close(); createGate.resolve(); await saving; await ui.settle();
  assert.equal(ui.urls.length, 0);
  assert.equal(ui.mutations().length, 1, "an already submitted edited snapshot may still finish durably");
});

test("an audio import finishing after another edit preserves that newer local edit", async (context) => {
  const uploadGate = deferred();
  const ui = await draft(context, { uploadGate });
  const uploading = ui.state.upload(new File(["audio bytes"], "Song.wav"));
  ui.state.change((document) => ({ ...document, brief: "I changed direction during import" })); await ui.settle();
  uploadGate.resolve(); await uploading; await ui.settle();
  assert.equal(ui.state.document.brief, "I changed direction during import");
  assert.equal(ui.state.document.track, null);
  assert.equal(ui.state.dirty, true);
  assert.match(ui.state.error, /workspace changed during import/i);
  assert.equal(ui.mutations().length, 1);
});

test("an old draft snapshot cannot roll back a project after its first save", async (context) => {
  const ui = await draft(context);
  const baseline = ui.state.captureDraft();
  ui.state.change((document) => ({ ...document, brief: "Saved version" })); await ui.settle();
  await ui.state.save(); await ui.settle();
  assert.equal(ui.state.captureDraft(), null);
  assert.equal(ui.state.restoreDraft(baseline), false); await ui.settle();
  assert.equal(ui.state.project.id, "saved-1");
  assert.equal(ui.state.document.brief, "Saved version");
  assert.equal(ui.state.dirty, false);
});

test("a job does not queue an older snapshot when the draft changes during its first save", async (context) => {
  const createGate = deferred();
  const ui = await draft(context, { createGate });
  ui.state.change((document) => ({ ...document, brief: "Original generation direction" })); await ui.settle();
  const starting = ui.state.startJob("generate", { generate: { mode: "fill" } });
  ui.state.change((document) => ({ ...document, brief: "Use my newer direction" })); await ui.settle();
  createGate.resolve();
  assert.equal(await starting, null); await ui.settle();
  assert.equal(ui.state.document.brief, "Use my newer direction");
  assert.equal(ui.state.project.document.brief, "Original generation direction");
  assert.equal(ui.state.dirty, true);
  assert.equal(ui.mutations().some((request) => request.route.endsWith("/jobs")), false);
  const retried = await ui.state.startJob("generate", { generate: { mode: "fill" } }); await ui.settle();
  assert.equal(retried.base_revision, 2);
  assert.equal(ui.state.project.document.brief, "Use my newer direction");
  assert.deepEqual(ui.mutations().map((request) => request.route), ["/projects", "/projects/saved-1", "/projects/saved-1/jobs"]);
});

test("a name edit and save in the same event create the requested name", async (context) => {
  const ui = await draft(context);
  ui.state.setName("Named without another render");
  const saved = await ui.state.save(); await ui.settle();
  assert.equal(saved.name, "Named without another render");
  assert.equal(JSON.parse(ui.mutations()[0].body).name, "Named without another render");
  assert.equal(ui.state.dirty, false);
});
