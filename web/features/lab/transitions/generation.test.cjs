const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const crypto = require("node:crypto").webcrypto;
const compile = (file, jsx = false) => ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, ...(jsx ? { jsx: ts.JsxEmit.ReactJSX } : {}) },
}).outputText;
const helpers = {};
vm.runInNewContext(compile("generation.ts"), { exports: helpers });
const bridgeHelpers = {};
const directions = {};
vm.runInNewContext(compile("directions.ts"), { exports: directions });
vm.runInNewContext(compile("bridges.ts"), { exports: bridgeHelpers, require: () => directions });
const plain = (value) => JSON.parse(JSON.stringify(value));
const draft = { parent_render_id: "11111111-1111-4111-8111-111111111111", prompt: "Move through the light", duration: 4, resolution: "720p", seed: 42 };
const provider = { provider: "runway", model: "seedance2_5", configured: true, generation_enabled: true, live_verified: false, credential_environment: "RUNWAYML_API_SECRET" };
const makeQuote = (request, patch = {}) => ({ ...provider, model: request.model ?? "seedance2_5", request, estimated_credits: 120, estimated_usd: 1.2, price_version: "price-v1", parent_manifest_sha256: "a".repeat(64), within_request_ceiling: true, aspect: "landscape", ratio: "1280:720", ...patch });
const review = { key: helpers.generationKey(draft), request_id: "22222222-2222-4222-8222-222222222222", quote: makeQuote(helpers.quoteRequest(draft)) };
const source = { id: draft.parent_render_id, status: "completed", request: {}, result: { preview_url: "/parent.mp4" } };
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };

test("review binds every setting and freezes request identity, source receipt and reviewed amount", () => {
  const payload = helpers.generationRequest(draft, review);
  assert.deepEqual(plain(payload), { ...draft, request_id: review.request_id, max_credits: 120, price_version: "price-v1", parent_manifest_sha256: "a".repeat(64), confirm_spend: true });
  assert.equal("seed" in helpers.quoteRequest(draft), false, "seed does not change the quote endpoint contract");
  for (const patch of [{ prompt: `${draft.prompt} ` }, { parent_render_id: "another" }, { duration: 5 }, { resolution: "480p" }, { seed: 43 }])
    assert.throws(() => helpers.generationRequest({ ...draft, ...patch }, review), /fresh quote/);
  assert.throws(() => helpers.generationRequest(draft, { ...review, quote: { ...review.quote, request: { ...review.quote.request, prompt: "Different quote" } } }), /fresh quote/);
});

test("invalid seeds, durations, estimates, missing configuration and incomplete receipts cannot generate", () => {
  for (const patch of [{ seed: NaN }, { seed: .5 }, { seed: 4294967296 }, { duration: 3 }, { duration: 4.5 }, { duration: 9 }, { prompt: " " }, { resolution: "4k" }])
    assert.throws(() => helpers.quoteRequest({ ...draft, ...patch }));
  for (const patch of [{ estimated_credits: 301 }, { estimated_credits: NaN }, { estimated_usd: 3.01 }, { estimated_usd: Infinity }, { within_request_ceiling: false }, { configured: false }, { generation_enabled: false }, { parent_manifest_sha256: "bad" }, { price_version: "" }])
    assert.throws(() => helpers.generationRequest(draft, { ...review, quote: { ...review.quote, ...patch } }));
  assert.equal(helpers.generationRejected({ status: 422 }), true);
  assert.equal(helpers.generationRejected({ status: 500 }), false);
  assert.equal(helpers.generationRejected(new TypeError("Network failed")), false);
  assert.throws(() => helpers.generationRequest(draft, { ...review, quote: { ...review.quote, configured: false, estimated_credits: 544, estimated_usd: 5.44, within_request_ceiling: false } }), /\$3.00/);
});

async function harness({ configured = true, active = true, quoteHandler, postHandler, historyHandler, initialGenerations = [], cancelHandler, providerPatch = {}, onAdjustTiming } = {}) {
  const hooks = [], effects = [], exported = {}, requests = [], jobs = new Map(), timers = new Map();
  let cursor = 0, scheduled = false, tree, timerId = 0;
  let props = { job: source, active, prompt: draft.prompt, onAdjustTiming };
  for (const item of initialGenerations) jobs.set(item.id, item);
  const same = (a, b) => a && b && a.length === b.length && a.every((item, index) => Object.is(item, b[index]));
  const schedule = () => { if (!scheduled) { scheduled = true; queueMicrotask(render); } };
  const react = {
    useState(initial) { const index = cursor++; hooks[index] ??= { value: typeof initial === "function" ? initial() : initial }; return [hooks[index].value, (change) => { const next = typeof change === "function" ? change(hooks[index].value) : change; if (!Object.is(next, hooks[index].value)) { hooks[index].value = next; schedule(); } }]; },
    useRef(initial) { return hooks[cursor++] ??= { current: initial }; },
    useCallback(callback, deps) { const index = cursor++; if (!hooks[index] || !same(hooks[index].deps, deps)) hooks[index] = { value: callback, deps }; return hooks[index].value; },
    useEffect(effect, deps) { const index = cursor++; if (!hooks[index] || !same(hooks[index].deps, deps)) { const cleanup = hooks[index]?.cleanup; hooks[index] = { deps, cleanup }; effects.push(() => { hooks[index].cleanup?.(); hooks[index].cleanup = effect(); }); } },
  };
  vm.runInNewContext(compile("GenerationPanel.tsx", true), {
    exports: exported, AbortController, crypto, Error,
    setTimeout(callback) { timers.set(++timerId, callback); return timerId; }, clearTimeout(id) { timers.delete(id); },
    require(name) {
      if (name === "react") return react;
      if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }), Fragment: "fragment" };
      if (name === "./generation") return helpers;
      if (name === "./bridges") return bridgeHelpers;
      if (name === "@/lib/lab") return { mediaUrl: (value) => value, labRequest: async (route, options) => {
        requests.push({ route, options });
        if (route === "/transitions/providers") return { ...provider, configured, ...providerPatch };
        if (route === "/transitions/bridges/quote") { const request = JSON.parse(options.body); return quoteHandler ? quoteHandler(request) : makeQuote(request, { configured }); }
        if (route === "/transitions/generations") {
          const request = JSON.parse(options.body);
          const count = requests.filter((item) => item.route === route).length;
          if (postHandler) await postHandler(request, count);
          const job = { id: request.request_id, status: "queued", request };
          jobs.set(job.id, job); return job;
        }
        if (route.startsWith("/transitions/generations?")) return historyHandler ? historyHandler() : { generations: initialGenerations };
        if (route.endsWith("/cancel")) { const id = route.split("/").at(-2); if (cancelHandler) await cancelHandler(id); const updated = { ...jobs.get(id), cancel_requested: true }; jobs.set(id, updated); return updated; }
        const id = route.split("/").at(-1);
        if (jobs.has(id)) return jobs.get(id);
        throw Object.assign(new Error("Not found"), { status: 404 });
      } };
      return { default: new Proxy({}, { get: (_, key) => key }) };
    },
  });
  function render() { cursor = 0; scheduled = false; tree = exported.default(props); effects.splice(0).forEach((effect) => effect()); }
  render(); await flush();
  return {
    requests, get tree() { return tree; }, get nodes() { return nodes(tree); },
    button(label) { return nodes(tree).find((node) => node.type === "button" && text(node).trim() === label); },
    generate() { return nodes(tree).find((node) => node.type === "button" && text(node).startsWith("Generate · estimated")); },
    async update(patch) { props = { ...props, ...patch }; schedule(); await flush(); },
    async change(label, value) { const node = nodes(tree).find((item) => item.props?.["aria-label"] === label); node.props.onChange({ target: { value, valueAsNumber: Number(value) } }); await flush(); },
  };
}

test("entry and readiness only read; missing credentials still allow local quotes but never generation", async () => {
  const hidden = await harness({ active: false });
  assert.equal(hidden.requests.length, 0);
  const ui = await harness({ configured: false });
  assert.ok(ui.requests.every(({ options }) => !options?.method));
  ui.button("Review generation quote").props.onClick(); await flush();
  assert.ok(ui.generate()); assert.equal(ui.generate().props.disabled, true);
  ui.generate().props.onClick(); await flush();
  assert.equal(ui.requests.filter(({ route }) => route === "/transitions/generations").length, 0);
  assert.match(text(ui.tree), /RUNWAYML_API_SECRET/);
});

test("retimed source blocks quotes and stale submit handlers while off sources can quote again", async () => {
  const ui = await harness();
  ui.button("Review generation quote").props.onClick(); await flush();
  const savedGenerate = ui.generate().props.onClick;
  await ui.update({ job: { ...source, request: { retime: { mode: "rush" } } } });
  assert.equal(ui.button("Review generation quote").props.disabled, true);
  assert.match(text(ui.tree), /Render a version with Speed off/);
  savedGenerate(); ui.button("Review generation quote").props.onClick(); await flush();
  assert.equal(ui.requests.filter(({ route }) => route === "/transitions/generations").length, 0);
  assert.equal(ui.requests.filter(({ route }) => route === "/transitions/bridges/quote").length, 1);
  await ui.update({ job: { ...source, request: { retime: { mode: "off" } } } });
  assert.equal(ui.button("Review generation quote").props.disabled, false);
});

test("duplicate quote clicks coalesce and responses arriving after a parent or prompt change are discarded", async () => {
  for (const patch of [{ prompt: "New direction" }, { job: { ...source, id: "new-parent" } }]) {
    const pending = deferred(); const ui = await harness({ quoteHandler: () => pending.promise });
    const click = ui.button("Review generation quote").props.onClick; click(); click(); await flush();
    assert.equal(ui.requests.filter(({ route }) => route === "/transitions/bridges/quote").length, 1);
    const original = JSON.parse(ui.requests.find(({ route }) => route === "/transitions/bridges/quote").options.body);
    await ui.update(patch); pending.resolve(makeQuote(original)); await flush();
    assert.equal(ui.generate(), undefined);
    assert.equal(ui.button("Review generation quote").props.disabled, false);
  }
});

test("completed quotes invalidate immediately on duration, resolution and seed edits", async () => {
  for (const [label, value] of [["Generation duration", "5"], ["Generation resolution", "480p"], ["Generation seed", "43"]]) {
    const ui = await harness(); ui.button("Review generation quote").props.onClick(); await flush();
    assert.ok(ui.generate()); await ui.change(label, value);
    assert.equal(ui.generate(), undefined);
    assert.equal(ui.requests.filter(({ route }) => route === "/transitions/generations").length, 0);
  }
});

test("a saved click handler cannot submit a quote after the prompt changed", async () => {
  const ui = await harness(); ui.button("Review generation quote").props.onClick(); await flush();
  const staleClick = ui.generate().props.onClick;
  await ui.update({ prompt: "New transition prompt" }); staleClick(); await flush();
  assert.equal(ui.requests.filter(({ route }) => route === "/transitions/generations").length, 0);
});

test("explicit submission is guarded in flight and freezes the quote even if the prompt changes", async () => {
  const pending = deferred(); const ui = await harness({ postHandler: () => pending.promise });
  ui.button("Review generation quote").props.onClick(); await flush();
  const click = ui.generate().props.onClick; click(); click(); await flush();
  await ui.update({ prompt: "Different motion" });
  const posts = ui.requests.filter(({ route }) => route === "/transitions/generations");
  assert.equal(posts.length, 1);
  const payload = JSON.parse(posts[0].options.body);
  assert.equal(payload.prompt, draft.prompt); assert.equal(payload.max_credits, 120); assert.equal(payload.confirm_spend, true);
  assert.match(payload.request_id, /^[a-f0-9-]{36}$/);
  pending.resolve(); await flush();
  assert.match(text(ui.tree), /is saved/);
});

test("an uncertain enqueue never retries automatically and explicit retry reuses the exact request", async () => {
  const ui = await harness({ postHandler: (_request, count) => { if (count === 1) throw new TypeError("Network failed"); } });
  ui.button("Review generation quote").props.onClick(); await flush(); ui.generate().props.onClick(); await flush();
  assert.equal(ui.requests.filter(({ route }) => route === "/transitions/generations").length, 1);
  assert.equal(ui.button("Review generation quote").props.disabled, true);
  assert.ok(ui.button("Check request status"));
  await ui.update({ prompt: "Edited after uncertainty" });
  ui.button("Retry same request").props.onClick(); await flush();
  const posts = ui.requests.filter(({ route }) => route === "/transitions/generations");
  assert.equal(posts.length, 2); assert.equal(posts[0].options.body, posts[1].options.body);
  assert.match(text(ui.tree), /is saved/);
});

test("late history snapshots cannot erase a just-accepted generation", async () => {
  const pending = deferred(); const ui = await harness({ historyHandler: () => pending.promise });
  ui.button("Review generation quote").props.onClick(); await flush(); ui.generate().props.onClick(); await flush();
  assert.match(text(ui.tree), /Saved generations · 1/);
  pending.resolve({ generations: [] }); await flush();
  assert.match(text(ui.tree), /Saved generations · 1/);
});

test("terminal generations expose receipt/original recovery and guard retained-task cancellation", async () => {
  const request = helpers.generationRequest(draft, review);
  const failed = { id: request.request_id, status: "interrupted", request, error: "Worker stopped", receipt_url: "/receipt", original_url: "/original", storage: { original_bytes: 100 } };
  const pending = deferred();
  const ui = await harness({ initialGenerations: [failed], cancelHandler: () => pending.promise });
  assert.ok(ui.nodes.some((node) => node.type === "a" && node.props.href === "/original"));
  assert.ok(ui.nodes.some((node) => node.type === "a" && node.props.href === "/receipt"));
  const click = ui.button("Cancel retained provider task").props.onClick;
  click(); click(); await flush();
  assert.equal(ui.requests.filter(({ route }) => route.endsWith("/cancel")).length, 1);
  assert.equal(ui.requests.filter(({ route }) => route === "/transitions/generations").length, 0);
  pending.resolve(); await flush();
  assert.match(text(ui.tree), /cancellation was checked/);
  const completed = await harness({ initialGenerations: [{ ...failed, status: "completed" }] });
  assert.equal(completed.button("Cancel retained provider task"), undefined);
});

const registry = [plain(helpers.DEFAULT_GENERATION_MODEL),
  { id: "h3_max", label: "MiniMax H3 Max", max_prompt_length: 6000, defaults: { duration: 5, resolution: "768p", prompt_expansion_mode: "disabled" },
    capabilities: { first_last_frames: true, min_seconds: 5, max_seconds: 8, durations: [5, 6, 7, 8], resolutions: ["480p", "768p"], seed: true, prompt_expansion_modes: ["disabled", "balanced", "quality"] } },
  { id: "wan3", label: "Wan 3", max_prompt_length: 20000, defaults: { duration: 2, resolution: "720p" },
    capabilities: { first_last_frames: true, min_seconds: 2, max_seconds: 8, durations: [2, 3, 4, 5, 6, 7, 8], resolutions: ["480p", "720p", "1080p"], seed: false, prompt_expansion_modes: [] } }];

test("model capabilities validate durations, prompt limits, seed omission and expansion without truncation", () => {
  const h3 = { ...draft, model: "h3_max", duration: 5, resolution: "768p", prompt_expansion_mode: "disabled" };
  const { seed: ignored, ...h3Quote } = h3;
  assert.deepEqual(plain(helpers.quoteRequest(h3, registry[1])), h3Quote);
  for (const patch of [{ duration: 4 }, { resolution: "720p" }, { prompt: "x".repeat(6001) }, { prompt_expansion_mode: "unknown" }])
    assert.throws(() => helpers.quoteRequest({ ...h3, ...patch }, registry[1]));
  const wan = { ...draft, model: "wan3", duration: 2, seed: undefined, prompt: "x".repeat(20000) };
  assert.equal(helpers.quoteRequest(wan, registry[2]).prompt.length, 20000);
  assert.throws(() => helpers.quoteRequest({ ...wan, seed: 42 }, registry[2]), /does not support a seed/);
  assert.throws(() => helpers.quoteRequest({ ...wan, prompt_expansion_mode: "disabled" }, registry[2]), /does not support/);
  const h3Review = { ...review, key: helpers.generationKey(h3), quote: makeQuote(helpers.quoteRequest(h3, registry[1])) };
  assert.equal(helpers.generationRequest(h3, h3Review, registry[1]).prompt_expansion_mode, "disabled");
  assert.throws(() => helpers.generationRequest(h3, { ...h3Review, quote: { ...h3Review.quote, model: "wan3" } }, registry[1]), /fresh quote/);
  assert.throws(() => helpers.generationRequest({ ...h3, prompt_expansion_mode: "quality" }, h3Review, registry[1]), /fresh quote/);
});

test("model changes invalidate reviewed quotes, apply supported defaults and preserve prompt writing", async () => {
  const ui = await harness({ providerPatch: { models: registry } });
  ui.button("Review generation quote").props.onClick(); await flush(); const oldGenerate = ui.generate().props.onClick;
  await ui.change("Generation model", "h3_max"); oldGenerate(); await flush();
  assert.equal(ui.generate(), undefined);
  assert.equal(ui.requests.filter(({ route }) => route === "/transitions/generations").length, 0);
  const field = label => ui.nodes.find(node => node.props?.["aria-label"] === label);
  assert.equal(field("Generation duration").props.value, 5);
  assert.equal(field("Generation resolution").props.value, "768p");
  ui.button("Review generation quote").props.onClick(); await flush();
  const quoted = JSON.parse(ui.requests.filter(({ route }) => route === "/transitions/bridges/quote").at(-1).options.body);
  assert.equal(quoted.model, "h3_max"); assert.equal(quoted.prompt, draft.prompt); assert.equal(quoted.prompt_expansion_mode, "disabled");
  await ui.change("Generation prompt expansion", "quality"); assert.equal(ui.generate(), undefined);
  await ui.change("Generation model", "wan3");
  assert.equal(field("Generation duration").props.value, 2);
  assert.equal(field("Generation seed"), undefined); assert.equal(field("Generation prompt expansion"), undefined);
  ui.button("Review generation quote").props.onClick(); await flush(); ui.generate().props.onClick(); await flush();
  const payload = JSON.parse(ui.requests.find(({ route }) => route === "/transitions/generations").options.body);
  assert.equal(payload.model, "wan3"); assert.equal(payload.prompt, draft.prompt);
  assert.equal(Object.hasOwn(payload, "seed"), false); assert.equal(Object.hasOwn(payload, "prompt_expansion_mode"), false);
});

test("a model change discards an in-flight quote and inactive methods cannot submit saved handlers", async () => {
  const pending = deferred(); const ui = await harness({ providerPatch: { models: registry }, quoteHandler: () => pending.promise });
  ui.button("Review generation quote").props.onClick(); await flush();
  const request = JSON.parse(ui.requests.find(({ route }) => route === "/transitions/bridges/quote").options.body);
  await ui.change("Generation model", "h3_max"); pending.resolve(makeQuote(request)); await flush();
  assert.equal(ui.generate(), undefined);
  const ready = await harness(); ready.button("Review generation quote").props.onClick(); await flush();
  const staleClick = ready.generate().props.onClick; await ready.update({ active: false }); staleClick(); await flush();
  assert.equal(ready.requests.filter(({ route }) => route === "/transitions/generations").length, 0);
});

test("uncertain submission retains its identity after a rejected retry and across source changes", async () => {
  const ui = await harness({ postHandler: (_request, count) => { if (count === 1) throw new TypeError("Network failed"); throw Object.assign(new Error("Price contract changed"), { status: 422 }); } });
  ui.button("Review generation quote").props.onClick(); await flush(); ui.generate().props.onClick(); await flush();
  await ui.change("Generation seed", "100"); await ui.update({ job: { ...source, id: "other-parent" } });
  ui.button("Retry same request").props.onClick(); await flush();
  const posts = ui.requests.filter(({ route }) => route === "/transitions/generations");
  assert.equal(posts.length, 2); assert.equal(posts[0].options.body, posts[1].options.body);
  assert.ok(ui.button("Check request status")); assert.ok(ui.button("Retry same request"));
  assert.equal(ui.button("Review generation quote").props.disabled, true);
  assert.match(text(ui.tree), /status remains uncertain/);
});

test("an initial definitive rejection permits a fresh quote without retaining an uncertain request", async () => {
  const ui = await harness({ postHandler: () => { throw Object.assign(new Error("Invalid source"), { status: 422 }); } });
  ui.button("Review generation quote").props.onClick(); await flush(); ui.generate().props.onClick(); await flush();
  assert.equal(ui.button("Check request status"), undefined);
  assert.equal(ui.button("Review generation quote").props.disabled, false);
});

test("retained originals offer local timing without quoting or generating, including failed assembly", async () => {
  for (const status of ["completed", "failed", "interrupted"]) {
    const saved = { id: "retained", status, request: { ...draft, model: "h3_max", prompt: "The saved archway direction" },
      original_url: "/lab/transitions/generations/retained/original", storage: { original_bytes: 1234 } };
    const selected = [], ui = await harness({ initialGenerations: [saved], onAdjustTiming: value => selected.push(value) });
    await ui.update({ prompt: "A different current prompt" });
    ui.button("Adjust timing").props.onClick(); await flush();
    assert.equal(selected[0], saved); assert.equal(selected[0].request.prompt, "The saved archway direction");
    assert.equal(ui.requests.some(item => item.options?.method === "POST"), false);
    const stale = ui.button("Adjust timing").props.onClick;
    await ui.update({ active: false }); stale(); assert.equal(selected.length, 1);
  }
});

test("an original URL without a measured retained file never offers broken recovery or timing", async () => {
  for (const storage of [undefined, null, { original_bytes: 0 }, { original_bytes: -1 }, { original_bytes: NaN }]) {
    const saved = { id: "failed-download", status: "failed", request: draft, original_url: "/missing/original", storage };
    assert.equal(helpers.retainedGenerationOriginal(saved), null);
    const ui = await harness({ initialGenerations: [saved], onAdjustTiming: () => assert.fail("No retained original") });
    assert.equal(ui.button("Adjust timing"), undefined);
    assert.doesNotMatch(text(ui.tree), /Recover generated original/);
  }
});

test("retained bytes do not expose originals the artifact API excludes after cancellation or during work", async () => {
  for (const patch of [{ status: "cancelled" }, { status: "running" }, { status: "queued" }, { status: "completed", cancel_requested: true }, { status: "failed", cancel_requested: true }]) {
    const saved = { id: "not-downloadable", request: draft, original_url: "/original", storage: { original_bytes: 100 }, ...patch };
    assert.equal(helpers.retainedGenerationOriginal(saved), null);
    const ui = await harness({ initialGenerations: [saved], onAdjustTiming: () => assert.fail("Original is not eligible") });
    assert.equal(ui.button("Adjust timing"), undefined); assert.doesNotMatch(text(ui.tree), /Recover generated original/);
  }
});
