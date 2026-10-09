const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

function load(file, modules = {}, globals = {}) {
  const output = ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInNewContext(output, {
    exports, process: { env: {} }, Headers, FormData, AbortController, setTimeout, clearTimeout,
    require(name) {
      if (name in modules) return modules[name];
      if (name === "./model") return model;
      if (name === "./releaseMetadata") return load("releaseMetadata.ts");
      if (name === "@/components/DirectionIcon") return { default: "DirectionIcon" };
      if (name.endsWith(".css")) return { default: {} };
      if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
      throw new Error(`Unexpected module: ${name}`);
    }, ...globals,
  });
  return exports;
}
const model = load("model.ts");
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
const status = { downloader: { configured: true, available: true }, search: { configured: true, available: true }, monitor: { running: true } };
const emptyQueue = () => ({ status, items: [], busy: null, error: null, loading: false, clearMutationError() {}, refresh: async () => {}, execute: async () => true });

function harness(file, renderEntry, modules = {}, globals = {}) {
  const hooks = [];
  let cursor = 0, scheduled = false, output, disposed = false, effects = [];
  const schedule = () => { if (!scheduled && !disposed) { scheduled = true; queueMicrotask(render); } };
  const react = {
    useState(initial) {
      const i = cursor++; hooks[i] ??= { value: typeof initial === "function" ? initial() : initial };
      return [hooks[i].value, (update) => { const value = typeof update === "function" ? update(hooks[i].value) : update;
        if (!Object.is(value, hooks[i].value)) { hooks[i].value = value; schedule(); } }];
    },
    useRef(initial) { const i = cursor++; return hooks[i] ??= { current: initial }; },
    useCallback(callback, deps) { const i = cursor++; if (!hooks[i] || !same(deps, hooks[i].deps)) hooks[i] = { callback, deps }; return hooks[i].callback; },
    useMemo(factory, deps) { const i = cursor++; if (!hooks[i] || !same(deps, hooks[i].deps)) hooks[i] = { value: factory(), deps }; return hooks[i].value; },
    useEffect(effect, deps) {
      const i = cursor++;
      if (!hooks[i] || !same(deps, hooks[i].deps)) { const old = hooks[i]; hooks[i] = { deps, cleanup: old?.cleanup };
        effects.push(() => { hooks[i].cleanup?.(); hooks[i].cleanup = effect(); }); }
    },
  };
  const exports = load(file, { react, ...modules }, globals);
  function render() {
    if (disposed) return;
    scheduled = false; cursor = 0; output = renderEntry(exports);
    const run = effects; effects = []; run.forEach((effect) => effect());
  }
  const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
  render();
  return { get state() { return output; }, flush, render,
    find: (predicate) => nodes(output).find(predicate),
    button: (label) => nodes(output).find((node) => node.type === "button" && text(node) === label),
    input: (label) => nodes(nodes(output).find((node) => node.type === "label" && text(node).startsWith(label))).find((node) => node.type === "input"),
    dispose() { disposed = true; hooks.forEach((hook) => hook.cleanup?.()); },
  };
}

function libraryHarness({ queue = emptyQueue(), films = [], incoming = [], jobs = [], fetch: fetchOverride, onReconcile = () => {}, globals = {} } = {}) {
  return harness("../../components/LibraryView.tsx", (exports) => exports.default(), {
    "@/features/acquisition/AcquisitionPanel": { default: "AcquisitionPanel" },
    "@/features/acquisition/AddFilmForm": { default: "AddFilmForm" },
    "@/features/acquisition/useAcquisitionQueue": { useAcquisitionQueue: (reconcile) => { onReconcile(reconcile); return queue; } },
    "@/features/acquisition/model": model,
    "./LibraryStorage": { default: "LibraryStorage" },
  }, {
    fetch: fetchOverride ?? (async (url) => {
      assert.ok(["/incoming", "/library", "/ingest/jobs"].includes(url));
      return { ok: true, json: async () => url === "/incoming" ? incoming : url === "/library" ? films : jobs };
    }),
    window: { requestAnimationFrame: (callback) => callback(), setTimeout: () => 1, clearTimeout() {} },
    ...globals,
  });
}

test("film metadata requires an explicit valid title/year and preserves optional edition", () => {
  assert.throws(() => model.metadataFromFields("", "1975", ""), /title/);
  for (const value of ["75", "1975.0", "1975extra", "1887", "2101"]) assert.throws(() => model.metadataFromFields("Film", value, ""), /year/);
  assert.deepEqual(JSON.parse(JSON.stringify(model.metadataFromFields("  Film  ", "1975", "  Restored  "))), { title: "Film", year: 1975, edition: "Restored" });
});

test("cancellation can abandon failed downloads but cannot be sent twice", () => {
  for (const status of ["queued", "downloading", "validating", "needs_review", "importing", "ingest_queued", "ingesting", "cleanup", "failed"]) {
    assert.equal(model.canCancel({ status }), true, status);
    assert.equal(model.canCancel({ status, cancel_requested: true }), false, status);
    assert.equal(model.canCancel({ status, cancellation_cleanup: "pending" }), false, status);
  }
  for (const status of ["ready", "failed", "cancelled"]) assert.equal(model.isActive({ status }), false);
  for (const status of ["ready", "cancelling", "cancelled"]) assert.equal(model.canCancel({ status }), false);
  assert.equal(model.isActive({ status: "cancelling", error: "File is locked" }), true);
  assert.equal(model.isActive({ status: "failed", cancel_requested: true }), true);
});

test("cancellation hints promise cleanup only after its completion receipt", () => {
  const item = { status: "cancelling", cancellation_cleanup: "pending", film_path: "V:/films/Film.mkv" };
  assert.match(model.acquisitionHint(item), /cleaning up downloaded files.*imported film is kept/);
  assert.match(model.acquisitionHint({ ...item, error: "File locked" }), /will retry automatically/);
  assert.doesNotMatch(model.cancellationNotice(item), /were cleaned up/);
  assert.doesNotMatch(model.cancellationNotice({ ...item, status: "cancelled", cancellation_cleanup: null }), /were cleaned up/);
  assert.match(model.cancellationNotice({ ...item, status: "cancelled", cancellation_cleanup: "complete" }), /were cleaned up.*imported film is kept/);
});

test("unknown byte counts and downloader sentinel ETA never become misleading progress", () => {
  assert.equal(model.formatBytes(null), "Unknown size"); assert.equal(model.formatBytes(0), "0 B");
  assert.equal(model.formatBytes(1024 ** 3), "1.0 GB"); assert.equal(model.formatEta(8_640_000), null);
  assert.equal(model.formatEta(-1), null);
});

test("an imported managed film has one queue row and cannot expose a competing library retry", () => {
  const film = { path: "V:/films/Challengers (2024).mkv", status: "not_indexed" };
  const job = { job_id: "ingest", path: "v:\\films\\Challengers (2024).mkv", status: "queued", queue_position: 7 };
  const item = { id: "download", film_path: film.path, ingest_job_id: "ingest", status: "ingest_queued" };
  assert.equal(model.belongsInLibrary(film, [job], [item]), false);
  assert.equal(model.independentJobs([job], [item]).length, 0);
  assert.equal(model.waitingLabel(job), "Waiting · #7");
  assert.equal(model.belongsInLibrary(film, [{ ...job, status: "error" }], [{ ...item, status: "failed" }]), false);
  assert.equal(model.belongsInLibrary(film, [{ ...job, status: "done" }], [{ ...item, status: "ready" }]), true);
  assert.equal(model.belongsInLibrary(film, [{ ...job, status: "error" }], [{ ...item, status: "cancelling" }]), false);
  assert.equal(model.belongsInLibrary(film, [{ ...job, status: "error" }], [{ ...item, status: "cancelled" }]), true);
  const reingest = { ...job, job_id: "later-job" };
  assert.equal(model.independentJobs([reingest], [{ ...item, status: "ready" }])[0].job_id, "later-job");
  assert.equal(model.independentJobs([reingest], [{ ...item, status: "cancelled" }])[0].job_id, "later-job");
});

test("unmanaged jobs keep their real queue positions and progress describes a stage only", () => {
  const jobs = [{ job_id: "first", path: "first.mkv", status: "running" }, { job_id: "next", path: "next.mkv", status: "queued", queue_position: 1 }];
  assert.equal(model.independentJobs(jobs, []).length, 2);
  assert.equal(model.waitingLabel(jobs[1]), "Next in queue");
  assert.equal(model.preparationProgress("[media] 200/1266"), "Creating previews · 200 of 1,266");
  assert.equal(model.preparationProgress("[unknown] raw internal detail"), "Preparing scenes for search");
  // Model warnings are skipped in favor of the newest stage line.
  const warning = "  cells.append(patch.reshape(batch, -1, 2).median(dim=1).values)";
  assert.equal(model.preparationProgress(warning, ["[understanding] Elf (2003) part 7/11: 156/156 shots", "[2026-10-08 00:35:52] [INFO] rf-detr - File", warning]),
    "Searchable · Understanding the story · part 7 of 11");
  assert.equal(model.preparationProgress(warning, ["[measure] starting", warning]), "Searchable · Measuring camera, subjects and color");
  assert.equal(model.preparationProgress("[transformers] `torch_dtype` is deprecated", ["[annotate] 900/1393", "[transformers] `torch_dtype` is deprecated"]),
    "Describing scenes · 900 of 1,393", "tagged library warnings are not stages");
});

test("multipart upload lets the browser supply its boundary and server validation explains failures", async () => {
  const calls = [];
  const api = load("api.ts", {}, { fetch: async (url, init) => { calls.push({ url, init }); return { ok: true, json: async () => ({ item: {} }) }; } });
  const form = new FormData(); form.set("title", "Film");
  await api.acquisitionRequest("/torrent", { method: "POST", body: form });
  assert.equal(calls[0].init.headers.has("Content-Type"), false);
  assert.equal(calls[0].init.body, form);
  const failing = load("api.ts", {}, { fetch: async () => ({ ok: false, status: 409, json: async () => ({ detail: "Queue item changed. Refresh and review again." }) }) });
  await assert.rejects(failing.acquisitionRequest("/item/review"), /Queue item changed/);
});

test("editing a release search aborts the old response and clears the old selection", async () => {
  const result = deferred(); let signal;
  const app = harness("AddFilmForm.tsx", (exports) => exports.default({ status, busy: false, onQueue: async () => true }), {
    "./api": { acquisitionRequest: (_path, init) => { signal = init.signal; return result.promise; }, messageOf: (e) => e.message },
  });
  try {
    app.button("Search releases").props.onClick(); await app.flush();
    app.input("Search movie releases").props.onChange({ target: { value: "Old film" } }); await app.flush();
    app.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} }); await app.flush();
    app.input("Search movie releases").props.onChange({ target: { value: "New film" } }); await app.flush();
    assert.equal(signal.aborted, true);
    result.resolve({ results: [{ id: "old", title: "Old release" }] }); await app.flush();
    assert.equal(text(app.state).includes("Old release"), false); assert.equal(app.button("Queue film"), undefined);
  } finally { app.dispose(); }
});

test("subtitle review defaults to automatic validation and still allows an explicit track", async () => {
  const calls = [];
  const item = { title: "Film", year: 1975, revision: 7, review: { selected_video: "film.mkv", videos: [{ relative_path: "film.mkv", name: "film.mkv", size: 1000 }], subtitles: [{ relative_path: "english.srt", excerpt: "Hello", validation: "Timing needs another check" }] } };
  const app = harness("AcquisitionReview.tsx", (exports) => exports.default({ item, busy: false, error: null, onClose() {}, onSubmit: async (body) => { calls.push(body); return true; } }));
  try {
    assert.equal(app.input("Automatic").props.checked, true);
    assert.match(text(app.state), /timestamps and dialogue coverage/);
    assert.match(text(app.state), /embedded subtitles or transcribe/);
    assert.match(text(app.state), /Timing needs another check/);
    app.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} }); await app.flush();
    assert.deepEqual(JSON.parse(JSON.stringify(calls)), [{ revision: 7, video_path: "film.mkv", subtitle_decision: { action: "auto" } }]);
    app.find((node) => node.type === "input" && node.props.value === "english.srt").props.onChange(); await app.flush();
    app.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} }); await app.flush();
    assert.deepEqual(JSON.parse(JSON.stringify(calls[1])), { revision: 7, video_path: "film.mkv", subtitle_decision: { action: "use", relative_path: "english.srt" } });
    app.input("None of these").props.onChange(); await app.flush();
    app.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} }); await app.flush();
    assert.deepEqual(JSON.parse(JSON.stringify(calls[2])), { revision: 7, video_path: "film.mkv", subtitle_decision: { action: "skip" } });
  } finally { app.dispose(); }
});

test("the first main-video choice explains that subtitle review follows without skipping it", async () => {
  const calls = [];
  const item = { title: "Film", year: 1975, revision: 2, review: { selected_video: null,
    videos: [{ relative_path: "first.mkv", name: "First" }, { relative_path: "second.mkv", name: "Second" }], subtitles: [] } };
  const app = harness("AcquisitionReview.tsx", (exports) => exports.default({ item, busy: false, error: null, onClose() {}, onSubmit: async (body) => { calls.push(body); return true; } }));
  try {
    app.find((node) => node.type === "input" && node.props.value === "second.mkv").props.onChange(); await app.flush();
    assert.ok(app.button("Check subtitles"));
    assert.equal(app.button("Add film"), undefined);
    app.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} }); await app.flush();
    assert.deepEqual(JSON.parse(JSON.stringify(calls)), [{ revision: 2, video_path: "second.mkv", subtitle_decision: { action: "auto" } }]);
  } finally { app.dispose(); }
});

test("changing the reviewed video requests its own subtitle check without skipping or reusing the old track", async () => {
  const calls = [];
  const item = { title: "Film", year: 1975, revision: 4, review: { selected_video: "first.mkv",
    videos: [{ relative_path: "first.mkv", name: "First" }, { relative_path: "second.mkv", name: "Second" }],
    subtitles: [{ relative_path: "first.srt", excerpt: "Only for first" }] } };
  const app = harness("AcquisitionReview.tsx", (exports) => exports.default({ item, busy: false, error: null, onClose() {}, onSubmit: async (body) => { calls.push(body); return true; } }));
  try {
    app.find((node) => node.type === "input" && node.props.value === "first.srt").props.onChange(); await app.flush();
    app.find((node) => node.type === "input" && node.props.value === "second.mkv").props.onChange(); await app.flush();
    assert.equal(text(app.state).includes("Only for first"), false);
    app.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} }); await app.flush();
    assert.deepEqual(JSON.parse(JSON.stringify(calls)), [{ revision: 4, video_path: "second.mkv", subtitle_decision: { action: "auto" } }]);
  } finally { app.dispose(); }
});

for (const scenario of [
  { name: "automatic with a candidate", candidates: true, expected: { action: "auto" } },
  { name: "automatic without candidates", candidates: false, expected: { action: "auto" } },
  { name: "an explicit English track", candidates: true, choice: "film.srt", expected: { action: "use_as_english", relative_path: "film.srt" } },
  { name: "explicit skipping", candidates: true, choice: "skip", expected: { action: "skip" } },
]) {
  test(`manual import submits ${scenario.name} after source confirmation`, async () => {
    const calls = [];
    const candidate = {
      relative_path: "film.mkv", filename: "film.mkv", suggested_filename: "Film (1985).mkv",
      suggested_title: "Film", suggested_year: 1985, suggested_edition: null,
      size_gb: 1, extra_video_count: 0,
      subtitle_review_candidates: scenario.candidates
        ? [{ relative_path: "film.srt", filename: "film.srt", excerpt: "Hello", validation: "Check dialogue coverage" }]
        : [],
    };
    const app = harness("../../components/LibraryView.tsx", (exports) => exports.default(), {
      "@/features/acquisition/AcquisitionPanel": { default: "AcquisitionPanel" },
      "@/features/acquisition/AddFilmForm": { default: "AddFilmForm" },
      "@/features/acquisition/useAcquisitionQueue": { useAcquisitionQueue: emptyQueue },
      "@/features/acquisition/model": model,
      "./LibraryStorage": { default: "LibraryStorage" },
    }, {
      fetch: async (url, init) => {
        if (init?.method === "POST") {
          assert.equal(url, "/films/import");
          calls.push(JSON.parse(init.body));
          return { ok: true, json: async () => ({ path: "V:/films/Film (1985).mkv", filename: "Film (1985).mkv", subtitle_filename: null, job: null }) };
        }
        assert.ok(["/incoming", "/library", "/ingest/jobs"].includes(url));
        return { ok: true, json: async () => url === "/incoming" ? [candidate] : [] };
      },
      window: { requestAnimationFrame: (callback) => callback() },
    });
    try {
      await app.flush();
      app.button("Add films").props.onClick(); await app.flush();
      const downloaded = app.find((node) => node.type === "AddFilmForm").props.downloadedFiles;
      nodes(downloaded).find((node) => node.type === "button" && text(node) === "Review & add").props.onClick(); await app.flush();
      if (scenario.candidates) {
        assert.equal(app.input("Automatic").props.checked, true);
        assert.match(text(app.state), /Check dialogue coverage/);
        if (scenario.choice) {
          app.find((node) => node.type === "input" && node.props.name === "subtitle-decision" && node.props.value === scenario.choice).props.onChange();
          await app.flush();
        }
      }
      app.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} }); await app.flush();
      assert.equal(calls.length, 0, "automatic subtitles do not waive source-move confirmation");
      assert.ok(app.find((node) => node.type === "AddFilmForm"), "validation keeps the source form available");
      app.input("Torrenting and seeding are finished.").props.onChange({ target: { checked: true } }); await app.flush();
      app.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} }); await app.flush();
      assert.equal(calls.length, 1);
      assert.deepEqual(calls[0].subtitle_decision, scenario.expected);
      assert.equal(calls[0].confirm_finished, true);
      assert.equal(calls[0].ingest, true);
      assert.equal(app.find((node) => node.type === "AddFilmForm"), undefined);
      assert.equal(app.find((node) => node.props?.id === "films-queue-view").props["aria-pressed"], true);
    } finally { app.dispose(); }
  });
}

test("storage refresh waits for the catalog and only invalidates on changed data or explicit Refresh", async () => {
  let films = [{ path: "V:/films/Film.mkv", filename: "Film.mkv", title: "Film", size_gb: 2, status: "indexed", film_id: "film", duration: 90 }];
  let reconcile;
  const requests = [];
  const app = harness("../../components/LibraryView.tsx", (exports) => exports.default(), {
    "@/features/acquisition/AcquisitionPanel": { default: "AcquisitionPanel" },
    "@/features/acquisition/AddFilmForm": { default: "AddFilmForm" },
    "@/features/acquisition/useAcquisitionQueue": { useAcquisitionQueue: (onLibraryChange) => { reconcile = onLibraryChange; return emptyQueue(); } },
    "@/features/acquisition/model": model,
    "./LibraryStorage": { default: "LibraryStorage" },
  }, {
    fetch: async (url) => {
      requests.push(url);
      assert.ok(["/incoming", "/library", "/ingest/jobs"].includes(url));
      return { ok: true, json: async () => JSON.parse(JSON.stringify(url === "/library" ? films : [])) };
    },
  });
  const storage = () => app.find((node) => node.type === "LibraryStorage");
  try {
    assert.equal(storage(), undefined, "storage mounts after the initial catalog completes");
    await app.flush();
    const initialKey = storage().props.refreshKey;
    assert.equal(initialKey, 0, "the first storage read may reuse the backend cache");

    await reconcile(); await app.flush();
    assert.equal(storage().props.refreshKey, initialKey, "late acquisition reconciliation does not force an identical catalog scan");

    films = [...films, { ...films[0], path: "V:/films/New film.mkv", filename: "New film.mkv", title: "New film", film_id: "new-film" }];
    await reconcile(); await app.flush();
    assert.equal(storage().props.refreshKey, initialKey + 1, "a changed library invalidates its storage measurement");
    assert.match(text(app.state), /New film/);

    app.find((node) => node.type === "button" && node.props["aria-pressed"] !== undefined && text(node).startsWith("Queue")).props.onClick(); await app.flush();
    app.button("Refresh").props.onClick(); await app.flush();
    assert.equal(storage().props.refreshKey, initialKey + 2, "an explicit Refresh measures storage even when catalog data is unchanged");
    assert.equal(requests.length, 12);
  } finally { app.dispose(); }
});

test("the film queue hides completed history while preserving active work, failures and cancellations", async () => {
  const calls = [];
  const queue = {
    status, busy: null, error: null, loading: false,
    items: [
      { id: "ready", title: "Completed film", year: 1968, status: "ready" },
      { id: "cancelled", title: "Stopped film", year: 1972, status: "cancelled", revision: 3 },
      { id: "active", title: "Downloading film", year: 1980, status: "downloading" },
      { id: "failed", title: "Failed film", year: 1985, status: "failed", revision: 7, error: "Connection lost" },
    ],
    execute: async (...args) => { calls.push(args); return true; },
  };
  const app = harness("AcquisitionPanel.tsx", (exports) => exports.default({ queue }), {
    "./AcquisitionReview": { default: "AcquisitionReview" },
  });
  try {
    assert.doesNotMatch(text(app.state), /History|Completed film/);
    assert.match(text(app.state), /Stopped film/);
    assert.match(text(app.state), /Downloading film/);
    assert.match(text(app.state), /Failed film/);
    assert.match(text(app.state), /Connection lost/);
    assert.equal(queue.items.length, 4);
    // A cancelled acquisition keeps its info_hash reserved forever, so its
    // only way back is retrying this same record, not re-adding it.
    const tryAgainButtons = nodes(app.state).filter((node) => node.type === "button" && text(node) === "Try again");
    assert.equal(tryAgainButtons.length, 2);
    for (const title of ["Stopped film", "Failed film"]) {
      const row = app.find((node) => node.type === "article" && text(node).includes(title));
      nodes(row).find((node) => node.type === "button" && text(node) === "Try again").props.onClick(); await app.flush();
    }
    assert.deepEqual(JSON.parse(JSON.stringify(calls)), [
      ["cancelled", "/cancelled/retry", { revision: 3 }],
      ["failed", "/failed/retry", { revision: 7 }],
    ]);
    queue.items = queue.items.slice(0, 1); app.render();
    assert.match(text(app.state), /No films in progress/);
    assert.doesNotMatch(text(app.state), /History|Completed film/);
  } finally { app.dispose(); }
});

test("collapsed queue rows keep stalled downloads, errors and recovery actions visible", () => {
  // Native details only exposes its summary until opened.
  function collapsedContent(node) {
    if (node == null || typeof node !== "object") return node;
    if (Array.isArray(node)) return node.map(collapsedContent);
    const children = node.type === "details" && !node.props.open
      ? nodes(node.props.children).find((child) => child.type === "summary") : node.props.children;
    return { ...node, props: { ...node.props, children: collapsedContent(children) } };
  }
  const queue = {
    status, busy: null, error: null, loading: false,
    items: [
      { id: "paused", title: "Paused film", year: 1980, status: "downloading", progress: 0.25, message: "Download stopped in qBittorrent" },
      { id: "waiting", title: "Waiting film", year: 1981, status: "downloading", message: "Waiting for peers" },
      { id: "failed", title: "Failed film", year: 1982, status: "failed", error: "Connection lost" },
      { id: "review", title: "Review film", year: 1983, status: "needs_review", review: {} },
    ],
  };
  const app = harness("AcquisitionPanel.tsx", (exports) => exports.default({ queue }), {
    "./AcquisitionReview": { default: "AcquisitionReview" },
  });
  try {
    const collapsed = collapsedContent(app.state);
    assert.match(text(collapsed), /Downloading · 25%/);
    assert.match(text(collapsed), /Download paused\. Resume it in qBittorrent/);
    assert.match(text(collapsed), /Waiting for people sharing this download/);
    assert.match(text(collapsed), /Connection lost/);
    const buttons = nodes(collapsed).filter((node) => node.type === "button").map(text);
    for (const label of ["Review", "Try again", "Dismiss", "Cancel"]) assert.ok(buttons.includes(label), label);
  } finally { app.dispose(); }
});

test("the queue surfaces attention first and orders managed and manual waiting films together", () => {
  const jobs = [
    { job_id: "later", path: "later.mkv", filename: "Later.mkv", status: "queued", queue_position: 2 },
    { job_id: "next", path: "next.mkv", filename: "Next.mkv", status: "queued", queue_position: 1 },
    { job_id: "running", path: "running.mkv", filename: "Running.mkv", status: "running" },
  ];
  const queue = { ...emptyQueue(), items: [
    { id: "later", title: "Later", year: 1980, status: "ingest_queued", ingest_job_id: "later" },
    { id: "cancelled", title: "Cancelled", year: 1981, status: "cancelled" },
    { id: "failed", title: "Failed", year: 1982, status: "failed" },
  ] };
  const app = harness("AcquisitionPanel.tsx", (exports) => exports.default({ queue, jobs }), {
    "./AcquisitionReview": { default: "AcquisitionReview" },
  });
  try {
    const headings = nodes(app.state).filter((node) => node.type === "article").map((row) => text(nodes(row).find((node) => node.type === "h3")));
    assert.deepEqual(headings.map((heading) => heading.match(/^(Failed|Running|Next|Later|Cancelled)/)?.[0]), ["Failed", "Running", "Next", "Later", "Cancelled"]);
    assert.match(headings[2], /Next in queue/);
    assert.match(headings[3], /Waiting · #2/);
  } finally { app.dispose(); }
});

test("Dismiss forgets a cancelled or failed row, freeing its release for a fresh download", async () => {
  const calls = [];
  const queue = {
    status, busy: null, error: null, loading: false,
    items: [
      { id: "cancelled", title: "Stopped film", year: 1972, status: "cancelled", revision: 3 },
      { id: "failed", title: "Failed film", year: 1985, status: "failed", revision: 7 },
    ],
    execute: async (...args) => { calls.push(args); return true; },
  };
  const app = harness("AcquisitionPanel.tsx", (exports) => exports.default({ queue }), {
    "./AcquisitionReview": { default: "AcquisitionReview" },
  });
  try {
    const dismissButtons = nodes(app.state).filter((node) => node.type === "button" && text(node) === "Dismiss");
    assert.equal(dismissButtons.length, 2);
    for (const title of ["Stopped film", "Failed film"]) {
      const row = app.find((node) => node.type === "article" && text(node).includes(title));
      nodes(row).find((node) => node.type === "button" && text(node) === "Dismiss").props.onClick(); await app.flush();
    }
    assert.deepEqual(JSON.parse(JSON.stringify(calls)), [
      ["cancelled", "/cancelled/dismiss", { revision: 3 }],
      ["failed", "/failed/dismiss", { revision: 7 }],
    ]);
  } finally { app.dispose(); }
});

test("cancel accepts failed acquisitions, reports pending cleanup, and keeps the row visible for a later retry", async () => {
  const calls = [];
  const queue = {
    status, busy: null, error: null, loading: false,
    items: [{ id: "film", revision: 7, title: "Retained film", year: 1980, status: "failed", film_path: "V:/films/Film.mkv", error: "Bad download" }],
    execute: async (...args) => {
      calls.push(args);
      queue.items = [{ ...queue.items[0], status: "cancelling", cancellation_cleanup: "pending", cancel_requested: true, error: null }];
      return true;
    },
  };
  const app = harness("AcquisitionPanel.tsx", (exports) => exports.default({ queue }), {
    "./AcquisitionReview": { default: "AcquisitionReview" },
  });
  try {
    const cancel = app.button("Cancel");
    assert.ok(cancel);
    assert.match(cancel.props.title, /Imported films and library assets are kept/);
    assert.ok(app.button("Try again"));
    cancel.props.onClick(); await app.flush();
    assert.deepEqual(JSON.parse(JSON.stringify(calls)), [["film", "/film/cancel", { revision: 7 }]]);
    assert.match(text(app.state), /Cancellation requested/);
    assert.match(text(app.state), /Cancelling…/);
    assert.match(text(app.state), /Your imported film is kept/);
    assert.doesNotMatch(text(app.state), /were cleaned up/);
    assert.equal(app.button("Try again"), undefined);
    assert.equal(app.button("Cancel"), undefined);
    queue.items = [{ ...queue.items[0], error: "Downloaded file is locked" }]; app.render();
    assert.match(text(app.state), /Downloaded file is locked/);
    assert.match(text(app.state), /will retry automatically/);
    assert.match(text(app.state), /Retained film/);
    queue.items = [{ ...queue.items[0], status: "cancelled", cancellation_cleanup: "complete", error: null }]; app.render();
    // A cancelled acquisition permanently reserves its info_hash, so the row
    // stays visible with a way to retry rather than disappearing like a
    // completed one; re-adding the same release would otherwise be a dead end.
    assert.match(text(app.state), /Retained film/);
    assert.doesNotMatch(text(app.state), /Cancelling…/);
    assert.ok(app.button("Try again"));
    assert.match(text(app.state), /Cancelled\. Downloaded files were cleaned up/);
    assert.match(text(app.state), /Your imported film is kept/);
  } finally { app.dispose(); }
});

test("a rejected cancellation does not announce acceptance or cleanup", async () => {
  let showError;
  const queue = {
    status, busy: null, error: null, loading: false,
    items: [{ id: "film", revision: 1, title: "Film", year: 1980, status: "downloading" }],
    execute: async () => { queue.error = "Queue item changed. Refresh and try again."; return false; },
  };
  const app = harness("AcquisitionPanel.tsx", (exports) => exports.default({ queue, showError }), {
    "./AcquisitionReview": { default: "AcquisitionReview" },
  });
  try {
    app.button("Cancel").props.onClick(); await app.flush(); app.render();
    assert.match(text(app.state), /Queue item changed/);
    assert.doesNotMatch(text(app.state), /Cancellation requested|were cleaned up/);
    showError = false; app.render();
    assert.doesNotMatch(text(app.state), /Queue item changed/, "an open Add films form owns the shared error");
    showError = true; app.render();
    assert.match(text(app.state), /Queue item changed/, "hiding the duplicate error does not clear it");
  } finally { app.dispose(); }
});

test("closing review restores keyboard focus without scrolling and setup distinguishes configured search", async () => {
  const focus = [];
  const trigger = { isConnected: true, focus: (options) => focus.push(options) };
  const queue = { status, items: [{ id: "review", revision: 1, title: "Film", year: 1968, status: "needs_review", review: {} }], busy: null, error: null, loading: false };
  const app = harness("AcquisitionPanel.tsx", (exports) => exports.default({ queue }), {
    "./AcquisitionReview": { default: "AcquisitionReview" },
  }, { window: { requestAnimationFrame: (callback) => callback() } });
  try {
    app.button("Review").props.onClick({ currentTarget: trigger }); await app.flush();
    app.find((node) => node.type === "AcquisitionReview").props.onClose(); await app.flush();
    assert.equal(focus.length, 1); assert.equal(focus[0].preventScroll, true);
    assert.match(text(app.state), /Search: configured/);
    assert.equal(app.find((node) => node.type === "AcquisitionReview"), undefined);
  } finally { app.dispose(); }
});

test("explicit Refresh updates both downloads and existing ingestion jobs and waits for both", async () => {
  const download = deferred(), library = deferred();
  const calls = []; let initial = true;
  const queue = { ...emptyQueue(), refresh: () => { calls.push("download"); return download.promise; } };
  const app = libraryHarness({ queue, fetch: async (url) => {
    if (!initial) {
      calls.push(url);
      if (url === "/library") await library.promise;
    }
    return { ok: true, json: async () => [] };
  } });
  try {
    await app.flush(); initial = false;
    app.find((node) => node.type === "button" && node.props["aria-pressed"] !== undefined && text(node).startsWith("Queue")).props.onClick(); await app.flush();
    app.button("Refresh").props.onClick(); await app.flush();
    assert.deepEqual(calls.slice().sort(), ["download", "/incoming", "/library", "/ingest/jobs"].sort());
    download.resolve(); await app.flush();
    assert.equal(app.button("Refreshing…").props.disabled, true);
    library.resolve(); await app.flush();
    assert.equal(app.button("Refresh").props.disabled, false);
  } finally { app.dispose(); }
});

test("Films defaults to Library, keeps one queue mounted, and does not switch views on background updates", async () => {
  const queue = emptyQueue();
  const app = libraryHarness({ queue });
  const switcher = (name) => app.find((node) => node.type === "button" && node.props["aria-pressed"] !== undefined && text(node).startsWith(name));
  const queueContainer = () => app.find((node) => node.props?.hidden !== undefined && nodes(node).some((child) => child.type === "AcquisitionPanel"));
  try {
    await app.flush();
    assert.equal(switcher("Library").props["aria-pressed"], true);
    assert.equal(queueContainer().props.hidden, true);
    assert.equal(app.find((node) => node.type === "AcquisitionPanel").props.queue, queue);
    switcher("Queue").props.onClick(); await app.flush();
    assert.equal(queueContainer().props.hidden, false);
    switcher("Library").props.onClick(); await app.flush();
    queue.items = [{ id: "new", title: "New film", year: 1980, status: "downloading" }];
    app.render(); await app.flush();
    assert.equal(switcher("Library").props["aria-pressed"], true);
    assert.equal(queueContainer().props.hidden, true);
    const panels = nodes(app.state).filter((node) => node.type === "AcquisitionPanel");
    assert.equal(panels.length, 1);
    assert.equal(panels[0].props.queue, queue, "the queue controller survives every view change");
  } finally { app.dispose(); }
});

test("workspace counts reconcile managed jobs once and keep attention visible from Library", async () => {
  const films = [
    { path: "V:/films/Ready.mkv", filename: "Ready.mkv", title: "Ready", status: "indexed" },
    { path: "V:/films/Managed.mkv", filename: "Managed.mkv", title: "Managed", status: "not_indexed" },
    { path: "V:/films/Manual.mkv", filename: "Manual.mkv", title: "Manual", status: "not_indexed" },
  ];
  const jobs = [
    { job_id: "managed", path: films[1].path, filename: films[1].filename, status: "running" },
    { job_id: "manual", path: films[2].path, filename: films[2].filename, status: "queued", queue_position: 1 },
  ];
  const queue = { ...emptyQueue(), items: [
    { id: "managed", title: "Managed", status: "ingesting", ingest_job_id: "managed", film_path: films[1].path },
    { id: "failed", title: "Failed", status: "failed", error: "Download failed" },
    { id: "review", title: "Review", status: "needs_review", review: {} },
    { id: "ready", title: "Ready", status: "ready", film_path: films[0].path },
  ] };
  const app = libraryHarness({ queue, films, jobs });
  const switcher = (name) => app.find((node) => node.type === "button" && node.props["aria-pressed"] !== undefined && text(node).startsWith(name));
  try {
    await app.flush();
    assert.match(text(switcher("Library")), /Library\s*1/);
    assert.match(text(switcher("Queue")), /Queue\s*3/);
    assert.match(text(switcher("Queue")), /2.*attention/i);
    queue.items = queue.items.filter((item) => !["failed", "review"].includes(item.id));
    app.render(); await app.flush();
    assert.match(text(switcher("Queue")), /Queue\s*2/);
    assert.doesNotMatch(text(switcher("Queue")), /attention/i);
    assert.equal(switcher("Library").props["aria-pressed"], true);
  } finally { app.dispose(); }
});

test("a manual preparation failure signals Library without moving the selected Queue view", async () => {
  const films = [
    { path: "V:/films/Manual.mkv", filename: "Manual.mkv", title: "Manual", status: "not_indexed" },
    { path: "V:/films/Managed.mkv", filename: "Managed.mkv", title: "Managed", status: "not_indexed" },
  ];
  let jobs = [{ job_id: "manual", path: films[0].path, filename: films[0].filename, status: "running" }];
  let reconcile;
  const queue = { ...emptyQueue(), items: [
    { id: "managed", title: "Managed", status: "failed", error: "Download failed", film_path: films[1].path },
  ] };
  const app = libraryHarness({ queue, onReconcile: (callback) => { reconcile = callback; }, fetch: async (url) => ({
    ok: true, json: async () => url === "/library" ? films : url === "/ingest/jobs" ? jobs : [],
  }) });
  const switcher = (name) => app.find((node) => node.type === "button" && node.props["aria-pressed"] !== undefined && text(node).startsWith(name));
  try {
    await app.flush();
    assert.match(text(switcher("Library")), /Library\s*0/);
    assert.doesNotMatch(text(switcher("Library")), /attention/i);
    switcher("Queue").props.onClick(); await app.flush();
    jobs = [{ ...jobs[0], status: "error", error: "Scene preparation failed" }];
    await reconcile(); await app.flush();
    assert.equal(switcher("Queue").props["aria-pressed"], true, "a background failure never changes the selected view");
    assert.match(text(switcher("Library")), /Library\s*1/);
    assert.match(text(switcher("Library")), /1 needs attention/);
    assert.match(text(switcher("Queue")), /1 needs attention/, "Queue retains only its managed failure");
    assert.doesNotMatch(text(switcher("Queue")), /2 need attention/);
    const library = app.find((node) => node.props?.id === "films-library-panel");
    assert.equal(library.props.hidden, true);
    const rows = nodes(library).filter((node) => node.type === "article");
    assert.equal(rows.length, 1);
    assert.match(text(rows[0]), /Manual.*Scene preparation failed/);
    assert.ok(nodes(rows[0]).some((node) => node.type === "button" && text(node) === "Try again"));
    switcher("Library").props.onClick(); await app.flush();
    assert.equal(app.find((node) => node.props?.id === "films-library-panel").props.hidden, false);
  } finally { app.dispose(); }
});

test("downloaded files stay behind Add films and their shortcut opens that source", async () => {
  const candidate = { relative_path: "Film.mkv", filename: "Film.mkv", suggested_filename: "Film (1985).mkv", suggested_title: "Film", suggested_year: 1985, size_gb: 1, subtitle_review_candidates: [] };
  const app = libraryHarness({ incoming: [candidate] });
  try {
    await app.flush();
    assert.equal(app.find((node) => node.type === "AddFilmForm"), undefined);
    assert.equal(app.button("Review & add"), undefined);
    app.button("1 downloaded file to review").props.onClick(); await app.flush();
    const form = app.find((node) => node.type === "AddFilmForm");
    assert.equal(form.props.initialSource, "downloaded");
    assert.match(text(form.props.downloadedFiles), /Film\.mkv/);
    assert.ok(nodes(form.props.downloadedFiles).some((node) => node.type === "button" && text(node) === "Review & add"));
  } finally { app.dispose(); }
});

test("adding returns to the queue only after success and keeps failures beside the form", async () => {
  const focus = [], scroll = [], calls = [];
  let succeeds = false;
  const queue = { ...emptyQueue(), execute: async (...args) => { calls.push(args); return succeeds; } };
  const app = libraryHarness({ queue });
  const switcher = (name) => app.find((node) => node.type === "button" && node.props["aria-pressed"] !== undefined && text(node).startsWith(name));
  try {
    await app.flush();
    switcher("Queue").props.ref.current = { focus: (value) => focus.push(value), scrollIntoView: (value) => scroll.push(value) };
    app.button("Add films").props.onClick(); await app.flush();
    queue.error = "A film with this name is already queued.";
    assert.equal(await app.find((node) => node.type === "AddFilmForm").props.onQueue("/release", {}), false);
    app.render(); await app.flush();
    assert.equal(app.find((node) => node.type === "AddFilmForm").props.requestError, queue.error);
    assert.equal(app.find((node) => node.type === "AcquisitionPanel").props.showError, false);
    assert.equal(switcher("Library").props["aria-pressed"], true);
    assert.equal(focus.length, 0);
    succeeds = true; queue.error = null;
    assert.equal(await app.find((node) => node.type === "AddFilmForm").props.onQueue("/release", {}), true);
    await app.flush();
    assert.equal(app.find((node) => node.type === "AddFilmForm"), undefined);
    assert.equal(app.find((node) => node.type === "AcquisitionPanel").props.showError, true);
    assert.equal(switcher("Queue").props["aria-pressed"], true);
    assert.equal(focus.length, 1); assert.equal(focus[0].preventScroll, true); assert.equal(scroll[0].block, "nearest");
    assert.deepEqual(JSON.parse(JSON.stringify(calls)), [["add", "/release", {}], ["add", "/release", {}]]);
    assert.match(text(app.state), /Film added to the queue/);
  } finally { app.dispose(); }
});

test("queue refresh ignores superseded responses, stops polling at idle, and aborts on unmount", async () => {
  const requests = [], timers = new Map(); let timerId = 0;
  const api = { acquisitionRequest: (url, init) => { const promise = deferred(); requests.push({ url, init, ...promise }); return promise.promise; }, messageOf: (e) => e.message };
  const app = harness("useAcquisitionQueue.ts", (exports) => exports.useAcquisitionQueue(async () => {}), { "./api": api }, {
    window: { addEventListener() {}, removeEventListener() {} }, setTimeout: (fn) => { timers.set(++timerId, fn); return timerId; }, clearTimeout: (id) => timers.delete(id),
  });
  try {
    assert.equal(requests.length, 2);
    const current = app.state.refresh();
    assert.equal(requests[0].init.signal.aborted, true);
    assert.equal(requests.length, 3, "pending health checks are shared across queue refreshes");
    requests[2].resolve({ items: [{ id: "fresh", status: "downloading" }] }); await current; await app.flush();
    requests[0].resolve({ items: [{ id: "stale", status: "ready" }] }); requests[1].resolve(status); await app.flush();
    assert.equal(app.state.items[0].id, "fresh"); assert.equal(timers.size, 1);
    const idle = app.state.refresh(); requests[3].resolve({ items: [{ id: "fresh", status: "ready" }] }); requests[4].resolve(status); await idle; await app.flush();
    assert.equal(timers.size, 0);
    app.state.refresh(); app.dispose(); assert.equal(requests[5].init.signal.aborted, true); assert.equal(requests[6].init.signal.aborted, true);
    requests[5].resolve({ items: [] }); requests[6].resolve(status); await app.flush();
  } finally { app.dispose(); }
});
