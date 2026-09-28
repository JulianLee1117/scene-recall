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
vm.runInNewContext(compile("matchFrames.ts"), { exports: helpers });
const compiled = compile("MatchSourcePlayer.tsx");
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };

async function harness(reference = 12) {
  const hooks = [], effects = [], requests = [], patches = [], points = [], timing = [];
  const listeners = new Map();
  let cursor = 0, scheduled = false, tree, mediaTime = reference;
  const player = {
    paused: true, seeking: false, videoWidth: 1920, videoHeight: 1080,
    get currentTime() { return mediaTime; },
    set currentTime(value) { mediaTime = value; this.seeking = true; },
    pause() { this.paused = true; },
    play: async () => {},
    addEventListener(event, callback) { listeners.set(event, callback); },
    removeEventListener(event, callback) { if (listeners.get(event) === callback) listeners.delete(event); },
    completeSeek() { this.seeking = false; listeners.get("seeked")?.(); },
  };
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
  const props = {
    clip: { id: "a", unit_id: "shot-a", film_id: "film-a", source_start: 11, source_end: 14, reference_time: reference, locked: false, region: { x: 0.1, y: 0.1, width: 0.3, height: 0.3 } },
    disabled: false, timing: "nearby", subjectReady: true, subjectPoint: { x: 0.2, y: 0.4 },
    onTiming: (value) => timing.push(value), onSubjectPoint: (value) => points.push(value), onChange: (value) => patches.push(value),
  };
  const exported = {};
  const jsx = (type, props) => { if (type === "video") props.ref.current = player; return { type, props }; };
  vm.runInNewContext(compiled, {
    exports: exported, AbortController, setTimeout, clearTimeout,
    require(name) {
      if (name === "react") return react;
      if (name === "react/jsx-runtime") return { jsx, jsxs: jsx, Fragment: "fragment" };
      if (name === "./matchFrames") return helpers;
      if (name === "@/lib/lab") return {
        mediaUrl: (value) => value, seconds: (value) => value.toFixed(3),
        labRequest: async (route) => {
          requests.push(route);
          return { t_start: 10, t_end: 20, frames: [{ time: 12, end: 12.05 }, { time: 15, end: 15.067 }, { time: 15.067, end: 15.1 }] };
        },
      };
      return { default: new Proxy({}, { get: (_, key) => key }) };
    },
  });
  function render() { cursor = 0; scheduled = false; tree = exported.default(props); effects.splice(0).forEach((effect) => effect()); }
  render(); await flush();
  nodes(tree).find((node) => node.type === "video").props.onLoadedMetadata({ currentTarget: player });
  player.completeSeek(); await flush();
  return { player, requests, patches, points, timing, get nodes() { return nodes(tree); }, button(label) { return nodes(tree).find((node) => node.type === "button" && text(node) === label); } };
}

test("subject selection binds the current displayed frame before accepting a prompt", async () => {
  const ui = await harness();
  ui.player.currentTime = 15.03; // Playback moved, but timeupdate has not yet fired.
  ui.player.completeSeek();
  ui.button("Choose a subject").props.onClick(); await flush();
  assert.ok(ui.requests.at(-1).endsWith("&time=15.03"));
  assert.equal(ui.nodes.some((node) => node.props?.["aria-label"] === "Click a subject"), false);
  assert.equal(ui.patches.length, 0);
  ui.player.completeSeek(); await flush();
  assert.equal(ui.patches.at(-1).reference_time, 15);
  assert.equal(ui.patches.at(-1).region, null);
  assert.deepEqual(ui.points, [null]);
  const surface = ui.nodes.find((node) => node.props?.["aria-label"] === "Click a subject");
  surface.props.onPointerDown({ clientX: 25, clientY: 50, pointerId: 1, currentTarget: { setPointerCapture() {}, getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 100 }) } });
  assert.deepEqual(JSON.parse(JSON.stringify(ui.points.at(-1))), { x: 0.25, y: 0.5 });
});

test("pinning within the same variable-length frame preserves existing subject focus", async () => {
  const ui = await harness(15);
  ui.player.currentTime = 15.03;
  ui.player.completeSeek();
  const done = ui.button("Pin this frame").props.onClick(); await flush();
  ui.player.completeSeek(); await done; await flush();
  assert.equal(ui.patches.at(-1).reference_time, 15);
  assert.equal("region" in ui.patches.at(-1), false);
  assert.deepEqual(ui.points, []);
  assert.deepEqual(ui.timing, ["fixed"]);
});
