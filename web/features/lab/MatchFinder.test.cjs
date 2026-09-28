const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const compiled = ts.transpileModule(fs.readFileSync(path.join(__dirname, "MatchFinder.tsx"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const clip = { id: "a", film_id: "film-a", unit_id: "shot-a", title: "Scene A", source_start: 10, source_end: 13, reference_time: 12, locked: false };
const candidate = (id, ready = false) => ({ id, film_title: `Film ${id}`, evidence: "Similar movement", outgoing: clip, incoming: { ...clip, id, reference_time: 40, source_start: 40, source_end: 41 }, preview_ready: ready });
const job = (status = "completed", candidates = [candidate("1"), candidate("2"), candidate("3")]) => ({ id: "match", kind: "match", status, base_revision: 3, result: { reference_clip_id: "a", candidates } });
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const flush = async () => { for (let i = 0; i < 35; i++) await Promise.resolve(); };
const plain = (value) => JSON.parse(JSON.stringify(value));

async function harness(overrides = {}, requestOverride) {
  const hooks = [], effects = [], requests = [];
  let cursor = 0, scheduled = false, tree;
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
  const props = { clip, busy: false, job: null, revision: 3, dirty: false, onFind() {}, onApply: async () => true, onExample() {}, onBrowse() {}, onChange() {}, ...overrides };
  const exported = {};
  vm.runInNewContext(compiled, {
    exports: exported, AbortController, setTimeout, clearTimeout, crypto: { randomUUID: () => "new-clip" },
    require(name) {
      if (name === "react") return react;
      if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }), Fragment: "fragment" };
      if (name === "@/lib/lab") return {
        mediaUrl: (value) => value, seconds: (value) => value.toFixed(3),
        labRequest: async (route, options) => {
          requests.push({ route, options });
          if (route === "/matching/cohorts") return { cohorts: [{ id: "cohort", films: [{ film_id: "film-a" }], shot_count: 200, motion_count: 80, visual_ready: false, motion_ready: true, subject_ready: false, examples: [] }] };
          if (requestOverride) return requestOverride(route, options);
          const id = route.split("/").at(-2);
          return { id: `preview-${id}`, kind: "match-preview", status: "completed", result: { candidate: candidate(id, true) } };
        },
      };
      if (name.endsWith(".module.css")) return { default: new Proxy({}, { get: (_, key) => key }) };
      return { default: name.replace("./", "") };
    },
  });
  function render() { cursor = 0; scheduled = false; tree = exported.default(props); effects.splice(0).forEach((effect) => effect()); }
  render(); await flush();
  return { requests, props, get tree() { return tree; }, get nodes() { return nodes(tree); }, async update(next) { Object.assign(props, next); render(); await flush(); }, button(label) { return nodes(tree).find((node) => node.type === "button" && text(node) === label); } };
}

test("the primary flow finds Automatic nearby matches with original framing", async () => {
  let submitted;
  const ui = await harness({ onFind: (options) => { submitted = options; } });
  ui.button("Find matches").props.onClick();
  assert.deepEqual(plain(submitted), { cohort_id: "cohort", reference_clip_id: "a", mode: "movement", movement: "camera", focus: "auto", timing: "nearby", allow_reframing: false });
  assert.equal(ui.nodes.filter((node) => node.type === "nav").length, 0);
  assert.equal(ui.nodes.find((node) => node.type === "option" && node.props.value === "subject").props.disabled, true);
});

test("a playable progressive result appears while matching still runs, with Keep disabled", async () => {
  const ui = await harness({ busy: true, job: job("running", [candidate("1", true)]) });
  assert.ok(ui.nodes.some((node) => node.type === "video" && node.props["aria-label"] === "A to B transition preview"));
  assert.equal(ui.button("Keep cut").props.disabled, true);
  assert.equal(ui.requests.filter((request) => request.options?.method === "POST").length, 0);
});
test("an unavailable reference channel is explained without suggesting full evidence", async () => {
  const result = job("completed", [candidate("1", true)]);
  result.result.notices = ["subject: No reliable moving subject in the selected window"];
  result.result.available_channels = ["subject", "camera", "shape"];
  const ui = await harness({ job: result });
  assert.ok(text(ui.tree).includes("Subject movement could not be assessed for this moment."));
  assert.ok(text(ui.tree).includes("No reliable moving subject in the selected window"));
  assert.ok(!text(ui.tree).includes("Preparation: cohort"));
});

test("missing top-three previews prepare automatically without an extra click", async () => {
  const ui = await harness({ job: job() });
  assert.deepEqual(ui.requests.filter((request) => request.options?.method === "POST").map((request) => request.route), ["/jobs/match/matches/1/preview", "/jobs/match/matches/2/preview", "/jobs/match/matches/3/preview"]);
  assert.ok(ui.nodes.some((node) => node.type === "video"));
  assert.equal(ui.button("Keep cut").props.disabled, false);
});

test("completing background previews cannot change the selected suggestion", async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const ui = await harness({ job: job() }, async (route) => {
    const id = route.split("/").at(-2);
    if (id === "1") await gate;
    return { id: `preview-${id}`, status: "completed", result: { candidate: candidate(id, true) } };
  });
  ui.nodes.find((node) => node.type === "button" && text(node).includes("Film 3")).props.onClick();
  await flush(); release(); await flush();
  assert.deepEqual(ui.requests.filter((request) => request.options?.method === "POST").map((request) => request.route), ["/jobs/match/matches/1/preview", "/jobs/match/matches/3/preview", "/jobs/match/matches/2/preview"]);
  const player = ui.nodes.find((node) => node.type === "video");
  assert.equal(player.props.src, "/lab/jobs/match/matches/3/preview");
});

test("timing previews apply the immutable preview job and Cancel restores the original proposal", async () => {
  let applied;
  const ui = await harness({ job: job("completed", [candidate("1", true)]), onApply: async (...args) => { applied = args; return true; } }, async (route, options) => {
    assert.equal(route, "/jobs/match/matches/1/adjust");
    assert.deepEqual(JSON.parse(options.body), { base_revision: 3, outgoing_time: 12.1, incoming_time: 40.1 });
    return { id: "adjusted", status: "completed", result: { candidate: { ...candidate("1", true), preview_url: "/lab/jobs/adjusted/matches/1/preview" } } };
  });
  ui.button("Adjust timing").props.onClick(); await flush();
  let controls = ui.nodes.find((node) => node.type === "MatchTimingControls");
  controls.props.onPreview(12.1, 40.1); await flush();
  assert.equal(ui.nodes.find((node) => node.type === "video").props.src, "/lab/jobs/adjusted/matches/1/preview");
  controls = ui.nodes.find((node) => node.type === "MatchTimingControls");
  controls.props.onCancel(); await flush();
  assert.equal(ui.nodes.find((node) => node.type === "video").props.src, "/lab/jobs/match/matches/1/preview");
  ui.button("Adjust timing").props.onClick(); await flush();
  ui.nodes.find((node) => node.type === "MatchTimingControls").props.onPreview(12.1, 40.1); await flush();
  await ui.button("Keep cut").props.onClick(); await flush();
  assert.deepEqual(applied, ["1", "adjusted"]);
});

test("an adjustment finishing after returning to scene A does not steal the monitor", async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const ui = await harness({ job: job("completed", [candidate("1", true)]) }, async () => {
    await gate;
    return { id: "adjusted", status: "completed", result: { candidate: candidate("1", true) } };
  });
  ui.button("Adjust timing").props.onClick(); await flush();
  ui.nodes.find((node) => node.type === "MatchTimingControls").props.onPreview(12.1, 40.1); await flush();
  ui.nodes.find((node) => node.type === "button" && text(node).includes("Edit moment")).props.onClick(); await flush();
  release(); await flush();
  assert.ok(ui.nodes.some((node) => node.type === "MatchSourcePlayer"));
  assert.equal(ui.nodes.some((node) => node.type === "video"), false);
});
