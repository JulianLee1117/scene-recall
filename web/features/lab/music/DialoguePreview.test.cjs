const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const compile = (name) => ts.transpileModule(fs.readFileSync(path.join(__dirname, name), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const flush = async () => { for (let i = 0; i < 16; i++) await Promise.resolve(); };
const nodes = (node) => !node || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);

async function setup() {
  const hooks = [], effects = [], changes = [], helpers = {}, gain = {}, transport = {}, exported = {};
  let cursor = 0, scheduled = false, disposed = false, tree, clock, time = 0;
  const media = { paused: true, ended: false, readyState: 4, seeking: false, duration: 2, error: null, volume: 1,
    get currentTime() { return time; }, set currentTime(value) { time = value; this.ended = false; },
    pause() { this.paused = true; }, async play() { this.paused = false; }, load() { this.error = null; },
  };
  const props = { clip: { id: "voice", film_id: "film", start: 14, source_start: 100, source_end: 102, gain_db: 0,
    fade_in_seconds: .08, fade_out_seconds: .12, music_duck_db: -8, source_audio_mode: "voice_focus" },
    disabled: false, suspended: false, onPlayingChange: (value) => changes.push(value) };
  const schedule = () => { if (!scheduled && !disposed) { scheduled = true; queueMicrotask(render); } };
  const react = {
    useRef: (value) => hooks[cursor++] ??= { current: value },
    useState(initial) { const index = cursor++; hooks[index] ??= { value: initial };
      return [hooks[index].value, (next) => { const value = typeof next === "function" ? next(hooks[index].value) : next;
        if (!Object.is(value, hooks[index].value)) { hooks[index].value = value; schedule(); } }]; },
    useEffect(effect, deps) { const index = cursor++, old = hooks[index];
      if (!old || deps.some((value, i) => !Object.is(value, old.deps[i]))) {
        hooks[index] = { deps, cleanup: old?.cleanup };
        effects.push(() => { old?.cleanup?.(); hooks[index].cleanup = effect(); });
      }
    },
  };
  vm.runInNewContext(compile("dialogueAudio.ts"), { exports: helpers });
  vm.runInNewContext(compile("dialogueGain.ts"), { exports: gain });
  const dependencies = (name) => {
    if (name === "./dialogueAudio") return helpers;
    if (name === "./dialogueGain") return gain;
    if (name === "./dialogueTransport") return transport;
    if (name === "@/lib/lab") return { mediaUrl: (url) => url };
    if (name === "react") return react;
    if (name === "react/jsx-runtime") return { jsx, jsxs: jsx };
    return { default: name.endsWith(".css") ? new Proxy({}, { get: (_, key) => key }) : name };
  };
  vm.runInNewContext(compile("dialogueTransport.ts"), { exports: transport, AbortController, URLSearchParams, require: dependencies });
  const jsx = (type, props) => { if (type === "audio") props.ref.current = media; return { type, props }; };
  vm.runInNewContext(compile("DialoguePreview.tsx"), { exports: exported, require: dependencies,
    window: { setInterval(callback) { clock = callback; return 1; }, clearInterval() {},
      document: { hidden: false, addEventListener() {}, removeEventListener() {} } },
  });
  function render() { if (disposed) return; cursor = 0; scheduled = false; tree = exported.default(props); effects.splice(0).forEach((effect) => effect()); }
  render(); await flush();
  return { media, changes,
    get text() { return text(tree); },
    async click() { nodes(tree).find((node) => node.type === "button").props.onClick(); await flush(); clock(); await flush(); },
    async tick(value, ended = false) { media.currentTime = value; media.ended = ended; if (ended) media.paused = true; clock(); await flush(); },
    async update(patch) { Object.assign(props, patch); render(); await flush(); },
    dispose() { disposed = true; hooks.forEach((hook) => hook.cleanup?.()); },
  };
}

test("source audition uses prepared audio offsets, stops at EOF, and replay starts at the selected source in", async () => {
  const app = await setup();
  try {
    await app.click();
    assert.equal(app.media.paused, false); assert.equal(app.media.currentTime, 0);
    const url = new URL(app.media.src, "http://localhost");
    assert.equal(url.searchParams.get("source_start"), "100");
    assert.equal(url.searchParams.get("source_audio_mode"), "voice_focus");
    await app.tick(.5); assert.equal(app.media.currentTime, .5, "audition clock does not add the original film offset twice");
    await app.tick(2, true); assert.equal(app.media.paused, true); assert.ok(app.text.includes("Listen to voice"));
    assert.equal(app.changes.at(-1), false);
    await app.click(); assert.equal(app.media.currentTime, 0); assert.equal(app.media.paused, false);
    await app.update({ suspended: true }); assert.equal(app.media.paused, true);
  } finally { app.dispose(); }
  assert.equal(app.media.paused, true);
});
