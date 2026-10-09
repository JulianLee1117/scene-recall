const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const helpers = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "transitions.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: helpers });
const fastTimingHelpers = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "fast-edit-timing.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: fastTimingHelpers, require: () => helpers });
const plain = (value) => JSON.parse(JSON.stringify(value));
const sourceA = { film_id: "film-a", unit_id: "shot-a", source_start: 10, source_end: 13 };
const sourceB = { film_id: "film-b", unit_id: "shot-b", source_start: 20, source_end: 23 };
const recipe = { id: "luma-reveal", duration: 0.5, direction: "left", easing: "smooth", intensity: 0.7, softness: 0.2 };
const request = { outgoing: sourceA, incoming: sourceB, recipe, output: { aspect: "landscape", quality: "draft" } };

test("search selections stay inside the scene at either boundary and preserve short clips", () => {
  const result = { film_id: "film-a", unit_id: "shot-a", film_title: "Film A", t_start: 10, t_end: 20 };
  for (const [matched, start, end] of [[undefined, 10, 13], [10, 10, 13], [15, 13.5, 16.5], [20, 17, 20], [100, 17, 20], [0, 10, 13]]) {
    const selected = helpers.sourceFromResult({ ...result, matched_frame_timestamp: matched });
    assert.deepEqual(plain(selected), { ...sourceA, title: "Film A", source_start: start, source_end: end });
  }
  assert.deepEqual(plain(helpers.sourceFromResult({ ...result, film_title: "", t_end: 10.25, matched_frame_timestamp: 10.1 })),
    { ...sourceA, title: "film-a", source_start: 10, source_end: 10.25 });
});

test("pair validation rejects missing, nonfinite, reversed, negative and overlong source windows", () => {
  assert.ok(helpers.validatePair(null, sourceB, recipe));
  assert.ok(helpers.validatePair(sourceA, null, recipe));
  assert.ok(helpers.validatePair(sourceA, sourceB, null));
  for (const patch of [
    { source_start: NaN }, { source_end: NaN }, { source_start: Infinity }, { source_end: Infinity },
    { source_start: -0.1 }, { source_end: 10 }, { source_end: 9 }, { source_end: 10.19 }, { source_end: 22.01 },
  ]) {
    assert.match(helpers.validatePair({ ...sourceA, ...patch }, sourceB, recipe), /clip A/i);
    assert.match(helpers.validatePair(sourceA, { ...sourceA, ...patch }, recipe), /clip B/i);
  }
  assert.equal(helpers.validatePair(sourceA, sourceB, recipe), null);
  assert.equal(helpers.validatePair({ ...sourceA, source_end: 22 }, sourceB, recipe), null);
  assert.ok(helpers.validatePair(sourceA, sourceB, recipe, 2));
});

test("the overlap must leave source context on both clips while a hard cut permits short clips", () => {
  for (const duration of [0.5, 0.49, 0.51]) {
    assert.match(helpers.validatePair({ ...sourceA, source_end: 10 + duration }, sourceB, recipe), /shorter than clip A/);
    assert.match(helpers.validatePair(sourceA, { ...sourceB, source_end: 20 + duration }, recipe), /shorter than clip B/);
  }
  assert.equal(helpers.validatePair({ ...sourceA, source_end: 10.54 }, sourceB, recipe), null);
  assert.ok(helpers.validatePair({ ...sourceA, source_end: 10.36 }, sourceB, { ...recipe, duration: 0.35 }),
    "half-up rounding makes both windows 11 frames, leaving no source context");
  assert.equal(helpers.validatePair({ ...sourceA, source_end: 10.39 }, sourceB, { ...recipe, duration: 0.35 }), null);
  assert.equal(helpers.validatePair({ ...sourceA, source_end: 10.25 }, sourceB, { ...recipe, id: "hard-cut", duration: 0 }), null);
  assert.ok(helpers.validatePair({ ...sourceA, source_end: 10.19 }, sourceB, { ...recipe, id: "hard-cut", duration: 0 }));
});

test("nonfinite and unsupported transition durations cannot be submitted as valid overlaps", () => {
  for (const duration of [NaN, Infinity, -Infinity, 0, -0.5, 0.07, 2.1]) {
    assert.ok(helpers.validatePair(sourceA, sourceB, { ...recipe, duration }), `duration ${duration} must fail`);
  }
});

test("speed duration mirrors backend source-time integration and half-up output frame counts", () => {
  // Values from pipeline.transitions.retiming.plan, including its 2048 integration intervals.
  for (const [mode, speed, span, curve, duration, expected, frames] of [
    ["rush", 4, 2, "smooth", 3, 1.99346906206962, 60],
    ["slow-hit", .25, 2, "smooth", 3, 4.973876248278473, 149],
    ["pulse", 4, 2, "snappy", 3, 2.0670385212061886, 62],
    ["rush", 2, .2, "snappy", .35, .2938328757352107, 9],
    ["rush", 1, .35, "smooth", .35, .35, 11],
  ]) {
    const retime = { ...helpers.DEFAULT_RETIME, mode, speed, span, curve };
    assert.ok(Math.abs(helpers.retimedDuration(duration, retime) - expected) < 1e-12);
    assert.equal(helpers.retimedFrames(duration, retime), frames);
  }
  assert.equal(helpers.retimedDuration(3.017), 3.017);
  assert.equal(helpers.retimedFrames(.35), 11);
});

test("speed validates source spans and output overlaps rather than silently trimming fixed footage", () => {
  const rush = { ...helpers.DEFAULT_RETIME, mode: "rush", speed: 4, span: .8 };
  const shortA = { ...sourceA, source_end: 10.8 };
  assert.equal(helpers.validatePair(shortA, sourceB, recipe), null);
  assert.match(helpers.validatePair(shortA, sourceB, recipe, 12, rush), /clip A after speed changes/);
  assert.match(helpers.validatePair(shortA, sourceB, recipe, 12, { ...rush, span: .81 }), /ramp window exceeds clip A/);
  assert.equal(helpers.validatePair({ ...sourceA, source_end: 10.2 }, sourceB, { ...recipe, duration: .3 }, 12, { ...rush, mode: "slow-hit", speed: .25, span: .2 }), null, "slow motion can provide a longer output overlap without expanding the source window");
  assert.equal(helpers.validatePair(shortA, sourceB, { ...recipe, id: "hard-cut", duration: 0 }, 12, rush), null);
  for (const retime of [null, [], { ...rush, span: NaN }, { ...rush, span: .09 }, { ...rush, speed: .5 }, { ...rush, speed: 4.1 }, { ...rush, mode: "slow-hit", speed: 2 }, { ...rush, curve: "linear" }, { ...rush, interpolation: "AI" }, { ...rush, unexpected: true }, { ...helpers.DEFAULT_RETIME, speed: 2 }, { ...helpers.DEFAULT_RETIME, interpolation: "flow" }]) assert.ok(helpers.validateRetime(retime));
  assert.equal(helpers.validateRetime(undefined), null);
  assert.equal(helpers.validateRetime({ ...rush, interpolation: "flow" }), null);
});

test("retiming changes the working receipt but preserves raw-source comparison and native endpoint evidence", () => {
  const retime = { ...helpers.DEFAULT_RETIME, mode: "rush", speed: 2 };
  const faster = { ...request, retime };
  assert.equal(helpers.samePair(request, faster), true);
  assert.equal(helpers.sameRetime(request.retime, faster.retime), false);
  assert.equal(helpers.sameRetime(undefined, helpers.DEFAULT_RETIME), true);
  assert.deepEqual(plain(helpers.workingChanges(request, faster)), ["Speed"]);
  assert.deepEqual(plain(helpers.matchingSourceEndpoints(savedJob, faster)), plain(helpers.matchingSourceEndpoints(savedJob, request)));
  const swept = helpers.timingSweep({ ...faster, outgoing: { ...sourceA, source_end: 10.7 }, recipe: { ...recipe, duration: .5 } });
  assert.ok(swept.length < 3, "retimed overlap bounds apply to every timing candidate");
  assert.ok(swept.every((variant) => helpers.sameRetime(variant.retime, retime) && helpers.validatePair(variant.outgoing, variant.incoming, variant.recipe, 12, retime) === null));
});

test("comparison ignores recipe and quality but detects every changed source window and framing", () => {
  assert.equal(helpers.samePair(request, { ...request, recipe: { ...recipe, id: "hard-cut", duration: 0 }, output: { ...request.output, quality: "high" } }), true);
  assert.equal(helpers.samePair(request, { ...request, output: { ...request.output, aspect: "portrait" } }), false);
  assert.equal(helpers.samePair(request, { ...request, outgoing: sourceB, incoming: sourceA }), false);
  for (const side of ["outgoing", "incoming"]) {
    for (const field of ["film_id", "source_start", "source_end"]) {
      const changed = { ...request, [side]: { ...request[side], [field]: field === "film_id" ? "different-film" : request[side][field] + 1 / 24 } };
      assert.equal(helpers.samePair(request, changed), false, `${side}.${field}`);
    }
  }
});

test("source payload strips display metadata and omits an absent unit identity", () => {
  assert.deepEqual(plain(helpers.sourcePayload({ ...sourceA, title: "Film A", browserOnly: true })), sourceA);
  assert.deepEqual(plain(helpers.sourcePayload({ ...sourceA, unit_id: null, title: "Film A" })),
    { film_id: "film-a", source_start: 10, source_end: 13 });
});

test("AI handoff requires a completed render and preserves its frozen pair instead of claiming generation", () => {
  const job = { id: "render-1", status: "completed", request, result: { renderer_version: "renderer-v1", manifest_url: "/manifest.json", frame_a_url: "/a.png", frame_b_url: "/b.png", frame_a_time: 12.958, frame_b_time: 20.02 } };
  for (const status of ["queued", "running", "failed", "cancelled", "interrupted"]) {
    assert.throws(() => helpers.handoffReceipt({ ...job, status }, "external-model", "Connect the frames"), /Render a source pair/);
  }
  assert.throws(() => helpers.handoffReceipt({ ...job, result: null }, "external-model", "Connect the frames"), /Render a source pair/);
  const receipt = helpers.handoffReceipt(job, "external-model", "Connect the frames");
  assert.equal(receipt.generated_result, false);
  assert.equal(receipt.source_render_id, job.id);
  assert.equal(receipt.renderer_version, "renderer-v1");
  assert.equal(receipt.manifest_url, "/manifest.json");
  assert.deepEqual(plain(receipt.request), request);
  assert.deepEqual(plain(receipt.endpoints.outgoing), { ...sourceA, frame_url: "/a.png", sampled_time: 12.958 });
  assert.deepEqual(plain(receipt.endpoints.incoming), { ...sourceB, frame_url: "/b.png", sampled_time: 20.02 });
  assert.equal(receipt.provider_model, "external-model");
  assert.equal(receipt.prompt, "Connect the frames");
});

const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
const result = { preview_url: "/preview.mp4", manifest_url: "/manifest.json", frame_a_url: "/a.png", frame_b_url: "/b.png", frame_a_time: 12.958, frame_b_time: 20.02, renderer_version: "v1", duration: 5.5, transition_start: 2.5, transition_end: 3, fps: 30 };
const savedJob = { id: "saved", status: "completed", request, result };

async function workspaceHarness({ renders = [], failure = null, postFailureAt = 0, postStatus = "completed", postHandler, linked = "", initialMode = "", detailHandler, historyHandler, rendererVersion = "v1" } = {}) {
  const hooks = [], effects = [], requests = [], timers = new Map(), exported = {};
  const stored = new Map(renders.map((item) => [item.id, item]));
  let cursor = 0, scheduled = false, tree, timerId = 0;
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
  const definitions = [recipe, { ...recipe, id: "hard-cut", duration: 0 }].map((defaults) => ({ id: defaults.id, name: helpers.recipeTitle(defaults.id), description: "Test recipe", controls: ["duration"], defaults }));
  const compiled = ts.transpileModule(fs.readFileSync(path.join(__dirname, "TransitionWorkspace.tsx"), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  vm.runInNewContext(compiled, {
    exports: exported, AbortController, URL, URLSearchParams, Error, structuredClone, crypto: require("node:crypto").webcrypto,
    window: { location: { search: `?${[linked ? `render=${linked}` : "", initialMode ? `mode=${initialMode}` : ""].filter(Boolean).join("&")}`, href: "http://localhost/lab/transitions" }, history: { replaceState() {} } },
    setTimeout(callback) { timers.set(++timerId, callback); return timerId; }, clearTimeout(id) { timers.delete(id); },
    require(name) {
      if (name === "react") return react;
      if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }), Fragment: "fragment" };
      if (name === "./transitions") return helpers;
      if (name === "./fast-edit-timing") return fastTimingHelpers;
      if (name === "./SourceCard") return { default: function SourceCard() {} };
      if (name === "./PairTimeline") return { default: function PairTimeline() {} };
      if (name === "./ComparisonPlayer") return { default: function ComparisonPlayer() {} };
      if (name === "./SpeedControls") return { default: function SpeedControls() {} };
      if (name === "./RecipeControls") return { default: function RecipeControls() {} };
      if (name === "./useNotebook") return { default: function useNotebook() { const [notebook, setNotebook] = react.useState({ schema_version: 1, variants: {}, recipes: [] }); return { notebook, setNotebook, error: "" }; } };
      if (name === "@/features/lab/LabWorkspaceHeader") return { default: "LabWorkspaceHeader", LabEmptyState: "LabEmptyState" };
      if (name === "@/lib/lab") return { mediaUrl: (value) => value, seconds: (value) => String(value), labRequest: async (route, options) => {
        requests.push({ route, options });
        if (failure) throw new Error(failure);
        if (route === "/transitions/recipes") return { renderer_version: rendererVersion, recipes: definitions, limits: { max_clip_seconds: 12 } };
        if (route.startsWith("/transitions/renders?")) return historyHandler ? historyHandler() : { renders };
        if (route === "/transitions/renders" && options?.method === "POST") {
          const count = requests.filter((request) => request.options?.method === "POST").length;
          if (count === postFailureAt) throw new Error("Render queue unavailable");
          const created = postHandler ? postHandler(JSON.parse(options.body), count) : { ...savedJob, id: `created-${count}`, status: postStatus, result: postStatus === "completed" ? result : null, request: JSON.parse(options.body) };
          stored.set(created.id, created); return created;
        }
        if (route.startsWith("/transitions/renders/")) { const id = route.split("/").at(-1); if (stored.has(id)) return stored.get(id); if (detailHandler) return detailHandler(id); }
        throw new Error(`Unexpected request ${route}`);
      } };
      if (name.endsWith(".module.css")) return { default: new Proxy({}, { get: (_, key) => key }) };
      return { default: name };
    },
  });
  function render() { cursor = 0; scheduled = false; tree = exported.default(); effects.splice(0).forEach((effect) => effect()); }
  render(); await flush();
  return { requests, get tree() { return tree; }, get nodes() { return nodes(tree); }, button(label) { return nodes(tree).find((node) => node.type === "button" && text(node).trim() === label); },
    async tick() { const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach((callback) => callback()); await flush(); },
    complete(id) { const item = stored.get(id); stored.set(id, { ...item, status: "completed", result }); },
  };
}

test("empty and failed entry retain Labs navigation without creating projects or starting renders", async () => {
  for (const failure of [null, "API unavailable"]) {
    const ui = await workspaceHarness({ failure });
    assert.ok(ui.nodes.some((node) => node.type === "LabWorkspaceHeader"));
    assert.ok(failure ? ui.nodes.some((node) => node.type === "LabEmptyState") : ui.nodes.some((node) => node.type?.name === "PairTimeline"));
    assert.ok(ui.requests.every(({ options }) => !options?.method || options.method === "GET"));
    assert.ok(ui.requests.every(({ route }) => !route.includes("projects")));
    if (failure) assert.match(text(ui.nodes.find((node) => node.props?.role === "alert")), /API unavailable/);
  }
});

test("restored renders compare only the same frozen pair and source edits are visibly stale", async () => {
  const baseline = { ...savedJob, id: "baseline", request: { ...request, recipe: { ...recipe, id: "hard-cut", duration: 0 } } };
  const changedPair = { ...baseline, id: "different-time", request: { ...baseline.request, outgoing: { ...sourceA, source_end: 13 + 1 / 30 } } };
  const changedAspect = { ...baseline, id: "portrait", request: { ...baseline.request, output: { ...request.output, aspect: "portrait" } } };
  const ui = await workspaceHarness({ renders: [savedJob, baseline, changedPair, changedAspect] });
  const compare = ui.nodes.find((node) => node.props?.["aria-label"] === "Compare render");
  assert.deepEqual(nodes(compare).filter((node) => node.type === "option").map((node) => node.props.value), ["", "baseline"]);
  ui.button("Load to refine").props.onClick(); await flush();
  assert.ok(!text(ui.tree).includes("Working settings changed"));
  const sourceCard = ui.nodes.find((node) => node.type?.name === "SourceCard" && node.props.label === "A");
  sourceCard.props.onChange({ ...sourceCard.props.source, source_start: 10.1 }); await flush();
  assert.ok(text(ui.tree).includes("Working settings changed"));
  const player = ui.nodes.find((node) => node.type?.name === "ComparisonPlayer");
  assert.equal(player.props.job.request.outgoing.source_start, 10, "editing a source never changes a saved render's identity");
  assert.ok(ui.requests.every(({ options }) => !options?.method || options.method === "GET"));
});

test("reusing a source pair with null unit identities does not falsely mark identical settings stale", async () => {
  const job = { ...savedJob, request: { ...request, outgoing: { ...sourceA, unit_id: null }, incoming: { ...sourceB, unit_id: null } } };
  const ui = await workspaceHarness({ renders: [job] });
  ui.button("Load to refine").props.onClick(); await flush();
  assert.ok(!text(ui.tree).includes("Working settings changed"));
});

test("cold entry restores the displayed version into working settings without starting a render", async () => {
  const ui = await workspaceHarness({ renders: [savedJob] });
  assert.equal(ui.nodes.find((node) => node.type?.name === "SourceCard" && node.props.label === "A").props.source.source_start, 10);
  assert.equal(ui.nodes.find((node) => node.type?.name === "SourceCard" && node.props.label === "B").props.source.source_start, 20);
  assert.equal(ui.nodes.find((node) => node.type?.name === "RecipeControls").props.recipe.id, recipe.id);
  assert.match(text(ui.tree), /Matches working settings/);
  assert.ok(ui.requests.every(({ options }) => !options?.method));
});

test("history previews preserve a draft until Load to refine is explicitly used", async () => {
  const alternative = { ...savedJob, id: "alternative", request: { ...request, outgoing: { ...sourceA, source_start: 11 }, recipe: { ...recipe, duration: .3 } } };
  const ui = await workspaceHarness({ renders: [savedJob, alternative] });
  const card = ui.nodes.find((node) => node.type?.name === "SourceCard" && node.props.label === "A");
  card.props.onChange({ ...card.props.source, source_start: 10.25 }); await flush();
  ui.nodes.find((node) => node.type === "button" && node.props.className === "historyItem" && text(node).includes("9f")).props.onClick(); await flush();
  assert.equal(ui.nodes.find((node) => node.type?.name === "ComparisonPlayer").props.job.id, "alternative");
  assert.equal(ui.nodes.find((node) => node.type?.name === "SourceCard" && node.props.label === "A").props.source.source_start, 10.25);
  assert.match(text(ui.tree), /Working settings changed/);
  ui.button("Load to refine").props.onClick(); await flush();
  assert.equal(ui.nodes.find((node) => node.type?.name === "SourceCard" && node.props.label === "A").props.source.source_start, 11);
  assert.equal(ui.nodes.find((node) => node.type?.name === "RecipeControls").props.recipe.duration, .3);
});

test("late deep-link restoration cannot overwrite an explicitly selected preview or edited source", async () => {
  let resolve;
  const pending = new Promise((done) => { resolve = done; });
  const ui = await workspaceHarness({ renders: [savedJob], linked: "linked", detailHandler: () => pending });
  const card = ui.nodes.find((node) => node.type?.name === "SourceCard" && node.props.label === "A");
  card.props.onChange({ ...sourceA, title: "Draft source", source_start: 10.2 }); await flush();
  ui.nodes.find((node) => node.type === "button" && node.props.className === "historyItem").props.onClick(); await flush();
  resolve({ ...savedJob, id: "linked", request: { ...request, recipe: { ...recipe, duration: .3 } } }); await flush();
  assert.equal(ui.nodes.find((node) => node.type?.name === "ComparisonPlayer").props.job.id, "saved");
  assert.equal(ui.nodes.find((node) => node.type?.name === "SourceCard" && node.props.label === "A").props.source.source_start, 10.2);
});

test("a queued timing sweep keeps the playable preview and opens the center timing when ready", async () => {
  const ui = await workspaceHarness({ renders: [savedJob], postStatus: "queued" });
  ui.nodes.find((node) => node.type === "button" && text(node).startsWith("Render 3 timings")).props.onClick(); await flush();
  const currentPlayer = () => ui.nodes.find((node) => node.type?.name === "ComparisonPlayer");
  assert.equal(currentPlayer().props.job.id, "saved");
  assert.match(text(ui.tree), /Rendering new preview/);
  ui.complete("created-1"); await ui.tick();
  assert.equal(currentPlayer().props.job.id, "saved", "the first shorter timing does not steal the preview");
  ui.complete("created-2"); await ui.tick();
  assert.equal(currentPlayer().props.job.id, "created-2");
  assert.equal(currentPlayer().props.compare.id, "saved");
  ui.complete("created-3"); await ui.tick();
  assert.equal(currentPlayer().props.job.id, "created-2", "the final longer timing does not replace the center");
  assert.equal(ui.nodes.find((node) => node.type?.name === "RecipeControls").props.recipe.duration, .5);
});

test("an explicit preview selection during a sweep wins over its later completion", async () => {
  const baseline = { ...savedJob, id: "baseline", request: { ...request, recipe: { ...recipe, id: "hard-cut", duration: 0 } } };
  const ui = await workspaceHarness({ renders: [savedJob, baseline], postStatus: "queued" });
  ui.nodes.find((node) => node.type === "button" && text(node).startsWith("Render 3 timings")).props.onClick(); await flush();
  ui.nodes.find((node) => node.type === "button" && node.props.className === "historyItem" && text(node).startsWith("Hard cut")).props.onClick(); await flush();
  ui.complete("created-2"); await ui.tick();
  assert.equal(ui.nodes.find((node) => node.type?.name === "ComparisonPlayer").props.job.id, "baseline");
  assert.equal(ui.nodes.find((node) => node.type?.name === "RecipeControls").props.recipe.id, "luma-reveal");
});

test("baseline comparison reuses a matching ready cut and otherwise renders the preview's frozen pair", async () => {
  const baseline = { ...savedJob, id: "baseline", request: { ...request, recipe: { ...recipe, id: "hard-cut", duration: 0 } } };
  const ui = await workspaceHarness({ renders: [savedJob, baseline] });
  ui.button("Compare hard cut").props.onClick(); await flush();
  assert.equal(ui.nodes.find((node) => node.type?.name === "ComparisonPlayer").props.compare.id, "baseline");
  assert.equal(ui.requests.filter(({ options }) => options?.method === "POST").length, 0);
  const fresh = await workspaceHarness({ renders: [savedJob], postStatus: "queued" });
  const card = fresh.nodes.find((node) => node.type?.name === "SourceCard" && node.props.label === "A");
  card.props.onChange({ ...card.props.source, source_start: 10.2 }); await flush();
  fresh.button("Compare hard cut").props.onClick(); await flush();
  const sent = JSON.parse(fresh.requests.find(({ options }) => options?.method === "POST").options.body);
  assert.equal(sent.outgoing.source_start, 10, "comparison uses the saved preview, even while the working pair differs");
  fresh.complete("created-1"); await fresh.tick();
  const player = fresh.nodes.find((node) => node.type?.name === "ComparisonPlayer");
  assert.equal(player.props.job.id, "saved"); assert.equal(player.props.compare.id, "created-1");
  assert.equal(fresh.nodes.find((node) => node.type?.name === "SourceCard" && node.props.label === "A").props.source.source_start, 10.2);
});

test("working differences ignore unused controls and mark older retained results visibly", async () => {
  const definition = { controls: ["duration", "softness"], defaults: recipe };
  assert.deepEqual(plain(helpers.workingChanges(request, { ...request, recipe: { ...recipe, direction: "right" } }, definition)), []);
  assert.deepEqual(plain(helpers.workingChanges(request, { ...request, recipe: { ...recipe, softness: .1 } }, definition)), ["Shape"]);
  const ui = await workspaceHarness({ renders: [savedJob], rendererVersion: "v4" });
  assert.match(text(ui.tree), /Earlier renderer/);
  assert.match(text(ui.tree), /Earlier renderer · render again/);
  assert.equal(helpers.variantTiming({ ...recipe, id: "hard-cut", duration: 0 }), "0f", "timing labels do not repeat the recipe name");
});

test("source scrubbing stays in known media and trim nudges never alter the opposite endpoint", () => {
  assert.deepEqual(plain(helpers.sourceScrubBounds(sourceA, 100)), { start: 9, end: 14 });
  assert.deepEqual(plain(helpers.sourceScrubBounds({ ...sourceA, source_start: 0, source_end: .5 }, .5)), { start: 0, end: .5 });
  const outside = helpers.sourceScrubBounds({ ...sourceA, source_start: 100, source_end: 103 }, 10);
  assert.ok(outside.start >= 0 && outside.start < outside.end && outside.end <= 10);
  assert.deepEqual(plain(helpers.sourceBoundary(sourceA, "source_end", 12.9, 100)), { ...sourceA, source_end: 12.9 });
  assert.equal(helpers.sourceBoundary(sourceA, "source_start", 12.9, 100), null);
  assert.equal(helpers.sourceBoundary(sourceA, "source_end", 14, 13), null);
  assert.equal(helpers.sourceBoundary(sourceA, "source_start", -1, 100), null);
  assert.equal(helpers.sourceBoundary(sourceA, "source_end", 25, 100), null);
  const fractional = { ...sourceA, source_start: 3933.67975, source_end: 3936.098833 };
  assert.equal(helpers.sourceInspectionTime(fractional, true), fractional.source_end - .1, "browser-only fallback is a nearby preview, not an exact endpoint");
  assert.equal(helpers.sourceInspectionTime(fractional, false), fractional.source_start);
  assert.equal(helpers.sourceInspectionTime({ ...sourceA, source_end: 10.01 }, true), sourceA.source_start);
});

test("native endpoint stills follow each source window independently and survive current crop changes", async () => {
  const endpoints = { a: { url: result.frame_a_url, time: result.frame_a_time }, b: { url: result.frame_b_url, time: result.frame_b_time } };
  assert.deepEqual(plain(helpers.matchingSourceEndpoints(savedJob, request)), endpoints);
  for (const changed of [{ ...request, recipe: { ...recipe, duration: .3 } }, { ...request, outgoing: { ...sourceA, framing: { ...helpers.DEFAULT_FRAMING, fit: "fill", zoom: 1.2, anchor_x: .8 } } }, { ...request, output: { ...request.output, aspect: "portrait" } }])
    assert.deepEqual(plain(helpers.matchingSourceEndpoints(savedJob, changed)), endpoints, "native stills have no lab crop, aspect or transition applied");
  assert.deepEqual(plain(helpers.matchingSourceEndpoints(savedJob, { ...request, outgoing: { ...sourceA, source_end: 12.9 } })), { b: endpoints.b });
  assert.deepEqual(plain(helpers.matchingSourceEndpoints(savedJob, { ...request, incoming: { ...sourceB, film_id: "other" } })), { a: endpoints.a });
  assert.equal(helpers.matchingSourceEndpoints(savedJob, { ...request, outgoing: sourceB, incoming: sourceA }), null);
  assert.equal(helpers.matchingSourceEndpoints(savedJob, null), null);
  assert.equal(helpers.matchingSourceEndpoints({ ...savedJob, status: "running" }, request), null);
  const ui = await workspaceHarness({ renders: [savedJob] });
  const cards = () => ui.nodes.filter((node) => node.type?.name === "SourceCard");
  assert.ok(cards().every((card) => card.props.endpoint));
  cards()[0].props.onChange({ ...cards()[0].props.source, framing: { ...helpers.DEFAULT_FRAMING, fit: "fill", anchor_x: .8, zoom: 1.2 } }); await flush();
  assert.ok(cards().every((card) => card.props.endpoint), "dragging the crop preserves the native still in both cards");
  cards()[1].props.onChange({ ...cards()[1].props.source, source_start: 20.1 }); await flush();
  assert.ok(cards()[0].props.endpoint, "trimming B leaves the unchanged A still available");
  assert.equal(cards()[1].props.endpoint, undefined);
});

test("invalid native endpoint receipts cannot hide an independently valid side", () => {
  const b = { b: { url: result.frame_b_url, time: result.frame_b_time } };
  for (const patch of [{ frame_a_url: "" }, { frame_a_url: "   " }, { frame_a_url: null }, { frame_a_time: NaN }, { frame_a_time: Infinity }, { frame_a_time: sourceA.source_start - .001 }, { frame_a_time: sourceA.source_end }])
    assert.deepEqual(plain(helpers.matchingSourceEndpoints({ ...savedJob, result: { ...result, ...patch } }, request)), b);
  const a = { a: { url: result.frame_a_url, time: result.frame_a_time } };
  for (const patch of [{ frame_b_url: "" }, { frame_b_url: 42 }, { frame_b_time: NaN }, { frame_b_time: sourceB.source_start - .001 }, { frame_b_time: sourceB.source_end }])
    assert.deepEqual(plain(helpers.matchingSourceEndpoints({ ...savedJob, result: { ...result, ...patch } }, request)), a);
  assert.equal(helpers.matchingSourceEndpoints({ ...savedJob, result: { ...result, frame_a_url: "", frame_b_time: NaN } }, request), null);
});

test("history refresh keeps new jobs, terminal progress and newest-first ordering", () => {
  const newer = { ...savedJob, id: "new", created_at: 200 };
  const older = { ...savedJob, id: "old", created_at: 100 };
  const merged = helpers.mergeRenderHistory([older, newer], [{ ...older, status: "running" }], true);
  assert.deepEqual(plain(merged.map((item) => [item.id, item.status])), [["new", "completed"], ["old", "completed"]]);
});

test("framing is persisted, compared against legacy defaults and validated before submission", () => {
  const framing = { fit: "fill", anchor_x: .3, anchor_y: .8, zoom: 1.25 };
  assert.deepEqual(plain(helpers.sourcePayload({ ...sourceA, framing, title: "A" })), { ...sourceA, framing });
  assert.equal(helpers.samePair(request, { ...request, outgoing: { ...sourceA, framing: helpers.DEFAULT_FRAMING } }), true);
  for (const patch of [{ fit: "fill" }, { anchor_x: .3 }, { anchor_y: .8 }, { zoom: 1.25 }]) {
    assert.equal(helpers.samePair(request, { ...request, outgoing: { ...sourceA, framing: { ...helpers.DEFAULT_FRAMING, ...patch } } }), false);
  }
  for (const patch of [{ fit: "stretch" }, { anchor_x: NaN }, { anchor_y: 1.1 }, { zoom: .9 }, { zoom: Infinity }]) {
    assert.match(helpers.validatePair({ ...sourceA, framing: { ...framing, ...patch } }, sourceB, recipe), /framing/);
  }
});

test("beat timing and bounded sweeps preserve source handles and deduplicate limits", () => {
  assert.equal(helpers.beatFrames(120, .5), 8);
  assert.equal(helpers.beatFrames(180, 1 / 3), 3);
  assert.equal(helpers.beatFrames(20, 2), 60);
  for (const bpm of [NaN, Infinity, 0, -1]) assert.equal(helpers.beatFrames(bpm, .5), 0);
  const before = JSON.stringify(request);
  const sweep = helpers.timingSweep(request);
  assert.deepEqual(plain(sweep.map((variant) => Math.round(variant.recipe.duration * 30))), [13, 15, 17]);
  assert.equal(JSON.stringify(request), before);
  assert.ok(sweep.every((variant) => helpers.samePair(variant, request)));
  assert.deepEqual(plain(helpers.timingSweep({ ...request, recipe: { ...recipe, duration: .1 } }).map((variant) => Math.round(variant.recipe.duration * 30))), [3, 5]);
  assert.deepEqual(plain(helpers.timingSweep({ ...request, recipe: { ...recipe, duration: 2 } }).map((variant) => Math.round(variant.recipe.duration * 30))), [58, 60]);
  const short = { ...request, outgoing: { ...sourceA, source_end: 10.5 } };
  assert.deepEqual(plain(helpers.timingSweep(short).map((variant) => Math.round(variant.recipe.duration * 30))), [13]);
  assert.equal(helpers.timingSweep({ ...request, recipe: { ...recipe, id: "hard-cut" } }).length, 0);
});

test("one seam-relative phase aligns different transition durations and clamps each preview independently", () => {
  const longer = { ...savedJob, id: "longer", result: { ...result, duration: 10, transition_start: 6, transition_end: 8 } };
  assert.equal(helpers.seamCenter(savedJob), 2.75);
  assert.equal(helpers.phaseToMedia(savedJob, 0), 2.75);
  assert.equal(helpers.phaseToMedia(longer, 0), 7);
  assert.equal(helpers.phaseToMedia(savedJob, -4), 0);
  assert.equal(helpers.phaseToMedia(longer, -4), 3);
  assert.equal(helpers.phaseToMedia(savedJob, 4), result.duration - 1 / 30);
  assert.equal(helpers.phaseToMedia(longer, 4), 10 - 1 / 30);
  assert.deepEqual(plain(helpers.comparisonBounds([savedJob, longer], true)), { start: -1.65, end: 1.65 });
  const full = helpers.comparisonBounds([savedJob, longer], false);
  assert.equal(full.start, -7);
  assert.equal(full.end, 10 - 1 / 30 - 7);
});

test("a timing sweep submits exactly three frozen variants and reports partial enqueue failures", async () => {
  for (const failureAt of [0, 2]) {
    const ui = await workspaceHarness({ renders: [savedJob], postFailureAt: failureAt });
    ui.button("Load to refine").props.onClick(); await flush();
    const sweep = ui.nodes.find((node) => node.type === "button" && text(node).startsWith("Render 3 timings"));
    assert.equal(sweep.props.disabled, false);
    sweep.props.onClick(); await flush();
    const posts = ui.requests.filter(({ options }) => options?.method === "POST");
    assert.equal(posts.length, failureAt || 3);
    assert.deepEqual(posts.map(({ options }) => Math.round(JSON.parse(options.body).recipe.duration * 30)), failureAt ? [13, 15] : [13, 15, 17]);
    assert.ok(posts.every(({ options }) => helpers.samePair(JSON.parse(options.body), request)));
    assert.match(text(ui.tree), failureAt ? /1 variant already queued.*Render queue unavailable/ : /3 timing variants queued/);
  }
});

test("favorites, variant notes and named recipes stay browser-local and recipes reload a frozen source pair", async () => {
  const ui = await workspaceHarness({ renders: [savedJob] });
  ui.button("Load to refine").props.onClick(); await flush();
  ui.button("☆ Favorite variant").props.onClick(); await flush();
  assert.ok(ui.button("★ Favorited"));
  const notes = ui.nodes.find((node) => node.props?.["aria-label"] === "Variant notes");
  notes.props.onChange({ target: { value: "Needs a quicker hit" } }); await flush();
  assert.equal(ui.nodes.find((node) => node.props?.["aria-label"] === "Variant notes").props.value, "Needs a quicker hit");
  ui.nodes.find((node) => node.props?.["aria-label"] === "Saved recipe name").props.onChange({ target: { value: "Favorite hit" } }); await flush();
  ui.button("Save in this browser").props.onClick(); await flush();
  const sourceCard = ui.nodes.find((node) => node.type?.name === "SourceCard" && node.props.label === "A");
  sourceCard.props.onChange({ ...sourceCard.props.source, source_start: 11 }); await flush();
  ui.nodes.find((node) => node.type === "button" && text(node).startsWith("Favorite hit")).props.onClick(); await flush();
  assert.equal(ui.nodes.find((node) => node.type?.name === "SourceCard" && node.props.label === "A").props.source.source_start, 10);
  assert.ok(ui.requests.every(({ options }) => options?.method !== "POST"));
});

test("shared speed survives cold restoration, effect changes, sweeps and browser-saved recipes", async () => {
  const retime = { ...helpers.DEFAULT_RETIME, mode: "slow-hit", speed: .5, span: .8, curve: "snappy", interpolation: "blend" };
  const ui = await workspaceHarness({ renders: [{ ...savedJob, request: { ...request, retime } }] });
  const speed = () => ui.nodes.find((node) => node.type?.name === "SpeedControls");
  assert.deepEqual(plain(speed().props.value), retime);
  speed().props.onChange({ ...retime, span: .7 }); await flush();
  assert.match(text(ui.tree), /Working settings changed · speed/);
  const controls = ui.nodes.find((node) => node.type?.name === "RecipeControls");
  controls.props.onChange({ ...controls.props.recipe, duration: .3 }); await flush();
  assert.equal(speed().props.value.span, .7, "changing an effect never resets shared speed");
  ui.nodes.find((node) => node.type === "button" && text(node).startsWith("Render 3 timings")).props.onClick(); await flush();
  const posted = ui.requests.filter(({ options }) => options?.method === "POST").map(({ options }) => JSON.parse(options.body));
  assert.equal(posted.length, 3);
  assert.ok(posted.every((item) => item.retime.span === .7 && item.retime.interpolation === "blend"));
  ui.nodes.find((node) => node.props?.["aria-label"] === "Saved recipe name").props.onChange({ target: { value: "Slow favorite" } }); await flush();
  ui.button("Save in this browser").props.onClick(); await flush();
  speed().props.onChange({ ...helpers.DEFAULT_RETIME }); await flush();
  ui.nodes.find((node) => node.type === "button" && text(node).startsWith("Slow favorite")).props.onClick(); await flush();
  assert.deepEqual(plain(speed().props.value), { ...retime, span: .7 });
  assert.equal(helpers.recipeTitle("focus-pull"), "Defocus bridge");
  assert.equal(helpers.recipeTitle("prism-push"), "Prism push");
});

test("comparisons isolate the effect or speed and freeze the rendered settings", async () => {
  const retime = { ...helpers.DEFAULT_RETIME, mode: "rush", speed: 2 };
  const selected = { ...savedJob, request: { ...request, retime } };
  const original = { ...savedJob, id: "original" };
  const fast = { ...original, id: "fast", request: { ...original.request, recipe: { ...recipe, id: "hard-cut", duration: 0 }, retime } };
  const ui = await workspaceHarness({ renders: [selected, original, fast] });
  ui.button("Compare hard cut · same speed").props.onClick(); await flush();
  assert.equal(ui.nodes.find((node) => node.type?.name === "ComparisonPlayer").props.compare.id, "fast");
  ui.button("Compare without speed").props.onClick(); await flush();
  assert.equal(ui.nodes.find((node) => node.type?.name === "ComparisonPlayer").props.compare.id, "original");
  assert.ok(ui.requests.every(({ options }) => options?.method !== "POST"));
  const fresh = await workspaceHarness({ renders: [selected, original], postStatus: "queued" });
  fresh.nodes.find((node) => node.type?.name === "SpeedControls").props.onChange({ ...helpers.DEFAULT_RETIME }); await flush();
  fresh.button("Compare hard cut · same speed").props.onClick(); await flush();
  const sent = JSON.parse(fresh.requests.find(({ options }) => options?.method === "POST").options.body);
  assert.deepEqual(sent.retime, retime, "baseline uses the saved preview's speed, not a changed draft or an existing original-speed cut");
  const without = await workspaceHarness({ renders: [selected, { ...original, id: "wrong-duration", request: { ...request, recipe: { ...recipe, duration: .3 } } }] });
  without.button("Compare without speed").props.onClick(); await flush();
  const unchangedEffect = JSON.parse(without.requests.find(({ options }) => options?.method === "POST").options.body);
  assert.deepEqual(unchangedEffect.recipe, selected.request.recipe, "removing speed preserves the frozen effect including its transition duration");
  assert.deepEqual(unchangedEffect.retime, plain(helpers.DEFAULT_RETIME));
});

test("rendered speed receipt reports cut sampling separately from requested edge speed", () => {
  const faster = { ...savedJob, request: { ...request, retime: { ...helpers.DEFAULT_RETIME, mode: "rush", speed: 4 } }, result: { ...result, retiming: { visible_cut: { at_picture_cut: [{ side: "outgoing", sample_grid_speed: 1.234 }, { side: "incoming", sample_grid_speed: 1.75 }] } } } };
  assert.equal(helpers.cutSpeedTitle(faster), "At cut A 1.23× / B 1.75×");
  assert.equal(helpers.cutSpeedTitle(savedJob), null);
  assert.equal(helpers.cutSpeedTitle({ ...faster, result }), null);
});

test("saved export size and dimensions come from the completed file rather than a guessed estimate", () => {
  assert.equal(helpers.renderFileSummary({ ...savedJob, result: { ...result, width: 1920, height: 1080 }, storage: { output_bytes: 2.5 * 1024 * 1024 } }), "1920×1080 · 5.50s · 2.5 MiB");
  assert.equal(helpers.fileSize(undefined), null); assert.equal(helpers.fileSize(NaN), null); assert.equal(helpers.fileSize(-1), null);
  assert.equal(helpers.renderFileSummary({ ...savedJob, storage: null }), "5.50s", "legacy missing size is not presented as zero");
  assert.equal(helpers.qualityTitle("high"), "Review · 720p"); assert.equal(helpers.qualityTitle("export"), "Export · 1080p");
});

test("1080p copy uses the saved request and reports completed reuse without duplicating history", async () => {
  const exported = { ...savedJob, id: "exported", request: { ...request, output: { ...request.output, quality: "export" } }, result: { ...result, width: 1920, height: 1080 }, storage: { output_bytes: 2 * 1024 * 1024 }, reused: true };
  const ui = await workspaceHarness({ renders: [savedJob, exported], postHandler: () => exported });
  const card = ui.nodes.find((node) => node.type?.name === "SourceCard" && node.props.label === "A");
  card.props.onChange({ ...card.props.source, source_start: 11 }); await flush();
  ui.nodes.find((node) => node.type?.name === "SpeedControls").props.onChange({ ...helpers.DEFAULT_RETIME, mode: "rush", speed: 2 }); await flush();
  ui.button("Render 1080p copy").props.onClick(); await flush();
  const posts = ui.requests.filter(({ options }) => options?.method === "POST");
  assert.equal(posts.length, 1);
  assert.deepEqual(JSON.parse(posts[0].options.body), exported.request, "working edits cannot alter the saved export copy");
  assert.match(text(ui.tree), /Reused the matching saved render/);
  assert.match(text(ui.tree), /1920×1080 · 5.50s · 2.0 MiB/);
  assert.equal(ui.button("Render 1080p copy"), undefined, "an existing export does not offer a redundant export upgrade");
  assert.equal(ui.nodes.find((node) => node.type?.name === "ComparisonPlayer").props.job.id, "exported");
  assert.match(text(ui.tree), /Most recent 2 renders/);
});

test("corrupt browser storage drops malformed recipes and bounds notes without crashing", () => {
  const notebook = {};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "notebook.ts"), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText,
    { exports: notebook, require: () => helpers });
  for (const raw of [null, "{", "null", "[]", '{"schema_version":2}']) assert.deepEqual(plain(notebook.parseNotebook(raw)), plain(notebook.emptyNotebook()));
  const saved = { id: "one", name: "Hit", request, saved_at: 100 };
  const parsed = notebook.parseNotebook(JSON.stringify({ schema_version: 1, variants: { saved: { favorite: true, note: "x".repeat(3000) } }, recipes: [saved,
    { ...saved, id: "invalid", request: { ...request, outgoing: { ...sourceA, framing: { fit: "fill" } } } }, { ...saved, id: "bad-output", request: { ...request, output: [] } }, { ...saved, id: "bad-speed", request: { ...request, retime: { ...helpers.DEFAULT_RETIME, mode: "rush", speed: .25 } } }] }));
  assert.equal(parsed.variants.saved.note.length, 2000);
  assert.equal(parsed.variants.saved.favorite, true);
  assert.deepEqual(plain(parsed.recipes), [saved]);
  const retimed = { ...saved, request: { ...request, output: { ...request.output, quality: "export" }, retime: { ...plain(helpers.DEFAULT_RETIME), mode: "rush", speed: 2, interpolation: "flow" } } };
  assert.deepEqual(plain(notebook.parseNotebook(JSON.stringify({ schema_version: 1, recipes: [retimed] })).recipes), [retimed]);
});

test("saved speed labels distinguish interpolation without changing original or nearest labels", () => {
  assert.equal(helpers.speedTitle(), "Original speed");
  assert.equal(helpers.speedTitle(helpers.DEFAULT_RETIME), "Original speed");
  const slow = { ...helpers.DEFAULT_RETIME, mode: "slow-hit", speed: .5 };
  assert.equal(helpers.speedTitle(slow), "Slow hit 0.5×");
  assert.equal(helpers.speedTitle({ ...slow, interpolation: "blend" }), "Slow hit 0.5× · frame blend");
  assert.equal(helpers.speedTitle({ ...slow, interpolation: "flow" }), "Slow hit 0.5× · optical flow");
  assert.equal(helpers.speedTitle({ ...slow, mode: "rush", speed: 2, interpolation: "flow" }), "Rush 2× · optical flow");
});

test("shared transport waits for decoded seek targets and cleans listeners on success or failure", async () => {
  const transport = {};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "transport.ts"), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText,
    { exports: transport, setTimeout, clearTimeout });
  class Media extends EventTarget { readyState = 1; seeking = true; }
  const media = new Media(); let resolved = false;
  const awaited = transport.waitForMedia(media, 1000).then(() => { resolved = true; });
  media.readyState = 2; media.dispatchEvent(new Event("loadeddata")); await flush();
  assert.equal(resolved, false, "decoded data from before the target seek is insufficient");
  media.seeking = false; media.dispatchEvent(new Event("seeked")); await awaited;
  assert.equal(resolved, true);
  const missing = new Media();
  await assert.rejects(transport.waitForMedia(missing, 5), /still loading/);
  const failed = new Media(); const promise = transport.waitForMedia(failed, 1000);
  failed.dispatchEvent(new Event("error")); await assert.rejects(promise, /still loading/);
  const playable = new Media(); playable.seeking = false; playable.readyState = 2;
  const cancelled = new AbortController(); const future = transport.waitForMedia(playable, 1000, cancelled.signal, 3);
  cancelled.abort(); await assert.rejects(future, /cancelled/);
  let futureReady = false;
  const readyForPlay = transport.waitForMedia(playable, 1000, undefined, 3).then(() => { futureReady = true; });
  playable.dispatchEvent(new Event("loadeddata")); await flush(); assert.equal(futureReady, false, "a current image alone is not enough to advance the playback clock");
  playable.readyState = 3; playable.dispatchEvent(new Event("canplay")); await readyForPlay;
});

test("top-level AI mode preserves the working draft and pauses local previews without enqueueing", async () => {
  const ui = await workspaceHarness({ renders: [savedJob] });
  const source = ui.nodes.find(node => node.type?.name === "SourceCard" && node.props.label === "A");
  source.props.onChange({ ...source.props.source, source_start: 10.25 }); await flush();
  ui.nodes.find(node => node.props?.id === "ai-tab").props.onClick(); await flush();
  assert.equal(ui.nodes.find(node => node.props?.id === "local-panel").props.hidden, true);
  assert.equal(ui.nodes.find(node => node.props?.id === "ai-panel").props.hidden, false);
  assert.equal(ui.nodes.find(node => node.type?.name === "ComparisonPlayer").props.active, false);
  assert.ok(ui.nodes.filter(node => node.type?.name === "SourceCard").every(node => node.props.disabled));
  const ai = ui.nodes.find(node => node.type === "./AIHandoff");
  assert.equal(ai.props.active, true); assert.equal(ai.props.job.id, "saved"); assert.equal(ai.props.sourcePairChanged, true);
  assert.match(text(ui.tree), /Working pair differs from the prepared pair/);
  ui.nodes.find(node => node.props?.id === "local-tab").props.onClick(); await flush();
  assert.equal(ui.nodes.find(node => node.type?.name === "SourceCard" && node.props.label === "A").props.source.source_start, 10.25);
  assert.equal(ui.nodes.find(node => node.type === "./AIHandoff").props.active, false);
  assert.equal(ui.requests.filter(({ options }) => options?.method === "POST").length, 0);
});

test("saved render history starts compact and reveals older items without changing the preview", async () => {
  const renders = Array.from({ length: 25 }, (_, index) => ({ ...savedJob, id: `history-${index}` }));
  const ui = await workspaceHarness({ renders });
  const items = () => ui.nodes.filter(node => node.type === "button" && node.props.className === "historyItem");
  const drawer = ui.nodes.find(node => node.type === "details" && node.props.className === "historyDrawer");
  assert.equal(!!drawer.props.open, false); assert.equal(items().length, 6);
  ui.nodes.find(node => node.type === "button" && node.props.className === "showMore").props.onClick(); await flush();
  assert.equal(items().length, 18);
  assert.equal(ui.nodes.find(node => node.type?.name === "ComparisonPlayer").props.job.id, "history-0");
  ui.nodes.find(node => node.type === "button" && node.props.className === "showMore").props.onClick(); await flush();
  assert.equal(items().length, 25);
  assert.equal(ui.requests.filter(({ options }) => options?.method === "POST").length, 0);
});

test("AI preparation freezes a draft cut at original speed without rewriting local effect settings", async () => {
  const speed = { mode: "rush", speed: 2, span: .5, curve: "smooth", interpolation: "nearest" };
  const parent = { ...savedJob, request: { ...request, retime: speed, output: { aspect: "portrait", quality: "high" } } };
  const ui = await workspaceHarness({ renders: [parent], postStatus: "queued" });
  const source = ui.nodes.find(node => node.type?.name === "SourceCard" && node.props.label === "A");
  source.props.onChange({ ...source.props.source, source_start: 10.25 }); await flush();
  ui.nodes.find(node => node.props?.id === "ai-tab").props.onClick(); await flush();
  const prepare = ui.button("Prepare working pair").props.onClick; prepare(); prepare(); await flush();
  const posts = ui.requests.filter(({ options }) => options?.method === "POST"); assert.equal(posts.length, 1);
  const sent = JSON.parse(posts[0].options.body);
  assert.equal(sent.recipe.id, "hard-cut"); assert.equal(sent.recipe.duration, 0);
  assert.equal(sent.retime.mode, "off"); assert.equal(sent.retime.speed, 1);
  assert.equal(sent.output.quality, "draft"); assert.equal(sent.output.aspect, "portrait"); assert.equal(sent.outgoing.source_start, 10.25);
  assert.equal(ui.nodes.find(node => node.type?.name === "RecipeControls").props.recipe.id, recipe.id);
  assert.equal(ui.nodes.find(node => node.type?.name === "SpeedControls").props.value.mode, "rush");
  assert.equal(ui.nodes.find(node => node.type === "./AIHandoff").props.job.id, "saved");
  ui.complete("created-1"); await ui.tick();
  assert.equal(ui.nodes.find(node => node.type === "./AIHandoff").props.job.id, "created-1");
  assert.equal(ui.nodes.find(node => node.type === "./AIHandoff").props.sourcePairChanged, false);
  assert.equal(ui.nodes.find(node => node.type?.name === "RecipeControls").props.recipe.id, recipe.id);
  assert.equal(ui.nodes.find(node => node.type?.name === "SpeedControls").props.value.mode, "rush");
  const qualityLabel = ui.nodes.find(node => node.type === "label" && text(node).startsWith("Quality"));
  assert.equal(nodes(qualityLabel).find(node => node.type === "select").props.value, "high");
});

test("explicit preview selection wins over an AI preparation that completes later", async () => {
  const parent = { ...savedJob, request: { ...request, retime: { mode: "rush", speed: 2, span: .5, curve: "smooth", interpolation: "nearest" } } };
  const alternative = { ...savedJob, id: "alternative", request: { ...request, recipe: { ...recipe, duration: .3 } } };
  const ui = await workspaceHarness({ renders: [parent, alternative], postStatus: "queued" });
  ui.nodes.find(node => node.props?.id === "ai-tab").props.onClick(); await flush();
  ui.button("Prepare working pair").props.onClick(); await flush();
  ui.nodes.find(node => node.props?.id === "local-tab").props.onClick(); await flush();
  ui.nodes.find(node => node.type === "button" && node.props.className === "historyItem" && text(node).includes("9f")).props.onClick(); await flush();
  ui.complete("created-1"); await ui.tick();
  assert.equal(ui.nodes.find(node => node.type === "./AIHandoff").props.job.id, "alternative");
  assert.equal(ui.nodes.find(node => node.type?.name === "RecipeControls").props.recipe.duration, .5);
});

test("an AI entry link opens the AI flow directly without preparing or generating automatically", async () => {
  const ui = await workspaceHarness({ renders: [savedJob], initialMode: "ai" });
  assert.equal(ui.nodes.find(node => node.props?.id === "ai-panel").props.hidden, false);
  assert.equal(ui.nodes.find(node => node.props?.id === "local-panel").props.hidden, true);
  assert.equal(ui.nodes.find(node => node.type === "./AIHandoff").props.job.id, "saved");
  assert.equal(ui.requests.filter(({ options }) => options?.method === "POST").length, 0);
});

test("one monitor follows timeline clip selection and pauses the other media", async () => {
  const ui = await workspaceHarness({ renders: [savedJob] });
  const timeline = () => ui.nodes.find((node) => node.type?.name === "PairTimeline");
  const cards = () => ui.nodes.filter((node) => node.type?.name === "SourceCard");
  const player = () => ui.nodes.find((node) => node.type?.name === "ComparisonPlayer");
  assert.ok(cards().every((node) => node.props.disabled && node.props.monitor));
  assert.ok(player().props.active); assert.ok(player().props.hideScrubber);
  timeline().props.onSelect("a"); await flush();
  assert.equal(timeline().props.selectedSide, "a"); assert.equal(player().props.active, false);
  assert.equal(cards()[0].props.disabled, false); assert.equal(cards()[1].props.disabled, true);
  timeline().props.onSeek("b", 20.5); await flush();
  assert.equal(cards()[0].props.disabled, true); assert.equal(cards()[1].props.disabled, false);
  assert.equal(cards()[1].props.inspection.time, 20.5);
  assert.equal(cards()[1].props.inspection.film, sourceB.film_id);
  timeline().props.onSelectTransition(); await flush();
  assert.ok(cards().every((node) => node.props.disabled)); assert.ok(player().props.active);
});

test("timeline trimming previews the edited source without changing the saved video", async () => {
  const ui = await workspaceHarness({ renders: [savedJob] });
  const timeline = () => ui.nodes.find((node) => node.type?.name === "PairTimeline");
  timeline().props.onTrim("a", "source_end", 12.5); await flush();
  assert.equal(timeline().props.a.source_start, 10); assert.equal(timeline().props.a.source_end, 12.5);
  assert.equal(timeline().props.b.source_start, sourceB.source_start);
  assert.equal(timeline().props.selectedSide, "a");
  assert.match(text(ui.tree), /Preview needs update/);
  assert.equal(ui.nodes.find((node) => node.type?.name === "ComparisonPlayer").props.job.request.outgoing.source_end, 13);
  assert.equal(timeline().props.onPreviewSeek, undefined);
  assert.equal(ui.requests.filter(({ options }) => options?.method === "POST").length, 0);
});

test("one timeline seeks the rendered video only while it matches working settings", async () => {
  const ui = await workspaceHarness({ renders: [savedJob] });
  const timeline = () => ui.nodes.find((node) => node.type?.name === "PairTimeline");
  const player = () => ui.nodes.find((node) => node.type?.name === "ComparisonPlayer");
  timeline().props.onPreviewSeek(1.25); await flush();
  assert.equal(player().props.externalSeek.time, 1.25); assert.equal(player().props.externalSeek.jobId, "saved");
  player().props.onTimeChange(1.5); await flush(); assert.equal(timeline().props.playbackTime, 1.5);
  timeline().props.onSelectTransition(); await flush();
  assert.equal(player().props.externalSeek.time, helpers.seamCenter(savedJob));
  timeline().props.onDuration(.3); await flush();
  assert.equal(timeline().props.recipe.duration, .3); assert.equal(timeline().props.onPreviewSeek, undefined);
  assert.equal(timeline().props.playbackTime, null);
  assert.equal(timeline().props.a.source_start, sourceA.source_start);
  assert.equal(ui.requests.filter(({ options }) => options?.method === "POST").length, 0);
});
