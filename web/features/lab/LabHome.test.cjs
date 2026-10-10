const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const compiled = ts.transpileModule(fs.readFileSync(path.join(__dirname, "LabHome.tsx"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
test("entering experiments and resuming saved projects follow registry routes without creating projects", async () => {
  const experiments = [
    { id: "visual-rhymes", name: "Match Cuts", description: "Find a cut", route: "/match", project_route: "/lab/visual-rhymes" },
    { id: "music-sketch", name: "AI Music Video", description: "Make an edit", route: "/lab/music-sketch", project_route: "/lab/music-sketch" },
    { id: "future", name: "Future", description: "Try it", route: "/custom-entry", project_route: "/custom-editor" },
  ];
  const projects = experiments.map((experiment, index) => ({ id: `saved-${index}`, experiment_id: experiment.id, name: `Edit ${index}`, revision: 1, updated_at: 100 + index, active_job_count: 0, track_name: null, clip_count: 0, sheet_unit_ids: [] }));
  const hooks = [], calls = [], exported = {}; let cursor = 0, effect;
  vm.runInNewContext(compiled, { exports: exported, AbortController, Date, Promise, URLSearchParams, window: { location: { search: "" } }, require(name) {
    if (name === "react") return {
      useState(initial) { const index = cursor++; if (!(index in hooks)) hooks[index] = initial; return [hooks[index], (value) => hooks[index] = value]; },
      useEffect(callback) { effect ??= callback; },
    };
    if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
    if (name === "@/lib/lab") return { experimentName: (_, fallback) => fallback, labRequest: async (route, options) => { calls.push({ route, options }); return route === "/projects" ? { projects } : { experiments }; } };
    return { default: name };
  } });
  exported.default(); effect(); for (let i = 0; i < 5; i++) await Promise.resolve(); cursor = 0;
  const links = nodes(exported.default()).filter((node) => node.props?.href).map((node) => node.props.href);
  for (let i = 0; i < experiments.length; i++) {
    assert.ok(links.includes(experiments[i].route));
    assert.ok(links.includes(`${experiments[i].project_route}?project=saved-${i}`));
  }
  assert.deepEqual(calls.map(({ route }) => route), ["/projects", "/experiments"]);
  assert.ok(calls.every(({ options }) => !options.method || options.method === "GET"));
});

const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
async function deletionHarness({ storageFailure = null, cleanupPending = false, deleteFailure = false, search = "" } = {}) {
  const hooks = [], calls = [], exported = {}, removedKeys = [];
  let cursor = 0, effect, tree;
  const projects = ["first", "second"].map((id) => ({ id, name: `Edit ${id}`, experiment_id: "music-sketch", revision: 3, updated_at: 100, active_job_count: 0, track_name: null, clip_count: 0, sheet_unit_ids: [] }));
  const storage = { removeItem(key) { removedKeys.push(key); if (storageFailure === "remove") throw new Error("Storage denied"); } };
  const window = { location: { search }, get localStorage() { if (storageFailure === "access") throw new Error("Storage denied"); return storage; } };
  vm.runInNewContext(compiled, { exports: exported, AbortController, Date, Promise, URLSearchParams, window, require(name) {
    if (name === "react") return {
      useState(initial) { const index = cursor++; if (!(index in hooks)) hooks[index] = initial; return [hooks[index], (update) => { hooks[index] = typeof update === "function" ? update(hooks[index]) : update; }]; },
      useEffect(callback) { effect ??= callback; },
    };
    if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
    if (name === "./ProjectActions") return { DeleteProjectButton: "DeleteProjectButton" };
    if (name === "@/lib/lab") return { experimentName: () => "AI Music Video", labRequest: async (route, options) => {
      calls.push({ route, options });
      if (options?.method === "DELETE") {
        if (deleteFailure) throw new Error("Project changed; reload before deleting");
        return { deleted: "first", cleanup_pending: cleanupPending };
      }
      return route === "/projects" ? { projects } : { experiments: [] };
    } };
    return { default: name };
  } });
  const render = () => { cursor = 0; tree = exported.default(); };
  render(); effect(); for (let i = 0; i < 5; i++) await Promise.resolve(); render();
  return { calls, removedKeys, render, get tree() { return tree; },
    remove: () => nodes(tree).find((node) => node.type === "DeleteProjectButton" && node.props.name === "Edit first").props.onDelete(),
    names: () => nodes(tree).filter((node) => node.type === "DeleteProjectButton").map((node) => node.props.name),
  };
}

for (const storageFailure of ["remove", "access"]) test(`list deletion survives localStorage ${storageFailure} failure and announces pending cleanup`, async () => {
  const app = await deletionHarness({ storageFailure, cleanupPending: true });
  assert.equal(await app.remove(), true); app.render();
  assert.deepEqual(app.names(), ["Edit second"]);
  const status = nodes(app.tree).find((node) => node.props?.role === "status");
  assert.match(text(status), /Project deleted.*generated files are still awaiting cleanup/);
  assert.equal(nodes(app.tree).some((node) => node.props?.role === "alert"), false);
  assert.deepEqual(app.calls.filter(({ options }) => options?.method === "DELETE").map(({ route, options }) => ({ route, method: options.method })), [
    { route: "/projects/first?base_revision=3", method: "DELETE" },
  ]);
});

test("Labs renders the workspace cleanup notice as status and rejected deletion keeps the row", async () => {
  const app = await deletionHarness({ search: "?cleanup=pending", deleteFailure: true });
  assert.match(text(nodes(app.tree).find((node) => node.props?.role === "status")), /Project deleted/);
  await assert.rejects(app.remove(), /Project changed/); app.render();
  assert.deepEqual(app.names(), ["Edit first", "Edit second"]);
  assert.deepEqual(app.removedKeys, []);
});

test("frozen experiments stay openable but are listed last and labeled Frozen", async () => {
  const experiments = [
    { id: "transitions", name: "Transitions", description: "Old lab", route: "/lab/transitions", status: "frozen" },
    { id: "music-sketch", name: "AI Music Video", description: "Make an edit", route: "/lab/music-sketch", status: "experimental" },
  ];
  const hooks = [], exported = {}; let cursor = 0, effect;
  vm.runInNewContext(compiled, { exports: exported, AbortController, Date, Promise, URLSearchParams, window: { location: { search: "" } }, require(name) {
    if (name === "react") return {
      useState(initial) { const index = cursor++; if (!(index in hooks)) hooks[index] = initial; return [hooks[index], (value) => hooks[index] = value]; },
      useEffect(callback) { effect ??= callback; },
    };
    if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
    if (name === "@/lib/lab") return { experimentName: (_, fallback) => fallback, labRequest: async (route) => route === "/projects" ? { projects: [] } : { experiments } };
    return { default: name };
  } });
  exported.default(); effect(); for (let i = 0; i < 5; i++) await Promise.resolve(); cursor = 0;
  const entries = nodes(exported.default()).filter((node) => node.props?.["aria-label"]?.startsWith("Open "));
  assert.deepEqual(entries.map((node) => node.props.href), ["/lab/music-sketch", "/lab/transitions"]);
  assert.match(text(entries[1]), /TransitionsFrozen/);
  assert.doesNotMatch(text(entries[0]), /Frozen/);
});

test("every saved edit is listed with its song and clip count, and the filter narrows them", async () => {
  const projects = Array.from({ length: 10 }, (_, i) => ({
    id: `p${i}`, name: i === 3 ? "Dracula night" : `Edit ${i}`, experiment_id: "music-sketch", revision: 1, updated_at: 100 + i,
    active_job_count: 0, track_name: i === 5 ? "Halloween Song" : "Song", clip_count: 1, sheet_unit_ids: [`u${i}`],
  }));
  const experiments = [{ id: "music-sketch", name: "AI Music Video", description: "Make an edit", route: "/lab/music-sketch" }];
  const hooks = [], exported = {}; let cursor = 0, effect;
  vm.runInNewContext(compiled, { exports: exported, AbortController, Date, Promise, URLSearchParams, window: { location: { search: "" } }, require(name) {
    if (name === "react") return {
      useState(initial) { const index = cursor++; if (!(index in hooks)) hooks[index] = initial; return [hooks[index], (value) => { hooks[index] = typeof value === "function" ? value(hooks[index]) : value; }]; },
      useEffect(callback) { effect ??= callback; },
    };
    if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
    if (name === "@/lib/lab") return { experimentName: (_, fallback) => fallback, mediaUrl: (path) => path, labRequest: async (route) => route === "/projects" ? { projects } : { experiments } };
    return { default: name };
  } });
  const render = () => { cursor = 0; return exported.default(); };
  render(); effect(); for (let i = 0; i < 5; i++) await Promise.resolve();
  let tree = render();
  const edits = () => nodes(tree).filter((node) => node.props?.href?.startsWith("/lab/music-sketch?project="));
  assert.equal(edits().length, 10, "no View all: every edit is shown");
  assert.equal(nodes(tree).some((node) => /View all/.test(text(node))), false);
  assert.match(text(edits()[0]), /Edit 9Song1 clip/);
  assert.equal(nodes(tree).find((node) => text(node) === "New edit" && node.props?.href).props.href, "/lab/music-sketch");
  nodes(tree).find((node) => node.type === "input").props.onChange({ target: { value: "halloween" } });
  tree = render();
  assert.deepEqual(edits().map((node) => node.props.href), ["/lab/music-sketch?project=p5"], "the filter matches song names too");
});

test("experiments show as soon as they arrive, before the saved edits", async () => {
  const experiments = [{ id: "music-sketch", name: "AI Music Video", description: "Make an edit", route: "/lab/music-sketch" }];
  const hooks = [], exported = {}; let cursor = 0, effect;
  vm.runInNewContext(compiled, { exports: exported, AbortController, Date, Promise, URLSearchParams, window: { location: { search: "" } }, require(name) {
    if (name === "react") return {
      useState(initial) { const index = cursor++; if (!(index in hooks)) hooks[index] = initial; return [hooks[index], (value) => hooks[index] = value]; },
      useEffect(callback) { effect ??= callback; },
    };
    if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
    if (name === "@/lib/lab") return { experimentName: (_, fallback) => fallback, labRequest: (route) => route === "/projects" ? new Promise(() => {}) : Promise.resolve({ experiments }) };
    return { default: name };
  } });
  exported.default(); effect(); for (let i = 0; i < 5; i++) await Promise.resolve(); cursor = 0;
  const tree = exported.default();
  assert.equal(nodes(tree).find((node) => node.props?.["aria-label"] === "Open AI Music Video").props.href, "/lab/music-sketch");
  assert.match(text(nodes(tree).find((node) => node.props?.role === "status")), /Loading edits/);
  assert.equal(nodes(tree).find((node) => node.type === "@/components/AppBar").props.active, "lab");
});
