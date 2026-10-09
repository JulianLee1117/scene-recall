const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const compile = (file) => ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const guide = {};
vm.runInNewContext(compile("guide.ts"), { exports: guide });
const compiled = compile("InfoView.tsx");
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
const settings = () => ({
  schema_version: 1, scope: "loaded configuration",
  models: { visual_encoder: "custom-visual", text_encoder: "custom-text", annotator: "custom-vision", annotator_provider: "custom-provider", annotator_image_detail: "high", annotator_reasoning_effort: "low", whisper: "custom-whisper" },
  thresholds: { subsegment_min_duration: 29, flash_min_duration: 0.37, keyframe_short_shot_s: 3.25 },
  retrieval: { weights: { img: 0.55, txt: 0.3, lex: 0.15 }, candidate_limit: 137, result_window: 31, max_result_limit: 199, diversity: { page_size: 9, film_results_per_page_target: 3, film_repeat_rank_strength: 17 } },
  ingest: { annotation_concurrency: 5 },
  lab: { music_provider: "custom-audio-provider", music_model: "custom-music", planner_model: "custom-planner", music_prompt_version: "music-test", planner_prompt_version: "planner-test", context_profile: null, footage_inspection: false, beat_checkpoint_configured: false, beat_device: "cpu" },
});
const stepsById = (sections) => Object.fromEntries(sections.flatMap((section) => section.steps.map((step) => [step.id, step])));

function harness() {
  const hooks = [], requests = [], timers = [];
  let cursor = 0, output, scheduled = false, disposed = false, effects = [], lateUpdates = 0;
  const schedule = () => { if (!scheduled && !disposed) { scheduled = true; queueMicrotask(render); } };
  const react = {
    useState(initial) {
      const i = cursor++; hooks[i] ??= { value: typeof initial === "function" ? initial() : initial };
      return [hooks[i].value, (update) => {
        if (disposed) lateUpdates++;
        const value = typeof update === "function" ? update(hooks[i].value) : update;
        if (!Object.is(value, hooks[i].value)) { hooks[i].value = value; schedule(); }
      }];
    },
    useEffect(effect, deps) {
      const i = cursor++;
      if (!hooks[i] || !same(deps, hooks[i].deps)) {
        const old = hooks[i]; hooks[i] = { deps, cleanup: old?.cleanup };
        effects.push(() => { hooks[i].cleanup?.(); hooks[i].cleanup = effect(); });
      }
    },
  };
  const exports = {};
  vm.runInNewContext(compiled, {
    exports, process: { env: { NEXT_PUBLIC_API_URL: "https://api.example.test" } }, AbortController,
    fetch(url, init) { return new Promise((resolve, reject) => requests.push({ url, init, resolve, reject })); },
    setInterval(...args) { timers.push({ kind: "interval", args }); return timers.length; },
    setTimeout(...args) { timers.push({ kind: "timeout", args }); return timers.length; },
    clearInterval() {}, clearTimeout() {},
    require(name) {
      if (name === "react") return react;
      if (name === "./guide") return guide;
      if (name.endsWith(".css")) return { default: {} };
      if (name === "react/jsx-runtime") {
        const jsx = (type, props) => typeof type === "function" ? type(props) : { type, props };
        return { jsx, jsxs: jsx, Fragment: "fragment" };
      }
      throw new Error(`Unexpected module: ${name}`);
    },
  });
  function render() {
    if (disposed) return;
    scheduled = false; cursor = 0; output = exports.default();
    const run = effects; effects = []; run.forEach((effect) => effect());
  }
  const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
  render();
  return {
    requests, timers, flush,
    get state() { return output; },
    get lateUpdates() { return lateUpdates; },
    find: (predicate) => nodes(output).find(predicate),
    async render() { render(); await flush(); },
    async resolve(index, body = settings(), ok = true) { requests[index].resolve({ ok, json: async () => body }); await flush(); },
    async reject(index) { requests[index].reject(new Error("Network offline")); await flush(); },
    dispose() { disposed = true; hooks.forEach((hook) => hook?.cleanup?.()); },
  };
}

test("guide values follow supplied settings, including alternative models and disabled optional features", () => {
  const config = settings();
  const before = JSON.stringify(config);
  const steps = stepsById(guide.buildGuide(config));
  assert.equal(JSON.stringify(config), before, "building explanations must not mutate settings");
  for (const name of ["custom-visual", "custom-text", "custom-vision", "custom-provider", "custom-whisper", "custom-music", "custom-audio-provider", "custom-planner"]) {
    assert.ok(JSON.stringify(steps).includes(name), name);
  }
  assert.doesNotMatch(JSON.stringify(steps), /PE-Core-L-14-336|97b0c61|gpt-5\.6/);
  assert.match(steps.shots.method, /0\.37 s.*29 s/);
  assert.match(steps.media.method, /3\.25 s/);
  assert.match(steps.describe.detail.join(" "), /Up to 5 requests/);
  assert.match(steps.candidates.method, /137 per channel; up to 411/);
  assert.match(steps.fusion.method, /visual 0\.55, semantic text 0\.3, lexical 0\.15/);
  assert.match(steps.refine.method, /17 × repeats/);
  assert.match(steps.refine.method, /pages: 9, soft target 3/);
  assert.match(steps.results.method, /31 results.*199/);
  assert.match(steps["editor-selection"].model, /Context profile: disabled.*pilot: disabled/);
  config.lab.context_profile = "context-example";
  config.lab.footage_inspection = true;
  config.lab.beat_checkpoint_configured = true;
  const enabled = stepsById(guide.buildGuide(config));
  assert.match(enabled["editor-selection"].model, /context-example.*enabled in configuration/);
  assert.match(enabled.music.model, /checkpoint override: configured/);
});

test("known model metadata appears only for matching configured encoders", () => {
  const config = settings();
  config.models.visual_encoder = "pe_core_l14";
  config.models.text_encoder = "qwen3-embedding-0.6b";
  const steps = stepsById(guide.buildGuide(config));
  assert.match(steps.describe.model, /timm\/PE-Core-L-14-336.*1,024 dimensions/);
  assert.match(steps.publish.model, /Qwen3-Embedding-0\.6B.*1,024 dimensions.*97b0c61/);
});

test("guide remains complete without settings, with unique topic/step IDs and existing implementation references", () => {
  const sections = guide.buildGuide(null);
  const ids = [], repository = path.resolve(__dirname, "../../..");
  assert.ok(sections.length >= 3);
  for (const section of sections) {
    ids.push(section.id);
    assert.ok(section.introduction && section.note && section.steps.length);
    for (const step of section.steps) {
      ids.push(step.id);
      assert.ok(step.summary && step.detail.length && step.method && step.output, step.id);
      assert.ok(step.sources.length, step.id);
      for (const source of step.sources) {
        assert.match(source, /^pipeline\/[\w/.-]+\.py$/);
        assert.ok(fs.statSync(path.join(repository, source)).isFile(), source);
      }
    }
  }
  assert.equal(new Set(ids).size, ids.length);
  assert.doesNotMatch(JSON.stringify(sections), /undefined|NaN|97b0c61|timm\/PE-Core-L-14-336/);
  assert.match(JSON.stringify(sections), /settings unavailable/);
});

test("Info fetches one read-only configuration snapshot per mount and refresh, without polling", async () => {
  const app = harness();
  try {
    assert.equal(app.requests.length, 1);
    assert.ok(app.find((node) => node.props?.role === "status"));
    assert.equal(app.find((node) => node.type === "button" && node.props.disabled)?.props.disabled, true);
    await app.resolve(0);
    await app.render(); await app.render();
    assert.equal(app.requests.length, 1, "rendering loaded content must not schedule more network work");
    assert.match(text(app.state), /custom-vision/);
    app.find((node) => node.type === "button" && text(node) === "Refresh settings").props.onClick();
    await app.flush();
    assert.equal(app.requests.length, 2);
    assert.equal(app.requests[0].init.signal.aborted, true);
    for (const request of app.requests) {
      assert.equal(request.url, "https://api.example.test/project/info");
      assert.equal(request.init.method ?? "GET", "GET");
      assert.equal(request.init.body, undefined);
      assert.equal(request.init.cache, "no-store");
    }
    assert.deepEqual(app.timers, []);
  } finally { app.dispose(); }
});

test("network, HTTP and unsupported-schema errors preserve a readable guide and explicit retry", async () => {
  for (const failure of ["network", "http", "schema"]) {
    const app = harness();
    try {
      if (failure === "network") await app.reject(0);
      else await app.resolve(0, failure === "schema" ? { ...settings(), schema_version: 2 } : {}, failure !== "http");
      assert.match(text(app.find((node) => node.props?.role === "status")), /unavailable.*guide is still available/i, failure);
      assert.equal(text(app.find((node) => node.props?.id === "info-topic-heading")), "Ingestion", failure);
      const navigation = app.find((node) => node.type === "nav" && node.props["aria-label"] === "Guide topics");
      nodes(navigation).find((node) => node.type === "button" && text(node).includes("Search")).props.onClick();
      await app.flush();
      assert.equal(text(app.find((node) => node.props?.id === "info-topic-heading")), "Search", failure);
      assert.ok(app.find((node) => node.type === "h3" && text(node) === "Retrieve candidates"), failure);
      assert.equal(app.requests.length, 1, "offline topic navigation must remain local");
      assert.doesNotMatch(text(app.state), /undefined|NaN/);
      const retry = app.find((node) => node.type === "button" && text(node) === "Refresh settings");
      assert.equal(retry.props.disabled, false);
      retry.props.onClick(); await app.flush();
      await app.resolve(1);
      assert.match(text(app.state), /custom-visual/);
      assert.doesNotMatch(text(app.find((node) => node.props?.role === "status")), /unavailable/i);
      assert.equal(app.requests.length, 2);
    } finally { app.dispose(); }
  }
});

test("unmount aborts the pending request and ignores a late success or failure", async () => {
  for (const outcome of ["success", "failure"]) {
    const app = harness();
    app.dispose();
    assert.equal(app.requests[0].init.signal.aborted, true);
    if (outcome === "success") await app.resolve(0);
    else await app.reject(0);
    assert.equal(app.lateUpdates, 0, outcome);
    assert.equal(app.requests.length, 1);
  }
});

test("topic navigation exposes one labelled article with every step visible and noninteractive diagrams", async () => {
  const app = harness();
  const sections = guide.buildGuide(null);
  const stepTitles = new Set(sections.flatMap((topic) => topic.steps.map((step) => step.title)));
  const buttons = () => nodes(app.find((node) => node.type === "nav" && node.props["aria-label"] === "Guide topics")).filter((node) => node.type === "button");
  try {
    const headingId = app.state.props["aria-labelledby"];
    assert.ok(app.find((node) => node.type === "h1" && node.props.id === headingId));
    assert.equal(buttons().length, 4);
    assert.equal(text(app.find((node) => node.props?.id === "info-topic-heading")), "Ingestion");
    assert.ok(text(buttons().find((node) => node.props["aria-current"] === "page")).includes("Ingestion"));

    for (const topic of sections) {
      const displayTitle = topic.id === "editing" ? "Editing" : topic.title;
      const button = buttons().find((node) => text(node).includes(displayTitle));
      assert.ok(button, topic.title);
      assert.equal(button.props.type, "button");
      assert.notEqual(button.props.role, "tab", "topic navigation uses ordinary buttons, not a partial tab widget");
      assert.notEqual(button.props.disabled, true, "topics remain available while settings load");
      button.props.onClick(); await app.flush();

      const selected = buttons().filter((node) => node.props["aria-current"] === "page");
      assert.equal(selected.length, 1);
      assert.ok(text(selected[0]).includes(displayTitle));
      for (const other of buttons().filter((node) => node !== selected[0])) assert.notEqual(other.props["aria-current"], "page");
      const articles = nodes(app.state).filter((node) => node.type === "article");
      assert.equal(articles.length, 1);
      const article = articles[0];
      assert.equal(article.props["aria-labelledby"], "info-topic-heading");
      assert.equal(text(nodes(article).find((node) => node.type === "h2" && node.props.id === "info-topic-heading")), displayTitle);
      assert.deepEqual(nodes(article).filter((node) => node.type === "h3" && stepTitles.has(text(node))).map(text), Array.from(topic.steps, (step) => step.title));
      for (const step of topic.steps) {
        const visibleSection = nodes(article).find((node) => node.type === "section" && nodes(node).some((child) => child.type === "h3" && text(child) === step.title));
        assert.ok(visibleSection, step.id);
        assert.notEqual(visibleSection.props.hidden, true);
        assert.ok(![true, "true"].includes(visibleSection.props["aria-hidden"]));
        assert.ok(text(visibleSection).includes(step.method), step.id);
        assert.ok(text(visibleSection).includes(step.output), step.id);
      }
      const diagram = nodes(article).find((node) => node.type === (topic.id === "storage" ? "ul" : "ol") && node.props["aria-label"] === `${topic.title} ${topic.id === "storage" ? "ownership" : "process"} diagram`);
      assert.ok(diagram, topic.id);
      assert.equal(nodes(diagram).filter((node) => node.type === "li").length, topic.steps.length);
      assert.equal(nodes(diagram).filter((node) => node.type === "button" || node.type === "a" || node.props?.onClick || node.props?.tabIndex !== undefined).length, 0);
      assert.equal(nodes(app.state).filter((node) => node.type === "details" || node.type === "summary").length, 0);
      assert.equal(app.requests.length, 1, "topic navigation must not fetch settings or start work");
    }
  } finally { app.dispose(); }
});

test("the selected topic survives loading and refreshing live settings", async () => {
  const app = harness();
  const topicHeading = () => text(app.find((node) => node.props?.id === "info-topic-heading"));
  try {
    const navigation = app.find((node) => node.type === "nav" && node.props["aria-label"] === "Guide topics");
    nodes(navigation).find((node) => node.type === "button" && text(node).includes("Search")).props.onClick();
    await app.flush();
    assert.equal(topicHeading(), "Search");
    await app.resolve(0);
    assert.equal(topicHeading(), "Search");
    app.find((node) => node.type === "button" && text(node) === "Refresh settings").props.onClick();
    await app.flush();
    assert.equal(topicHeading(), "Search");
    const changed = settings(); changed.models.visual_encoder = "refreshed-visual";
    await app.resolve(1, changed);
    assert.equal(topicHeading(), "Search");
    assert.match(text(app.find((node) => node.type === "article")), /refreshed-visual/);
    assert.equal(app.requests.length, 2);
    assert.deepEqual(app.timers, []);
  } finally { app.dispose(); }
});
