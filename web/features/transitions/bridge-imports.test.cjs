const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const compile = (file, jsx = false) => ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, ...(jsx ? { jsx: ts.JsxEmit.ReactJSX } : {}) },
}).outputText;
const directions = {}, helpers = {}, generationHelpers = {};
vm.runInNewContext(compile("directions.ts"), { exports: directions });
vm.runInNewContext(compile("bridges.ts"), { exports: helpers, require: () => directions });
vm.runInNewContext(compile("generation.ts"), { exports: generationHelpers });
const source = { id: "parent-a", status: "completed", request: {}, result: {} };
const bridge = (id, parent = source.id) => ({ id, status: "completed", request: { parent_render_id: parent, trim_start: 0, provenance: {} }, input: { name: `${id}.mp4`, size: 100, media: { duration: 4 }, provenance_status: "user-supplied" },
  result: { preview_url: `/${id}/video`, manifest_url: `/${id}/manifest`, original_url: `/${id}/original`, duration: 8, transition_start: 2, transition_end: 6 } });
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const nodes = node => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = node => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
async function harness({ active = true, initial = [], historyHandler, uploadHandler, generationSource = null, fetchHandler, onExternalFile } = {}) {
  const hooks = [], effects = [], exported = {}, requests = [], downloads = [], timers = new Map(), revoked = [];
  let cursor = 0, scheduled = false, tree, timerId = 0;
  let props = { job: source, active, model: "External model", prompt: "Custom motion", generationSource, onExternalFile };
  const same = (a, b) => a && b && a.length === b.length && a.every((item, index) => Object.is(item, b[index]));
  const schedule = () => { if (!scheduled) { scheduled = true; queueMicrotask(render); } };
  const react = {
    useState(initial) { const index = cursor++; hooks[index] ??= { value: typeof initial === "function" ? initial() : initial }; return [hooks[index].value, (change) => { const next = typeof change === "function" ? change(hooks[index].value) : change; if (!Object.is(next, hooks[index].value)) { hooks[index].value = next; schedule(); } }]; },
    useRef(initial) { return hooks[cursor++] ??= { current: initial }; },
    useCallback(callback, deps) { const index = cursor++; if (!hooks[index] || !same(hooks[index].deps, deps)) hooks[index] = { value: callback, deps }; return hooks[index].value; },
    useEffect(effect, deps) { const index = cursor++; if (!hooks[index] || !same(hooks[index].deps, deps)) { const cleanup = hooks[index]?.cleanup; hooks[index] = { deps, cleanup }; effects.push(() => { hooks[index].cleanup?.(); hooks[index].cleanup = effect(); }); } },
  };
  vm.runInNewContext(compile("BridgeImports.tsx", true), {
    exports: exported, AbortController, Error, File,
    fetch: async (url, options) => { downloads.push({ url, options }); return fetchHandler ? fetchHandler(url, options) : { ok: true, headers: { get: () => "100" }, blob: async () => new Blob([new Uint8Array(100)], { type: "video/mp4" }) }; },
    URL: { createObjectURL: file => `blob:${file.name}`, revokeObjectURL: value => revoked.push(value) },
    FormData: class { fields = {}; append(key, value) { this.fields[key] = value; } },
    setTimeout(callback) { timers.set(++timerId, callback); return timerId; }, clearTimeout(id) { timers.delete(id); },
    require(name) {
      if (name === "react") return react;
      if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }), Fragment: "fragment" };
      if (name === "./bridges") return helpers;
      if (name === "./generation") return generationHelpers;
      if (name === "@/lib/lab") return { mediaUrl: value => value, labRequest: async (route, options) => {
        requests.push({ route, options });
        if (route === "/transitions/bridges") {
          const metadata = JSON.parse(options.body.fields.metadata);
          return uploadHandler ? uploadHandler(metadata) : { ...bridge("saved", metadata.parent_render_id), request: metadata };
        }
        if (route.startsWith("/transitions/bridges?")) return historyHandler ? historyHandler(route) : { bridges: initial };
        const id = route.split("/").at(-1); return initial.find(item => item.id === id) ?? bridge(id);
      } };
      return { default: new Proxy({}, { get: (_, key) => key }) };
    },
  });
  function render() { cursor = 0; scheduled = false; tree = exported.default(props); effects.splice(0).forEach(effect => effect()); }
  render(); await flush();
  return {
    requests, downloads, timers, revoked, get tree() { return tree; }, get nodes() { return nodes(tree); },
    button(label) { return nodes(tree).find(node => node.type === "button" && text(node).trim() === label); },
    selected() { return nodes(tree).find(node => node.type === "button" && node.props["aria-pressed"] === true && nodes(node).some(child => child.type === "strong")); },
    player(label) { return nodes(tree).find(node => node.type === "video" && node.props["aria-label"] === label); },
    async update(patch) { props = { ...props, ...patch }; schedule(); await flush(); },
    async choose(name = "chosen.mp4") { const field = nodes(tree).find(node => node.props?.["aria-label"] === "Generated bridge video"); field.props.onChange({ target: { files: [{ name, size: 100 }], value: name } }); await flush(); },
    async trim(value) { const field = nodes(tree).find(node => node.type === "input" && node.props.max === "29.92"); field.props.onChange({ target: { value } }); await flush(); },
  };
}

test("inactive import route does not fetch or poll and pauses both media players", async () => {
  const ui = await harness({ active: false, initial: [{ ...bridge("pending"), status: "queued", result: null }] });
  assert.equal(ui.requests.length, 0);
  await ui.update({ active: true }); assert.ok(ui.requests.length >= 2); assert.equal(ui.timers.size, 1);
  await ui.choose(); let pauses = 0;
  ui.player("Imported bridge before assembly").props.ref({ pause() { pauses++; } });
  await ui.update({ active: false }); assert.equal(pauses, 1); assert.equal(ui.timers.size, 0);
  const count = ui.requests.length; await flush(); assert.equal(ui.requests.length, count);
  assert.equal(ui.button("Save & render both joins").props.disabled, true);
  const completed = await harness({ initial: [bridge("completed")] }); await completed.choose();
  let completedPauses = 0;
  const media = { pause() { completedPauses++; } };
  completed.player("Imported bridge before assembly").props.ref(media);
  completed.player("Original A, imported bridge, original B").props.ref(media);
  await completed.update({ active: false }); assert.equal(completedPauses, 2);
});

test("refresh and method switches preserve a valid saved selection and file trim", async () => {
  const ui = await harness({ initial: [bridge("first"), bridge("second")] });
  ui.nodes.find(node => node.type === "button" && text(node).startsWith("second.mp4")).props.onClick(); await flush();
  await ui.choose(); await ui.trim("1.25");
  ui.button("Refresh saved auditions").props.onClick(); await flush();
  assert.match(text(ui.selected()), /second.mp4/);
  await ui.update({ active: false }); await ui.update({ active: true });
  assert.match(text(ui.selected()), /second.mp4/);
  assert.equal(ui.nodes.find(node => node.type === "input" && node.props.max === "29.92").props.value, "1.25");
  assert.equal(ui.player("Imported bridge before assembly").props.src, "blob:chosen.mp4");
  assert.equal(ui.revoked.length, 0);
  assert.doesNotMatch(text(ui.tree), /undefineds/);
});

test("a late history response cannot erase a newly saved import", async () => {
  const pending = deferred(), ui = await harness({ historyHandler: () => pending.promise });
  await ui.choose(); ui.button("Save & render both joins").props.onClick(); await flush();
  assert.match(text(ui.selected()), /saved.mp4/);
  pending.resolve({ bridges: [bridge("older")] }); await flush();
  assert.match(text(ui.selected()), /saved.mp4/); assert.match(text(ui.tree), /Saved imports · 2/);
});

test("a different parent keeps the file but requires explicit reassociation and hides old results", async () => {
  const ui = await harness({ initial: [bridge("old-result")] });
  await ui.choose(); await ui.trim("1.25"); const staleUpload = ui.button("Save & render both joins").props.onClick;
  await ui.update({ job: { ...source, id: "parent-b" } });
  assert.equal(ui.player("Original A, imported bridge, original B"), undefined);
  assert.ok(ui.button("Use file for this source pair")); assert.equal(ui.button("Save & render both joins").props.disabled, true);
  staleUpload(); await flush(); assert.equal(ui.requests.filter(item => item.route === "/transitions/bridges").length, 0);
  ui.button("Use file for this source pair").props.onClick(); await flush();
  ui.button("Save & render both joins").props.onClick(); await flush();
  const payload = JSON.parse(ui.requests.find(item => item.route === "/transitions/bridges").options.body.fields.metadata);
  assert.equal(payload.parent_render_id, "parent-b"); assert.equal(payload.trim_start, 1.25); assert.equal(payload.provenance.prompt, "Custom motion");
});

test("an upload finishing after a source switch cannot select its result under the new pair", async () => {
  const pending = deferred(), ui = await harness({ uploadHandler: () => pending.promise });
  await ui.choose(); const click = ui.button("Save & render both joins").props.onClick; click(); click(); await flush();
  assert.equal(ui.requests.filter(item => item.route === "/transitions/bridges").length, 1);
  await ui.update({ job: { ...source, id: "parent-b" } }); pending.resolve(bridge("saved-old")); await flush();
  assert.equal(ui.selected(), undefined); assert.equal(ui.player("Original A, imported bridge, original B"), undefined);
  assert.ok(ui.button("Use file for this source pair"));
});

test("inactive and retimed source changes block stale upload handlers", async () => {
  for (const patch of [{ active: false }, { job: { ...source, request: { retime: { mode: "rush" } } } }]) {
    const ui = await harness(); await ui.choose(); const stale = ui.button("Save & render both joins").props.onClick;
    await ui.update(patch); stale(); await flush();
    assert.equal(ui.requests.filter(item => item.route === "/transitions/bridges").length, 0);
  }
});

const retainedGeneration = () => ({ id: "saved-generation", status: "failed", original_url: "/lab/transitions/generations/saved-generation/original",
  receipt_url: "/lab/transitions/generations/saved-generation/receipt", storage: { original_bytes: 100 },
  request: { parent_render_id: source.id, model: "h3_max", prompt: "Keep this exact saved direction.", seed: 0, duration: 5, resolution: "768p" } });
const originalResponse = () => ({ ok: true, headers: { get: () => "100" }, blob: async () => new Blob([new Uint8Array(100)], { type: "video/mp4" }) });

test("retained original opens existing trim controls and saves its frozen notes only on explicit Save", async () => {
  const generation = retainedGeneration(), ui = await harness({ generationSource: generation });
  assert.equal(ui.downloads.length, 1); assert.equal(ui.downloads[0].url, generation.original_url);
  assert.equal(ui.player("Imported bridge before assembly").props.src, "blob:generation-saved-generation.mp4");
  assert.match(text(ui.tree), /Local timing copy/); assert.match(text(ui.tree), /user-supplied notes/);
  assert.equal(ui.requests.some(item => item.options?.method === "POST"), false);
  await ui.trim(".25");
  ui.button("1s").props.onClick(); await flush();
  await ui.update({ model: "New unsaved model", prompt: "Different current prompt", active: false });
  await ui.update({ active: true });
  assert.equal(ui.downloads.length, 1, "returning to the form keeps the loaded file and trim");
  ui.button("Save & render both joins").props.onClick(); await flush();
  const post = ui.requests.find(item => item.route === "/transitions/bridges");
  const metadata = JSON.parse(post.options.body.fields.metadata);
  assert.equal(metadata.trim_start, .25); assert.equal(metadata.playback_duration, 1);
  assert.deepEqual(metadata.provenance, { provider: "Runway", model: "h3_max", prompt: generation.request.prompt, seed: 0 });
  assert.equal(post.options.body.fields.file.name, "generation-saved-generation.mp4");
  assert.equal(ui.requests.some(item => item.route === "/transitions/generations"), false);
});

test("manual replacement cancels a pending original load and restores external notes", async () => {
  const pending = deferred(); let external = 0;
  const ui = await harness({ generationSource: retainedGeneration(), fetchHandler: () => pending.promise, onExternalFile: () => external++ });
  assert.match(text(ui.tree), /Loading retained original/);
  await ui.choose("external.mp4"); assert.equal(external, 1); assert.equal(ui.downloads[0].options.signal.aborted, true);
  pending.resolve(originalResponse()); await flush();
  assert.equal(ui.player("Imported bridge before assembly").props.src, "blob:external.mp4");
  ui.button("Save & render both joins").props.onClick(); await flush();
  const metadata = JSON.parse(ui.requests.find(item => item.route === "/transitions/bridges").options.body.fields.metadata);
  assert.deepEqual(metadata.provenance, { provider: null, model: "External model", prompt: "Custom motion" });
});

test("a source change or cancelled load cannot attach a late original to the new pair", async () => {
  for (const stop of [async ui => ui.update({ job: { ...source, id: "new-parent" } }), async ui => { ui.button("Cancel loading").props.onClick(); await flush(); }]) {
    const pending = deferred(), ui = await harness({ generationSource: retainedGeneration(), fetchHandler: () => pending.promise });
    await stop(ui); pending.resolve(originalResponse()); await flush();
    assert.equal(ui.downloads[0].options.signal.aborted, true);
    assert.equal(ui.player("Imported bridge before assembly"), undefined);
    assert.equal(ui.button("Save & render both joins").props.disabled, true);
    assert.equal(ui.requests.some(item => item.options?.method === "POST"), false);
  }
});

test("unavailable retained media reports a local load error without saving or generating", async () => {
  const ui = await harness({ generationSource: retainedGeneration(), fetchHandler: async () => ({ ok: false }) });
  assert.match(text(ui.tree), /retained original could not be loaded/);
  assert.equal(ui.player("Imported bridge before assembly"), undefined);
  assert.equal(ui.button("Save & render both joins").props.disabled, true);
  assert.equal(ui.requests.some(item => item.options?.method === "POST"), false);
});
