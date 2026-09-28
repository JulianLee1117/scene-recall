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
vm.runInNewContext(compile("../../lib/matching.ts"), { exports: helpers, process: { env: {} }, URLSearchParams });
const plain = (value) => JSON.parse(JSON.stringify(value));
const flush = async () => { for (let i = 0; i < 35; i++) await Promise.resolve(); };
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);

async function harness(file, name, args, imports) {
  const hooks = [], effects = [];
  let cursor = 0, scheduled = false, current;
  const same = (a, b) => a && b && a.length === b.length && a.every((value, index) => Object.is(value, b[index]));
  const schedule = () => { if (!scheduled) { scheduled = true; queueMicrotask(render); } };
  const react = {
    useState(initial) {
      const index = cursor++;
      hooks[index] ??= { value: typeof initial === "function" ? initial() : initial };
      return [hooks[index].value, (update) => { const next = typeof update === "function" ? update(hooks[index].value) : update; if (!Object.is(next, hooks[index].value)) { hooks[index].value = next; schedule(); } }];
    },
    useRef(initial) { return hooks[cursor++] ??= { current: initial }; },
    useEffect(effect, deps) {
      const index = cursor++;
      if (!hooks[index] || !same(hooks[index].deps, deps)) {
        const previous = hooks[index]; hooks[index] = { deps, cleanup: previous?.cleanup };
        effects.push(() => { hooks[index].cleanup?.(); hooks[index].cleanup = effect(); });
      }
    },
  };
  const exported = {};
  const timers = new Map(); let timerId = 0;
  vm.runInNewContext(compile(file), {
    exports: exported, AbortController, URLSearchParams, document: { body: {} },
    setTimeout: (callback) => { timers.set(++timerId, callback); return timerId; }, clearTimeout: (id) => timers.delete(id),
    require(module) {
      if (module === "react") return react;
      if (module === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }), Fragment: "fragment" };
      if (module === "react-dom") return { createPortal: (node) => node };
      if (imports[module]) return imports[module];
      if (module.endsWith(".module.css")) return { default: new Proxy({}, { get: (_, key) => key }) };
      return { default: module };
    },
  });
  function render() { cursor = 0; scheduled = false; current = exported[name](...args); effects.splice(0).forEach((effect) => effect()); }
  render(); await flush();
  return { get current() { return current; }, get nodes() { return nodes(current); }, async render() { render(); await flush(); }, async tick() { const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach((callback) => callback()); await flush(); }, button(label) { return nodes(current).find((node) => node.type === "button" && text(node).trim() === label); } };
}

const source = { id: "ref", unit_id: "unit-a", film_id: "film-a", title: "Source scene", source_start: 10, source_end: 14, reference_time: 12, locked: false };
const candidate = { id: "one", film_title: "Film B", evidence: "A useful cut", outgoing: source, incoming: { ...source, film_id: "film-b" }, preview_ready: true, cues: [{ code: "position", label: "Same screen position", description: "Both subjects sit at the lower right." }], primary_cue: "position" };
const request = { cohort_id: "cohort", reference: { unit_id: "unit-a", time: 12 }, focus: "auto", timing: "nearby", film_ids: [], include_source_film: true, min_incoming_seconds: 1, allow_reframing: false };

test("scene links preserve explicit moments and use an outgoing handle when no moment exists", () => {
  const shot = { unit_id: "shot & 1", t_start: 10, t_end: 14 };
  const query = (url) => new URLSearchParams(url.split("?")[1]);
  assert.equal(query(helpers.matchSceneHref(shot)).get("time"), "13.85");
  assert.equal(query(helpers.matchSceneHref(shot)).get("unit_id"), "shot & 1");
  assert.equal(query(helpers.matchSceneHref({ ...shot, matched_frame_timestamp: 11.234 })).get("time"), "11.234");
  assert.equal(query(helpers.matchSceneHref({ ...shot, evidence_timestamp: 12.4 })).get("time"), "12.4");
  assert.equal(query(helpers.matchSceneHref(shot, 12.789)).get("time"), "12.789");
  assert.equal(query(helpers.matchSceneHref(shot, 20)).get("time"), "13.999");
});

test("the result's declared primary cue wins over cue ordering", () => {
  const match = { ...candidate, cues: [{ code: "shape", label: "Similar shape", description: "Shape" }, ...candidate.cues] };
  assert.equal(helpers.primaryMatchCue(match).code, "position");
});

test("restored API null defaults and reordered film scope do not make results stale", () => {
  const saved = { ...request, film_ids: ["b", "a"], reference: { ...request.reference, region: null, subject_point: null } };
  assert.equal(helpers.matchSearchKey(saved), helpers.matchSearchKey({ ...request, film_ids: ["a", "b"] }));
});

test("progressive results do not request missing thumbnails or enable legacy-only matching", async () => {
  const state = { job: { id: "search", status: "running", result: { candidates: [candidate] } }, submitted: request, busy: false, previews: {}, prepare() {}, async start() {}, cancel() {}, preparingId: null };
  const ui = await harness("MatchSearch.tsx", "default", [], {
    "next/navigation": { useRouter: () => ({ push() {}, replace() {} }), useSearchParams: () => new URLSearchParams("unit_id=unit-a&time=12") },
    "@/lib/lab": { seconds: (value) => value.toFixed(3) },
    "@/lib/matching": { ...helpers, matchingRequest: async (route) => route === "/cohorts" ? { cohorts: [{ id: "cohort", films: [], motion_count: 0, motion_ready: false, subject_ready: false, visual_ready: true, shape_ready: true }] } : { reference: source, bounds: { t_start: 10, t_end: 14 } } },
    "./useMatchSearch": { useMatchSearch: () => state },
  });
  assert.equal(ui.nodes.filter((node) => node.type === "img").length, 0);
  assert.equal(ui.button("Find match cuts").props.disabled, true);
  state.job.result.candidates = [{ ...candidate, frame_url: "/matching/searches/search/candidates/one/frame" }];
  await ui.render();
  assert.equal(ui.nodes.find((node) => node.type === "img").props.src, "/matching/searches/search/candidates/one/frame");
  assert.ok(text(ui.current).includes("Both subjects sit at the lower right."));
  assert.ok(!text(ui.current).includes("%"));
});

test("saved focused searches restore without starting work; an explicit new search checks every cue", async () => {
  let starts = 0, restores = 0, newRequest;
  const routes = [];
  const savedRequest = { ...request, focus: "shape", timing: "fixed", film_ids: ["film-b"] };
  const saved = { id: "saved", status: "completed", request: savedRequest, reference: source, result: { candidates: [candidate] } };
  const state = { job: null, submitted: null, busy: false, previews: {}, prepare() {}, async start(value) { starts++; newRequest = value; }, async restore() { restores++; state.job = saved; state.submitted = savedRequest; return saved; }, cancel() {}, preparingId: null };
  const ui = await harness("MatchSearch.tsx", "default", [], {
    "next/navigation": { useRouter: () => ({ push() {}, replace() {} }), useSearchParams: () => new URLSearchParams("search_id=saved") },
    "@/lib/lab": { seconds: (value) => value.toFixed(3) },
    "@/lib/matching": { ...helpers, matchingRequest: async (route) => { routes.push(route); return { cohorts: [{ id: "cohort", films: [{ film_id: "film-b" }], motion_count: 80, motion_ready: true, subject_ready: true }] }; } },
    "./useMatchSearch": { useMatchSearch: () => state },
  });
  assert.equal(restores, 1);
  assert.equal(starts, 0);
  assert.deepEqual(routes, ["/cohorts"]);
  assert.equal(ui.button("Shape"), undefined);
  assert.ok(text(ui.current).includes("Saved shape search"));
  assert.ok(text(ui.current).includes("Pinned frame"));
  assert.ok(text(ui.current).includes("Matches 1"));
  assert.ok(!text(ui.current).includes("Settings changed"));
  assert.equal(ui.nodes.find((node) => node.type === "input" && node.props["aria-label"] === "Include source film").props.checked, true);
  ui.nodes.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} }); await flush();
  assert.equal(starts, 1);
  assert.equal(newRequest.focus, "auto");
  assert.equal(newRequest.timing, "fixed");
  assert.equal(newRequest.include_source_film, true, "restoring and rerunning must honor the saved explicit inclusion");
  assert.deepEqual(plain(newRequest.film_ids), ["film-b"]);
});

test("failed playback offers a forced preview retry", async () => {
  let forced;
  const props = { candidate, searchId: "search", preparing: false, onPrepare: (force) => { forced = force; }, onClose() {} };
  const ui = await harness("MatchCutPreview.tsx", "default", [props], { "@/lib/matching": helpers });
  ui.nodes.find((node) => node.type === "video").props.onError(); await flush();
  assert.ok(ui.button("Retry preview"));
  props.candidate = { ...candidate, evidence: "An updated explanation from polling" };
  await ui.render();
  assert.ok(ui.button("Retry preview"), "polling updates must preserve playback errors");
  ui.button("Retry preview").props.onClick();
  assert.equal(forced, true);
  props.candidate = { ...candidate, preview_url: "/matching/searches/retry/candidates/one/preview" };
  await ui.render();
  assert.ok(ui.nodes.find((node) => node.type === "video"), "a replaced preview can play again");
});

test("every preview dismissal releases the modal before restoring the trigger focus", async () => {
  for (const action of ["escape", "close button", "backdrop"]) {
    let open = true, focus = "dialog", prevented = false;
    const ui = await harness("MatchCutPreview.tsx", "default", [{ candidate, searchId: "search", preparing: false, onPrepare() {}, onClose() { if (!open) focus = "preview trigger"; } }], { "@/lib/matching": helpers });
    const dialog = ui.nodes.find((node) => node.type === "dialog");
    dialog.props.ref.current = { close() { open = false; } };
    if (action === "escape") dialog.props.onCancel({ preventDefault() { prevented = true; } });
    else if (action === "close button") ui.nodes.find((node) => node.props?.["aria-label"] === "Close cut preview").props.onClick();
    else { const target = {}; dialog.props.onClick({ target, currentTarget: target }); }
    assert.equal(focus, "preview trigger", action);
    if (action === "escape") assert.equal(prevented, true);
  }
});

test("forced retries use the completed search parent and can replace a ready cached preview", async () => {
  const requests = [];
  const ui = await harness("useMatchSearch.ts", "useMatchSearch", [], { "@/lib/matching": { ...helpers, matchingRequest: async (route, options) => {
    requests.push({ route, options });
    if (route === "/searches") return { id: "parent", status: "completed", result: { candidates: [candidate] } };
    return { id: "child", status: "completed", result: { candidate: { ...candidate, preview_url: "/matching/searches/child/candidates/one/preview" } } };
  } } });
  await ui.current.start(request); await flush();
  await ui.current.prepare(candidate); await flush();
  assert.equal(requests.length, 1);
  await ui.current.prepare(candidate, { force: true }); await flush();
  assert.equal(requests.at(-1).route, "/searches/parent/candidates/one/preview");
  assert.equal(ui.current.previews.one.preview_url, "/matching/searches/child/candidates/one/preview");
  assert.ok(requests.every((item) => !item.route.includes("project") && !item.route.includes("lab")));
});

test("a replacement search cancels an active child without cancelling its completed parent", async () => {
  const requests = []; let count = 0;
  const ui = await harness("useMatchSearch.ts", "useMatchSearch", [], { "@/lib/matching": { ...helpers, matchingRequest: async (route, options) => {
    requests.push(route);
    if (route === "/searches") return { id: `parent-${++count}`, status: "completed", result: { candidates: [{ ...candidate, preview_ready: false }] } };
    if (route.endsWith("/cancel")) return {};
    return { id: "child", status: "running" };
  } } });
  await ui.current.start(request); await flush();
  await ui.current.prepare({ ...candidate, preview_ready: false }); await flush();
  await ui.current.start({ ...request, focus: "shape" }); await flush();
  assert.deepEqual(requests, ["/searches", "/searches/parent-1/candidates/one/preview", "/searches/child/cancel", "/searches"]);
  assert.equal(ui.current.job.id, "parent-2");
});

test("restoring a completed job reuses its result with GET only", async () => {
  const requests = [];
  const saved = { id: "saved", status: "completed", request: { ...request, focus: "position" }, reference: source, result: { candidates: [candidate] } };
  const ui = await harness("useMatchSearch.ts", "useMatchSearch", [], { "@/lib/matching": { ...helpers, matchingRequest: async (route, options) => { requests.push({ route, method: options?.method ?? "GET" }); return saved; } } });
  const result = await ui.current.restore("saved"); await flush();
  assert.equal(result.id, "saved");
  assert.equal(ui.current.submitted.focus, "position");
  assert.equal(ui.current.job.result.candidates[0].id, "one");
  await ui.tick();
  assert.deepEqual(requests, [{ route: "/searches/saved", method: "GET" }]);
});

test("restoring a running job resumes polling without enqueueing work", async () => {
  let reads = 0;
  const routes = [];
  const ui = await harness("useMatchSearch.ts", "useMatchSearch", [], { "@/lib/matching": { ...helpers, matchingRequest: async (route, options) => {
    routes.push({ route, method: options?.method ?? "GET" });
    return { id: "saved", status: ++reads === 1 ? "running" : "completed", request, reference: source, result: { candidates: reads === 1 ? [] : [candidate] } };
  } } });
  await ui.current.restore("saved"); await flush();
  assert.equal(ui.current.busy, true);
  await ui.tick();
  assert.equal(ui.current.job.status, "completed");
  assert.ok(routes.every((item) => item.method === "GET" && item.route === "/searches/saved"));
});

test("one scene search checks every cue; adjusting scope waits for Find match cuts", async () => {
  const starts = [];
  const state = { job: null, submitted: null, busy: false, previews: {}, prepare() {}, async start(value) { starts.push(value); state.submitted = value; state.job = { id: "search", status: "completed", result: { candidates: [candidate] } }; return state.job; }, cancel() {}, preparingId: null };
  const ui = await harness("MatchSearch.tsx", "default", [], {
    "next/navigation": { useRouter: () => ({ push() {}, replace() {} }), useSearchParams: () => new URLSearchParams("unit_id=unit-a&time=12") },
    "@/lib/lab": { seconds: (value) => value.toFixed(3) },
    "@/lib/matching": { ...helpers, matchingRequest: async (route) => route === "/cohorts" ? { cohorts: [{ id: "cohort", films: [{ film_id: "film-b", title: "Film B" }], motion_count: 80, motion_ready: true, subject_ready: true }] } : { reference: source } },
    "./useMatchSearch": { useMatchSearch: () => state },
  });
  assert.equal(starts.length, 1);
  assert.equal(starts[0].focus, "auto");
  assert.equal(starts[0].include_source_film, false, "new scene searches discover cuts in other films by default");
  for (const label of ["Position", "Shape", "Subject movement"]) {
    assert.equal(ui.button(label), undefined);
  }
  ui.nodes.find((node) => node.type === "input" && node.props["aria-label"] === "Include source film").props.onChange({ target: { checked: true } }); await flush();
  assert.equal(starts.length, 1, "adjusting scope must not enqueue work");
  assert.ok(text(ui.current).includes("Settings changed. Run search to update results."));
  ui.nodes.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} }); await flush();
  assert.equal(starts.length, 2);
  assert.equal(starts[1].focus, "auto");
  assert.equal(starts[1].include_source_film, true);
});

test("choosing a different reference resets source-film inclusion without changing an explicit same-reference choice", async () => {
  let query = "unit_id=unit-a&time=12";
  const starts = [];
  const state = { job: null, submitted: null, busy: false, previews: {}, prepare() {}, async start(value) { starts.push(value); state.submitted = value; return { id: `search-${starts.length}` }; }, cancel() {}, preparingId: null };
  const ui = await harness("MatchSearch.tsx", "default", [], {
    "next/navigation": { useRouter: () => ({ push() {}, replace() {} }), useSearchParams: () => new URLSearchParams(query) },
    "@/lib/lab": { seconds: (value) => value.toFixed(3) },
    "@/lib/matching": { ...helpers, matchingRequest: async (route) => route === "/cohorts" ? { cohorts: [libraryCohort] } : { reference: { ...source, unit_id: new URLSearchParams(route.split("?")[1]).get("unit_id") } } },
    "./useMatchSearch": { useMatchSearch: () => state },
  });
  const inclusion = () => ui.nodes.find((node) => node.type === "input" && node.props["aria-label"] === "Include source film");
  assert.equal(starts[0].include_source_film, false);
  inclusion().props.onChange({ target: { checked: true } }); await flush();
  await ui.render();
  assert.equal(inclusion().props.checked, true);
  assert.equal(starts.length, 1);
  query = "unit_id=unit-b&time=12"; await ui.render();
  assert.equal(starts.length, 2);
  assert.equal(starts[1].reference.unit_id, "unit-b");
  assert.equal(starts[1].include_source_film, false);
  assert.equal(inclusion().props.checked, false);
});

const libraryFilms = Array.from({ length: 38 }, (_, index) => ({ film_id: index === 0 ? "film-a" : `library-${index}`, title: `Library film ${index + 1}` }));
const libraryCohort = { id: "cohort", films: libraryFilms.slice(0, 8), motion_count: 80, motion_ready: false, subject_ready: false,
  library: { ready: true, film_count: 38, frame_count: 117000, unit_count: 32000, films: libraryFilms } };

test("library-ready search uses every indexed film and retains explicit scope submission", async () => {
  const starts = [], routes = [];
  const state = { job: null, submitted: null, busy: false, previews: {}, prepare() {}, async start(value) {
    starts.push(value); state.submitted = value;
    state.job = { id: "search", status: "completed", result: { candidates: [candidate], coverage: { source: "library_keyframes", film_count: 37, frame_count: 117000 }, reference_summary: { kind: "people", count: 2 } } };
    return state.job;
  }, cancel() {}, preparingId: null };
  const ui = await harness("MatchSearch.tsx", "default", [], {
    "next/navigation": { useRouter: () => ({ push() {}, replace() {} }), useSearchParams: () => new URLSearchParams("unit_id=unit-a&time=12") },
    "@/lib/lab": { seconds: (value) => value.toFixed(3) },
    "@/lib/matching": { ...helpers, matchingRequest: async (route) => { routes.push(route); return route === "/cohorts" ? { cohorts: [libraryCohort] } : { reference: source }; } },
    "./useMatchSearch": { useMatchSearch: () => state },
  });
  await ui.render();
  assert.equal(starts.length, 1, "library readiness must start search without prepared motion or subject windows");
  assert.equal(ui.button("Find match cuts").props.disabled, false);
  const filmInputs = () => ui.nodes.filter((node) => node.type === "input" && node.props.type === "checkbox" && !node.props["aria-label"]);
  assert.equal(filmInputs().length, 38);
  assert.ok(text(ui.current).includes("Library film 38"));
  assert.ok(text(ui.current).includes("Indexed library · 37 films"));
  assert.ok(text(ui.current).includes(`Full library index: ${(117000).toLocaleString()} keyframes across 38 films.`));
  assert.ok(text(ui.current).includes("Searches indexed keyframes, then checks nearby source frames in shortlisted scenes."));
  assert.ok(text(ui.current).includes("2 prominent people"));
  filmInputs()[1].props.onChange({ target: { checked: false } }); await flush();
  assert.equal(starts.length, 1);
  assert.ok(text(ui.current).includes("Search scope 36 films"));
  assert.ok(text(ui.current).includes(`Full library index: ${(117000).toLocaleString()} keyframes across 38 films.`), "index totals must remain labeled as whole-library counts after filtering");
  assert.ok(text(ui.current).includes("Indexed library · 37 films"), "existing result coverage is unchanged by pending filters");
  assert.ok(text(ui.current).includes("2 prominent people"));
  assert.ok(text(ui.current).includes("Settings changed. Run search to update results."));
  ui.nodes.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} }); await flush();
  assert.equal(starts.length, 2);
  assert.equal(starts[1].film_ids.length, 37);
  assert.ok(!starts[1].film_ids.includes("library-1"));
  assert.equal(starts[1].include_source_film, false);
  await ui.tick();
  assert.equal(routes.filter((route) => route === "/cohorts").length, 1);
  assert.ok(!routes.some((route) => route.includes("/library")));
});

test("restored prepared results keep their original coverage beside the expanded next-search scope", async () => {
  let starts = 0;
  const saved = { id: "saved", status: "completed", request, reference: source, result: { candidates: [candidate], coverage: { film_count: 8, window_count: 80 } } };
  const state = { job: null, submitted: null, busy: false, previews: {}, prepare() {}, async start() { starts++; }, async restore() { state.job = saved; state.submitted = request; return saved; }, cancel() {}, preparingId: null };
  const ui = await harness("MatchSearch.tsx", "default", [], {
    "next/navigation": { useRouter: () => ({ push() {}, replace() {} }), useSearchParams: () => new URLSearchParams("search_id=saved") },
    "@/lib/lab": { seconds: (value) => value.toFixed(3) },
    "@/lib/matching": { ...helpers, matchingRequest: async () => ({ cohorts: [libraryCohort] }) },
    "./useMatchSearch": { useMatchSearch: () => state },
  });
  assert.equal(starts, 0);
  assert.ok(text(ui.current).includes("Prepared sample · 8 films"));
  assert.ok(!text(ui.current).includes("Indexed library · 38 films"));
  assert.ok(text(ui.current).includes("Search scope 38 films"));
  assert.ok(!text(ui.current).includes("prominent people"));
  const header = ui.nodes.find((node) => node.type === "../lab/LabWorkspaceHeader");
  assert.equal(header.props.title, "Match Cuts");
  assert.equal(header.props.project, undefined, "standalone searches use direct Labs navigation without project prompts");
});

test("search status distinguishes queue, running, stopping and terminal outcomes", async () => {
  const props = { job: { status: "queued", created_at: 100 }, busy: true, count: 0 };
  const ui = await harness("MatchSearchStatus.tsx", "default", [props], {});
  assert.ok(text(ui.current).includes("Waiting to start"));
  assert.ok(text(ui.current).includes("Other library or editing tasks"));
  props.job.status = "running"; props.job.progress = "Checking candidate cuts"; props.count = 2;
  await ui.render();
  assert.ok(text(ui.current).includes("Searching for match cuts"));
  assert.ok(text(ui.current).includes("Checking candidate cuts"));
  assert.ok(!text(ui.current).includes("2 possible cuts"));
  props.job.cancel_requested = true; await ui.render();
  assert.ok(text(ui.current).includes("Stopping search"));
  props.job.cancel_requested = false; props.job.finished_at = 142; props.busy = false;
  for (const [status, title] of [["completed", "Search complete"], ["failed", "Search failed"], ["interrupted", "Search interrupted"], ["cancelled", "Search cancelled"]]) {
    props.job.status = status; await ui.render();
    assert.ok(text(ui.current).includes(title));
    assert.ok(!text(ui.current).includes("Checking candidate cuts"));
    assert.ok(text(ui.current).includes("Elapsed 0:42"));
  }
});

test("poll recovery clears transient errors while preserving a failed user action", async () => {
  let reads = 0;
  const ui = await harness("useMatchSearch.ts", "useMatchSearch", [], { "@/lib/matching": { ...helpers, matchingRequest: async (route) => {
    if (route === "/searches") return { id: "parent", status: "running" };
    if (route.endsWith("/cancel")) throw new Error("Cancel failed");
    if (++reads === 1) throw new Error("Temporary connection failure");
    return { id: "parent", status: reads >= 3 ? "completed" : "running" };
  } } });
  await ui.current.start(request); await flush();
  await ui.tick(); assert.equal(ui.current.error, "Temporary connection failure");
  await ui.tick(); assert.equal(ui.current.error, "");
  await ui.current.cancel(); await flush();
  const actionError = ui.current.error; assert.ok(actionError);
  await ui.tick(); assert.equal(ui.current.error, actionError);
  assert.equal(ui.current.job.status, "completed");
});

test("a failed preview poll retries the same child without submitting another render", async () => {
  let reads = 0, renders = 0;
  const waiting = { ...candidate, preview_ready: false };
  const ui = await harness("useMatchSearch.ts", "useMatchSearch", [], { "@/lib/matching": { ...helpers, matchingRequest: async (route) => {
    if (route === "/searches") return { id: "parent", status: "completed", result: { candidates: [waiting] } };
    if (route.endsWith("/preview")) { renders++; return { id: "child", status: "running" }; }
    assert.equal(route, "/searches/child");
    if (++reads === 1) throw new Error("Preview status unavailable");
    return { id: "child", status: "completed", result: { candidate } };
  } } });
  await ui.current.start(request); await flush();
  await ui.current.prepare(waiting); await flush();
  await ui.tick(); assert.equal(ui.current.preparingId, "one");
  assert.equal(ui.current.error, "Preview status unavailable");
  await ui.tick(); assert.equal(ui.current.error, "");
  assert.equal(ui.current.previews.one.preview_ready, true);
  assert.equal(renders, 1);
});

test("closing a preview clears work that has not yet been submitted", async () => {
  const routes = [];
  const waiting = { ...candidate, preview_ready: false };
  const ui = await harness("useMatchSearch.ts", "useMatchSearch", [], { "@/lib/matching": { ...helpers, matchingRequest: async (route) => {
    routes.push(route);
    return { id: "parent", status: route === "/searches" ? "running" : "completed", result: { candidates: [waiting] } };
  } } });
  await ui.current.start(request); await flush();
  await ui.current.prepare(waiting); await flush();
  ui.current.dismissPreview(); await flush();
  await ui.tick();
  assert.equal(ui.current.job.status, "completed");
  assert.deepEqual(routes, ["/searches", "/searches/parent"]);
});
