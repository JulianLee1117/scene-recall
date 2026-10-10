const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const compile = (file) => ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const moments = {};
vm.runInNewContext(compile("../lib/resultMoment.ts"), { exports: moments });
const compiled = compile("ShotCard.tsx");
const shot = {
  unit_id: "swim", film_id: "moonlight", film_title: "Moonlight", t_start: 1102, t_end: 1119,
  caption: "Chiron floats.", keyframe_url: "/media/keyframe/swim/2", keyframe_index: 2,
  thumbnail_url: "/media/hero/swim", hero_url: "/media/hero/swim", hero_time: 1105,
  preview_url: "/media/preview/swim", focus_start: 1102, focus_end: 1107,
};

function harness(initial) {
  const hooks = [];
  let cursor = 0, output;
  const props = { shot: initial, position: 1, showDetails: false, onClick() {} };
  const exports = {};
  const videoElement = { play: () => Promise.resolve(), pause() {}, currentTime: 0 };
  vm.runInNewContext(compiled, {
    exports, process: { env: {} },
    require(name) {
      if (name === "react") return {
        useId: () => "shot-card",
        useState(initial) {
          const i = cursor++; if (!(i in hooks)) hooks[i] = initial;
          return [hooks[i], (next) => { hooks[i] = typeof next === "function" ? next(hooks[i]) : next; }];
        },
        useRef(initial) { const i = cursor++; return hooks[i] ??= { current: initial }; },
        useCallback: (callback) => callback,
      };
      if (name === "react/jsx-runtime") return { jsx: (type, props, key) => ({ type, props, key }), jsxs: (type, props, key) => ({ type, props, key }) };
      if (name === "@/lib/resultMoment") return moments;
      if (name === "@/lib/format") return { displayTitle: String, formatTime: String, filmLabel: String };
      if (name === "@/lib/matchReasons") return { hoverEvidence: () => ({ text: "Chiron floats." }), matchedWordsEvidence: () => null };
      if (name === "@/hooks/useScenePointerDrag") return { useScenePointerDrag: () => ({}) };
      return { default: name };
    },
  });
  function render() {
    cursor = 0; output = exports.default(props);
    const video = nodes(output).find((node) => node.type === "video");
    for (const hook of hooks) if (hook && typeof hook === "object" && "current" in hook && (hook.current === null || hook.current === videoElement)) hook.current = video ? videoElement : null;
  }
  render();
  return {
    find: (type) => nodes(output).find((node) => node.type === type),
    event(type, event) { nodes(output).find((node) => node.type === type).props[event](); render(); },
    update(next) { props.shot = next; render(); },
  };
}

test("hover keeps the still until a preview plays and restores it after playback failure", () => {
  const app = harness(shot);
  app.event("article", "onMouseEnter");
  assert.equal(app.find("img").props.style.opacity, 1, "loading must not blank the thumbnail");
  app.event("video", "onPlaying");
  assert.equal(app.find("img").props.style.opacity, 0);
  assert.equal(app.find("video").props.style.opacity, 1);
  app.event("video", "onError");
  assert.equal(app.find("img").props.style.opacity, 1);
  assert.equal(app.find("video").props.style.opacity, 0);
});

test("Saved hover keeps its exact still when the current preview shows another picture or is unknown", () => {
  for (const item of [
    { ...shot, evidence_timestamp: 1115, thumbnail_url: "/media/frame/moonlight?t=1115" },
    { ...shot, evidence_timestamp: 1105, focus_start: undefined },
    { ...shot, preview_url: "" },
  ]) {
    const app = harness(item);
    app.event("article", "onMouseEnter");
    assert.equal(app.find("video"), undefined);
    assert.equal(app.find("img").props.style.opacity, 1);
    assert.equal(app.find("img").props.src, item.thumbnail_url);
    assert.match(app.find("button").props["aria-label"], /Chiron floats/,
      "the accessible description follows the same character-aware hover text");
  }
});

test("Saved retains previews for its current visual span and never reuses another asset's playback state", () => {
  const app = harness({ ...shot, evidence_timestamp: 1105 });
  app.event("article", "onMouseEnter");
  app.event("video", "onPlaying");
  assert.equal(app.find("img").props.style.opacity, 0);
  app.update({ ...shot, evidence_timestamp: 1105, preview_url: "/media/preview/swim?focus=1103" });
  assert.equal(app.find("img").props.style.opacity, 1);
  app.event("video", "onPlaying");
  assert.equal(app.find("img").props.style.opacity, 0);
  app.event("article", "onMouseLeave");
  app.event("article", "onMouseEnter");
  assert.equal(app.find("img").props.style.opacity, 1);
});
