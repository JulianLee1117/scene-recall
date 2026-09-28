const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const compile = (name, jsx = false) => ts.transpileModule(fs.readFileSync(path.join(__dirname, name), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, ...(jsx ? { jsx: ts.JsxEmit.ReactJSX } : {}) } }).outputText;
const helpers = {}; vm.runInNewContext(compile("transitions.ts"), { exports: helpers });
const interaction = {}; vm.runInNewContext(compile("source-interaction.ts"), { exports: interaction });
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const flush = async () => { for (let i = 0; i < 25; i++) await Promise.resolve(); };

async function harness(overrides = {}, cold = false, options = {}) {
  const hooks = [], effects = [], exported = {}, changes = [];
  let cursor = 0, scheduled = false, tree, video, videoKey;
  let props = { label: "A", disabled: false, source: { film_id: "film", title: "Film", source_start: 10, source_end: 13, framing: { fit: "fill", anchor_x: .2, anchor_y: .8, zoom: 1.2 } }, endpoint: { url: "/frame-a.jpg", time: 12.95 }, onChoose() {}, onChange: (value) => changes.push(value), ...overrides };
  const same = (a, b) => a && b && a.length === b.length && a.every((item, index) => Object.is(item, b[index]));
  const schedule = () => { if (!scheduled) { scheduled = true; queueMicrotask(render); } };
  const react = {
    useState(initial) { const index = cursor++; hooks[index] ??= { value: typeof initial === "function" ? initial() : initial }; return [hooks[index].value, (change) => { const next = typeof change === "function" ? change(hooks[index].value) : change; if (!Object.is(next, hooks[index].value)) { hooks[index].value = next; schedule(); } }]; },
    useRef(initial) { return hooks[cursor++] ??= { current: initial }; },
    useEffect(effect, deps) { const index = cursor++; if (!hooks[index] || !same(hooks[index].deps, deps)) { const cleanup = hooks[index]?.cleanup; hooks[index] = { deps, cleanup }; effects.push(() => { hooks[index].cleanup?.(); hooks[index].cleanup = effect(); }); } },
  };
  vm.runInNewContext(compile("SourceCard.tsx", true), {
    exports: exported, AbortController, fetch: options.fetch ?? (async () => ({ ok: true, json: async () => ({ url: "/video/film" }) })),
    require(name) {
      if (name === "react") return react;
      if (name === "react/jsx-runtime") return { jsx: (type, props, key) => ({ type, props, key }), jsxs: (type, props, key) => ({ type, props, key }), Fragment: "fragment" };
      if (name === "./transitions") return helpers;
      if (name === "./source-interaction") return interaction;
      if (name === "@/lib/lab") return { mediaUrl: (value) => value, seconds: (value) => value.toFixed(2), LAB_API: "" };
      return { default: new Proxy({}, { get: (_, key) => key }) };
    },
  });
  function render() {
    cursor = 0; scheduled = false; tree = exported.default(props);
    const element = nodes(tree).find((node) => node.type === "video");
    if (element) {
      if (!video || element.key !== videoKey) {
        const replacement = !!video;
        videoKey = element.key;
        video = { currentTime: 0, duration: cold || replacement ? NaN : 20, videoWidth: 1920, videoHeight: 1080, readyState: cold || replacement ? 0 : 4, paused: true, playCount: 0, pause() { this.paused = true; }, play() { this.paused = false; this.playCount++; return Promise.resolve(); } };
      }
      // React attaches the keyed DOM element before passive effects run.
      element.props.ref.current = video;
    }
    effects.splice(0).forEach((effect) => effect());
  }
  render(); await flush();
  const videoNode = nodes(tree).find((node) => node.type === "video");
  if (!cold) { videoNode.props.onLoadedMetadata({ currentTarget: video }); await flush(); }
  return { get video() { return video; }, changes, get tree() { return tree; }, get nodes() { return nodes(tree); },
    button(label) { return nodes(tree).find((node) => node.type === "button" && text(node) === label); },
    async update(patch) { props = { ...props, ...patch }; schedule(); await flush(); },
    async metadata() { video.readyState = 4; if (!Number.isFinite(video.duration)) video.duration = 20; nodes(tree).find((node) => node.type === "video").props.onLoadedMetadata({ currentTarget: video }); await flush(); },
  };
}

test("matching native endpoint is shown initially with its timestamp and is never used as a new out point", async () => {
  const ui = await harness();
  const image = ui.nodes.find((node) => node.type === "img"), video = ui.nodes.find((node) => node.type === "video");
  assert.equal(image.props.src, "/frame-a.jpg"); assert.equal(image.props.alt, "Retained last outgoing source frame");
  assert.deepEqual(image.props.style, video.props.style, "still and browser video use the same per-clip framing");
  assert.match(text(ui.tree), /12.95/); assert.match(text(ui.tree), /Saved endpoint/);
  assert.equal(ui.button("Use playhead").props.disabled, true);
  ui.button("Use playhead").props.onClick(); assert.equal(ui.changes.length, 0);
});

test("monitor mode has one shared timeline while keeping playback and numeric trim controls", async () => {
  const ui = await harness({ monitor: true });
  assert.equal(ui.nodes.some((node) => node.props?.className === "trimRail"), false);
  assert.equal(ui.nodes.some((node) => node.props?.["aria-label"] === "Clip A source position in seconds"), false);
  assert.ok(ui.button("Play clip"));
  assert.ok(ui.nodes.find((node) => node.type === "input" && node.props.type === "number"));
  assert.ok(ui.nodes.find((node) => node.type === "video"));
});

test("external source inspection uses full-film seconds without changing the selected window", async () => {
  const positions = [], durations = [];
  const ui = await harness({ monitor: true, onPositionChange: (time) => positions.push(time), onDurationKnown: (duration) => durations.push(duration) }, true);
  await ui.update({ inspection: { film: "film", time: 38.75, token: 1 } });
  assert.equal(ui.nodes.find((node) => node.type === "video").props.src, "/video/film");
  ui.video.duration = 90; await ui.metadata();
  assert.equal(ui.video.currentTime, 38.75);
  assert.equal(positions.at(-1), 38.75);
  assert.deepEqual(durations, [90]);
  assert.equal(ui.video.playCount, 0); assert.equal(ui.changes.length, 0);
  await ui.update({ inspection: { film: "another-film", time: 55, token: 2 } });
  assert.equal(ui.video.currentTime, 38.75, "an inspection from another source cannot seek the current film");
});

test("an inactive source monitor defers inspection once and does not rewind on later reactivation", async () => {
  const positions = [];
  const ui = await harness({ monitor: true, disabled: true, onPositionChange: (time) => positions.push(time) });
  await ui.update({ inspection: { film: "film", time: 11.4, token: 1 } });
  assert.equal(ui.video.currentTime, 12.9); assert.deepEqual(positions, []);
  await ui.update({ disabled: false });
  assert.equal(ui.video.currentTime, 11.4);
  ui.video.currentTime = 11.8;
  ui.nodes.find((node) => node.type === "video").props.onTimeUpdate({ currentTarget: ui.video }); await flush();
  assert.equal(positions.at(-1), 11.8);
  await ui.update({ disabled: true });
  assert.equal(ui.video.paused, true);
  const reported = positions.length;
  ui.video.currentTime = 11.9;
  ui.nodes.find((node) => node.type === "video").props.onTimeUpdate({ currentTarget: ui.video }); await flush();
  assert.equal(positions.length, reported, "a hidden monitor cannot move the shared timeline");
  await ui.update({ disabled: false });
  assert.equal(ui.video.currentTime, 11.9, "an already consumed inspection is not replayed on monitor activation");
  await ui.update({ disabled: true, inspection: { film: "film", time: 10.7, token: 2 } });
  await ui.update({ disabled: false });
  assert.equal(ui.video.currentTime, 10.7, "a newer inspection made while hidden is still applied");
});

test("old-film metadata and media events cannot alter the new monitor or its pending Play", async () => {
  const durations = [], positions = [];
  const ui = await harness({ monitor: true, onPositionChange: (time) => positions.push(time), onDurationKnown: (duration) => durations.push(duration) });
  const oldVideo = ui.video, oldElement = ui.nodes.find((node) => node.type === "video");
  await ui.update({ endpoint: undefined, source: { film_id: "new-film", title: "New film", source_start: 40, source_end: 43 }, inspection: { film: "new-film", time: 41.5, token: 5 } });
  const newVideo = ui.video;
  assert.notEqual(newVideo, oldVideo); assert.equal(oldVideo.paused, true);
  oldVideo.duration = 10;
  oldElement.props.onLoadedMetadata({ currentTarget: oldVideo }); await flush();
  assert.deepEqual(durations, [20], "detached metadata cannot assign the old film's duration to the current source");
  const lastPosition = positions.at(-1);
  oldVideo.currentTime = 9;
  oldElement.props.onTimeUpdate({ currentTarget: oldVideo }); await flush();
  assert.equal(positions.at(-1), lastPosition, "a detached film's final timeupdate cannot move the shared playhead");
  ui.button("Play clip").props.onClick(); await flush();
  assert.ok(ui.button("Cancel loading"));
  oldElement.props.onPlay({ currentTarget: oldVideo }); await flush();
  assert.ok(ui.button("Cancel loading"), "a detached play event cannot mark the new cold source as playing");
  oldElement.props.onError({ currentTarget: oldVideo }); await flush();
  assert.ok(ui.button("Cancel loading"), "an old source error cannot cancel the new source's Play intent");
  newVideo.duration = 70; await ui.metadata();
  assert.equal(newVideo.currentTime, 41.5); assert.equal(newVideo.playCount, 1);
  assert.deepEqual(durations, [20, 70]);
  ui.nodes.find((node) => node.type === "video").props.onPlay({ currentTarget: newVideo }); await flush();
  oldElement.props.onPause({ currentTarget: oldVideo }); await flush();
  assert.ok(ui.button("Pause"), "a detached pause event cannot change the current transport state");
});

test("a source inspection waiting for its film is not consumed by the earlier source", async () => {
  const inspection = { film: "next-film", time: 51.2, token: 8 };
  const ui = await harness({ monitor: true, inspection });
  assert.equal(ui.video.currentTime, 12.9);
  await ui.update({ endpoint: undefined, source: { film_id: "next-film", title: "Next film", source_start: 50, source_end: 53 } });
  ui.video.duration = 80; await ui.metadata();
  assert.equal(ui.video.currentTime, 51.2, "the unchanged pending token is applied once its film becomes current");
  assert.equal(ui.video.playCount, 0); assert.equal(ui.changes.length, 0);
});

test("late playback discovery from an earlier film cannot reattach its full source", async () => {
  const requests = [];
  const ui = await harness({ monitor: true }, true, { fetch: (url, options) => new Promise((resolve) => requests.push({ url, options, resolve })) });
  await ui.update({ endpoint: undefined, source: { film_id: "new-film", title: "New film", source_start: 40, source_end: 43 }, inspection: { film: "new-film", time: 41, token: 7 } });
  assert.equal(requests.length, 2); assert.equal(requests[0].options.signal.aborted, true);
  requests[1].resolve({ ok: true, json: async () => ({ url: "/video/new-film" }) }); await flush();
  assert.equal(ui.nodes.find((node) => node.type === "video").props.src, "/video/new-film");
  requests[0].resolve({ ok: true, json: async () => ({ url: "/video/old-film" }) }); await flush();
  assert.equal(ui.nodes.find((node) => node.type === "video").props.src, "/video/new-film");
  ui.video.duration = 70; await ui.metadata();
  assert.equal(ui.video.currentTime, 41); assert.equal(ui.video.playCount, 0);
});

test("a retained endpoint avoids full-film loading until explicit playback and loads only once", async () => {
  const ui = await harness({}, true);
  const media = () => ui.nodes.find((node) => node.type === "video");
  assert.equal(media().props.src, undefined); assert.equal(media().props.preload, "none");
  ui.button("Play clip").props.onClick(); await flush();
  assert.equal(media().props.src, "/video/film"); assert.equal(ui.video.playCount, 0);
  assert.ok(ui.button("Cancel loading"));
  await ui.metadata();
  assert.equal(ui.video.playCount, 1); assert.equal(ui.video.currentTime, 10);
  ui.button("Inspect end").props.onClick(); await flush();
  assert.equal(media().props.src, "/video/film", "explicitly loaded source stays attached during endpoint inspection");
  await ui.metadata(); assert.equal(ui.video.playCount, 1, "another metadata event cannot repeat the prior play intent");
});

test("scrubbing or cancelling pending source loading cannot autoplay after metadata arrives", async () => {
  for (const action of ["scrub", "cancel", "inspect", "disable"]) {
    const ui = await harness({}, true);
    ui.button("Play clip").props.onClick(); await flush();
    if (action === "scrub") ui.nodes.find((node) => node.props?.["aria-label"] === "Clip A source position in seconds").props.onChange({ target: { value: "11.4" } });
    if (action === "cancel") ui.button("Cancel loading").props.onClick();
    if (action === "inspect") ui.button("Inspect end").props.onClick();
    if (action === "disable") await ui.update({ disabled: true });
    await flush(); await ui.metadata();
    assert.equal(ui.video.playCount, 0, action);
    if (action === "scrub") assert.equal(ui.video.currentTime, 11.4);
  }
});

test("Play requested after a cold source scrub begins at the explicit scrub position", async () => {
  const ui = await harness({}, true);
  ui.nodes.find((node) => node.props?.["aria-label"] === "Clip A source position in seconds").props.onChange({ target: { value: "11.4" } }); await flush();
  ui.button("Play clip").props.onClick(); await flush(); await ui.metadata();
  assert.equal(ui.video.currentTime, 11.4); assert.equal(ui.video.playCount, 1);
});

test("entering or adjusting Reframe cancels pending playback before late source metadata", async () => {
  for (const action of ["enter", "drag", "keyboard"]) {
    const ui = await harness({}, true);
    ui.nodes.find((node) => node.type === "img").props.onLoad({ currentTarget: { naturalWidth: 1920, naturalHeight: 1080 } }); await flush();
    if (action !== "enter") { ui.button("Reframe").props.onClick(); await flush(); }
    ui.button("Play clip").props.onClick(); await flush();
    assert.ok(ui.button("Cancel loading"));
    if (action === "enter") ui.button("Reframe").props.onClick();
    else {
      const surface = ui.nodes.find((node) => node.props?.className === "frameSurface");
      const currentTarget = { focus() {}, setPointerCapture() {}, getBoundingClientRect: () => ({ width: 100, height: 200 }) };
      if (action === "drag") surface.props.onPointerDown({ pointerId: 2, button: 0, clientX: 50, clientY: 100, currentTarget, preventDefault() {} });
      else surface.props.onKeyDown({ key: "ArrowRight", shiftKey: false, currentTarget, preventDefault() {} });
    }
    await flush(); await ui.metadata();
    assert.equal(ui.video.playCount, 0, `${action} invalidates the earlier play intent`);
    assert.equal(ui.video.paused, true); assert.ok(ui.button("Play clip"));
    assert.ok(ui.button("Done framing"), "metadata does not end the user's framing session");
    assert.equal(ui.nodes.find((node) => node.type === "video").props.src, "/video/film", "the explicitly loaded source stays attached");
  }
});

test("scrubbing and playing clear the retained still; Inspect restores it", async () => {
  const ui = await harness();
  ui.nodes.find((node) => node.props?.["aria-label"] === "Clip A source position in seconds").props.onChange({ target: { value: "11.5" } }); await flush();
  assert.equal(ui.nodes.some((node) => node.type === "img"), false); assert.equal(ui.video.currentTime, 11.5);
  assert.equal(ui.button("Use playhead").props.disabled, false);
  ui.button("Inspect end").props.onClick(); await flush(); assert.ok(ui.nodes.some((node) => node.type === "img"));
  ui.button("Play clip").props.onClick(); await flush(); assert.equal(ui.nodes.some((node) => node.type === "img"), false);
  assert.equal(ui.video.currentTime, 10, "Play clip starts the selected clip after inspecting its retained endpoint");
});

test("a mismatched or invalid source clears the still and labels browser inspection as approximate", async () => {
  const ui = await harness();
  await ui.update({ endpoint: undefined });
  assert.equal(ui.nodes.some((node) => node.type === "img"), false);
  assert.match(ui.button("Near end").props.title, /Browser seeking is approximate/);
  await ui.update({ endpoint: { url: "/frame-a.jpg", time: 12.95 }, source: { film_id: "film", title: "Film", source_start: 14, source_end: 15 } });
  assert.equal(ui.nodes.some((node) => node.type === "img"), false, "an endpoint outside the source window cannot be displayed");
  assert.ok(ui.button("Near end"));
});

test("browser endpoint inspection restarts the full clip while deliberate near-end scrubbing is preserved", async () => {
  const ui = await harness({ endpoint: undefined });
  assert.equal(ui.video.currentTime, 12.9);
  ui.button("Play clip").props.onClick(); await flush();
  assert.equal(ui.video.currentTime, 10, "initial nearby endpoint inspection restarts from in");
  ui.button("Near end").props.onClick(); await flush();
  assert.equal(ui.video.currentTime, 12.9);
  ui.button("Play clip").props.onClick(); await flush();
  assert.equal(ui.video.currentTime, 10, "explicit Near end is inspection rather than a resume point");
  const scrub = () => ui.nodes.find((node) => node.props?.["aria-label"] === "Clip A source position in seconds");
  scrub().props.onChange({ target: { value: "12.995" } }); await flush();
  ui.button("Play clip").props.onClick(); await flush();
  assert.equal(ui.video.currentTime, 12.995, "manual scrubbing right beside out is not eaten by an endpoint heuristic");
  ui.video.currentTime = 13; ui.video.paused = false;
  ui.nodes.find((node) => node.type === "video").props.onTimeUpdate({ currentTarget: ui.video }); await flush();
  ui.button("Play clip").props.onClick(); await flush();
  assert.equal(ui.video.currentTime, 10, "after the selected clip ends the next play starts the full clip");
});

test("direct trim dragging freezes the timeline scale and keeps the other boundary fixed", async () => {
  const ui = await harness();
  ui.nodes.find((node) => node.props?.className === "trimRail").props.ref.current = { getBoundingClientRect: () => ({ left: 50, width: 300 }) };
  const handle = () => ui.nodes.find((node) => node.props?.["aria-label"] === "Clip A out trim");
  const currentTarget = { focus() {}, setPointerCapture() {} };
  handle().props.onPointerDown({ pointerId: 1, button: 0, clientX: 295, currentTarget, preventDefault() {} }); await flush();
  handle().props.onPointerMove({ pointerId: 1, clientX: 265 }); await flush();
  let changed = ui.changes.at(-1);
  assert.equal(changed.source_start, 10); assert.equal(changed.source_end, 12.5);
  await ui.update({ source: changed, endpoint: undefined });
  handle().props.onPointerMove({ pointerId: 1, clientX: 235 }); await flush();
  changed = ui.changes.at(-1);
  assert.equal(changed.source_start, 10); assert.equal(changed.source_end, 12, "second move uses the original scale, not the changing selected duration");
  await ui.update({ source: changed });
  assert.ok(Math.abs(ui.video.currentTime - (12 - 1 / 30)) < .001, "trim inspection follows the dragged edge");
  handle().props.onPointerUp({ pointerId: 1 }); await flush();
  const count = ui.changes.length;
  handle().props.onPointerMove({ pointerId: 1, clientX: 200 }); assert.equal(ui.changes.length, count);
});

test("trim handles clamp to legal duration, support keyboard, and stop when disabled", async () => {
  const ui = await harness();
  const handle = () => ui.nodes.find((node) => node.props?.["aria-label"] === "Clip A in trim");
  let prevented = false;
  handle().props.onKeyDown({ key: "ArrowRight", shiftKey: false, preventDefault() { prevented = true; } });
  assert.ok(prevented); assert.equal(ui.changes.at(-1).source_start, 10.033); assert.equal(ui.changes.at(-1).source_end, 13);
  await ui.update({ disabled: true });
  const count = ui.changes.length;
  handle().props.onKeyDown({ key: "ArrowRight", shiftKey: true, preventDefault() {} }); assert.equal(ui.changes.length, count);
  assert.equal(interaction.trimAtPointer({ source_start: 10, source_end: 13 }, "source_start", 100), 12.8);
  assert.equal(interaction.trimAtPointer({ source_start: 10, source_end: 13 }, "source_end", 100, 20), 20);
});

test("reframing a retained still uses its dimensions without loading the film and clears on source change", async () => {
  const ui = await harness({ aspect: "portrait" }, true);
  ui.nodes.find((node) => node.type === "img").props.onLoad({ currentTarget: { naturalWidth: 1920, naturalHeight: 1080 } }); await flush();
  ui.button("Reframe").props.onClick(); await flush();
  const surface = () => ui.nodes.find((node) => node.props?.className === "frameSurface");
  const currentTarget = { focus() {}, setPointerCapture() {}, getBoundingClientRect: () => ({ width: 100, height: 200 }) };
  surface().props.onPointerDown({ pointerId: 2, button: 0, clientX: 50, clientY: 100, currentTarget, preventDefault() {} });
  surface().props.onPointerMove({ pointerId: 2, clientX: 70, clientY: 90 });
  const next = ui.changes.at(-1);
  assert.ok(next.framing.anchor_x < .2, "moving an oversized image right lowers its crop anchor");
  assert.equal(next.framing.anchor_y, 1, "dragging up clamps the crop to the bottom");
  assert.equal(ui.nodes.find((node) => node.type === "video").props.src, undefined);
  await ui.update({ source: { ...next, film_id: "another" }, endpoint: undefined });
  assert.equal(surface(), undefined);
});

test("framing drag matches fit, fill, zoom and axes without available movement", () => {
  const frame = { fit: "fill", zoom: 1, anchor_x: .5, anchor_y: .5 };
  const filled = interaction.framingDrag(frame, { width: 200, height: 100 }, { width: 100, height: 100 }, 20, 20);
  assert.equal(filled.anchor_x, .3); assert.equal(filled.anchor_y, .5);
  const fitted = interaction.framingDrag({ ...frame, fit: "fit" }, { width: 200, height: 100 }, { width: 100, height: 100 }, 20, 10);
  assert.equal(fitted.anchor_x, .5); assert.equal(fitted.anchor_y, .7);
  const zoomed = interaction.framingDrag({ ...frame, zoom: 2 }, { width: 100, height: 100 }, { width: 100, height: 100 }, -10, 10);
  assert.equal(zoomed.anchor_x, .6); assert.equal(zoomed.anchor_y, .4);
  assert.equal(interaction.framingDrag(frame, { width: 0, height: 0 }, { width: 100, height: 100 }, 10, 10), frame);
});
