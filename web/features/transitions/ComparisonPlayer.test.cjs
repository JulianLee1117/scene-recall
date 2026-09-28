const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const helpers = {};
const transport = {};
const compile = (file, jsx = false) => ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, ...(jsx ? { jsx: ts.JsxEmit.ReactJSX } : {}) },
}).outputText;
vm.runInNewContext(compile("transitions.ts"), { exports: helpers });
vm.runInNewContext(compile("transport.ts"), { exports: transport, setTimeout, clearTimeout });
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const job = { id: "one", status: "completed", request: { recipe: { id: "whip-pan" } }, result: { preview_url: "/one.mp4", duration: 4, transition_start: 1.8, transition_end: 2.2, fps: 30 } };
const audio = { url: "blob:old", name: "Old song", duration: 20, seamTime: 5, volume: .7 };

class Media extends EventTarget {
  currentTime = 0; readyState = 4; seeking = false; paused = true; duration = 20; pauseCount = 0; pending = [];
  pause() { this.paused = true; this.pauseCount++; }
  play() { this.paused = false; return new Promise((resolve, reject) => this.pending.push({ resolve, reject })); }
}

async function harness(overrides = {}) {
  const hooks = [], effects = [], exported = {}, frames = new Map();
  const document = Object.assign(new EventTarget(), { hidden: false });
  let cursor = 0, scheduled = false, tree, frameId = 0, now = 1000;
  let props = { job, audio: null, onAudioChange: (value) => { props = { ...props, audio: value }; schedule(); }, ...overrides };
  const same = (a, b) => a && b && a.length === b.length && a.every((item, index) => Object.is(item, b[index]));
  const schedule = () => { if (!scheduled) { scheduled = true; queueMicrotask(render); } };
  const react = {
    useState(initial) { const index = cursor++; hooks[index] ??= { value: typeof initial === "function" ? initial() : initial }; return [hooks[index].value, (change) => { const next = typeof change === "function" ? change(hooks[index].value) : change; if (!Object.is(next, hooks[index].value)) { hooks[index].value = next; schedule(); } }]; },
    useRef(initial) { return hooks[cursor++] ??= { current: initial }; },
    useCallback(callback, deps) { const index = cursor++; if (!hooks[index] || !same(hooks[index].deps, deps)) hooks[index] = { value: callback, deps }; return hooks[index].value; },
    useEffect(effect, deps) { const index = cursor++; if (!hooks[index] || !same(hooks[index].deps, deps)) { const cleanup = hooks[index]?.cleanup; hooks[index] = { deps, cleanup }; effects.push(() => { hooks[index].cleanup?.(); hooks[index].cleanup = effect(); }); } },
  };
  vm.runInNewContext(compile("ComparisonPlayer.tsx", true), {
    exports: exported, Error, AbortController, performance: { now: () => now }, URL: { createObjectURL: () => "blob:new" },
    requestAnimationFrame: (callback) => { frames.set(++frameId, callback); return frameId; }, cancelAnimationFrame: (id) => frames.delete(id),
    document,
    require(name) {
      if (name === "react") return react;
      if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }), Fragment: "fragment" };
      if (name === "./transitions") return helpers;
      if (name === "./transport") return transport;
      if (name === "@/lib/lab") return { mediaUrl: (value) => value };
      return { default: new Proxy({}, { get: (_, key) => key }) };
    },
  });
  function render() { cursor = 0; scheduled = false; tree = exported.default(props); effects.splice(0).forEach((effect) => effect()); }
  render(); await flush();
  const media = new Map();
  for (const pane of nodes(tree).filter((node) => node.type?.name === "VideoPane")) {
    const video = new Media(); media.set(pane.props.job.id, video);
    pane.props.register(pane.props.job.id, video); pane.props.onReady(pane.props.job.id);
  }
  await flush(); const video = media.get(job.id);
  return {
    video, media, frames, document, get tree() { return tree; }, get props() { return props; }, get nodes() { return nodes(tree); },
    button(label) { return nodes(tree).find((node) => node.type === "button" && text(node).trim() === label); },
    async update(patch) { props = { ...props, ...patch }; schedule(); await flush(); },
    async advance(ms) { now += ms; const queued = [...frames.values()]; frames.clear(); queued.forEach((callback) => callback(now)); await flush(); },
  };
}

test("an interrupted earlier play promise cannot stop the next playback or display a false failure", async () => {
  const ui = await harness();
  ui.button("Play").props.onClick(); await flush();
  assert.equal(ui.video.pending.length, 1);
  ui.button("Pause").props.onClick(); await flush();
  ui.button("Play").props.onClick(); await flush();
  assert.equal(ui.video.pending.length, 2);
  ui.video.pending[0].reject(new Error("Interrupted by pause")); await flush();
  assert.ok(ui.button("Pause"));
  assert.equal(ui.nodes.some((node) => node.props?.role === "alert"), false);
  ui.video.pending[1].reject(new Error("Current playback failure")); await flush();
  assert.ok(ui.button("Play"));
  assert.match(text(ui.tree), /Playback could not start/);
});

test("a buffering pane holds both previews, music and shared phase until it can resume", async () => {
  const second = { ...job, id: "two", result: { ...job.result, preview_url: "/two.mp4" } };
  const ui = await harness({ compare: second, audio });
  const music = new Media(); ui.nodes.find((node) => node.type === "audio").props.ref(music);
  ui.button("Play").props.onClick(); await flush(); await ui.advance(200);
  const other = ui.media.get("two"); other.readyState = 2;
  ui.nodes.find((node) => node.type?.name === "VideoPane" && node.props.job.id === "two").props.onWaiting(); await flush();
  assert.equal(ui.video.paused, true); assert.equal(other.paused, true); assert.equal(music.paused, true);
  assert.match(text(ui.tree), /Buffering/);
  const phase = ui.nodes.find((node) => node.props?.["aria-label"] === "Shared seam-relative playhead").props.value;
  await ui.advance(3000);
  assert.equal(ui.nodes.find((node) => node.props?.["aria-label"] === "Shared seam-relative playhead").props.value, phase);
  other.readyState = 4; other.dispatchEvent(new Event("canplay")); await flush();
  assert.equal(ui.video.paused, false); assert.equal(other.paused, false); assert.equal(music.paused, false);
  await ui.advance(100);
  const resumed = ui.nodes.find((node) => node.props?.["aria-label"] === "Shared seam-relative playhead").props.value;
  assert.ok(Math.abs(resumed - phase - .1) < .001, "buffer time is not added to the playback clock");
  ui.button("Pause").props.onClick(); await flush();
});

test("pausing or changing context while buffering cancels the pending restart", async () => {
  for (const cancel of [async (ui) => { ui.button("Pause").props.onClick(); await flush(); }, (ui) => ui.update({ active: false }), (ui) => ui.update({ job: { ...job, id: "new" } })]) {
    const ui = await harness(); ui.button("Play").props.onClick(); await flush();
    ui.video.readyState = 2;
    ui.nodes.find((node) => node.type?.name === "VideoPane").props.onWaiting(); await flush();
    const plays = ui.video.pending.length;
    await cancel(ui);
    ui.video.readyState = 4; ui.video.dispatchEvent(new Event("canplay")); await flush();
    assert.equal(ui.video.pending.length, plays);
    assert.equal(ui.video.paused, true);
    assert.doesNotMatch(text(ui.tree), /Buffering|Playback wait cancelled/);
  }
});

test("a pending media seek also holds the clock even when an earlier frame remains decoded", async () => {
  const ui = await harness(); ui.button("Play").props.onClick(); await flush();
  ui.video.seeking = true;
  ui.nodes.find((node) => node.type?.name === "VideoPane").props.onWaiting(); await flush();
  assert.equal(ui.video.paused, true); assert.match(text(ui.tree), /Buffering/);
  ui.video.seeking = false; ui.video.dispatchEvent(new Event("seeked")); await flush();
  assert.equal(ui.video.paused, false); assert.doesNotMatch(text(ui.tree), /Buffering/);
  ui.button("Pause").props.onClick(); await flush();
});

test("cached panes re-registered during a seek recover readiness without re-aligning on seeked or canplay", async () => {
  const ui = await harness();
  const pane = () => ui.nodes.find((node) => node.type?.name === "VideoPane");
  ui.video.readyState = 1; ui.video.seeking = true;
  pane().props.register(job.id, null); pane().props.register(job.id, ui.video); await flush();
  assert.equal(ui.button("Play").props.disabled, true);
  ui.video.readyState = 4; ui.video.seeking = false; ui.video.currentTime = 1.234;
  const ready = pane().props.onReady;
  // Exercise the real VideoPane event wiring with the cached DOM element.
  const isolated = {};
  vm.runInNewContext(compile("ComparisonPlayer.tsx", true) + "\nexports.VideoPane = VideoPane;", { exports: isolated, require(name) {
    if (name === "react") return { useRef: () => ({ current: ui.video }), useState: (initial) => [initial, () => {}], useEffect: () => {} };
    if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }), Fragment: "fragment" };
    if (name === "./transitions") return helpers;
    if (name === "@/lib/lab") return { mediaUrl: (value) => value };
    return { default: new Proxy({}, { get: (_, key) => key }) };
  } });
  const video = nodes(isolated.VideoPane({ ...pane().props, onReady: ready })).find((node) => node.type === "video");
  video.props.onSeeked(); await flush();
  assert.equal(ui.button("Play").props.disabled, false);
  assert.equal(ui.video.currentTime, 1.234, "seek completion updates readiness, not the chosen position");
  video.props.onCanPlay(); await flush();
  assert.equal(ui.video.currentTime, 1.234, "canplay cannot cause a seek-event loop");
  assert.doesNotMatch(text(ui.tree), /Loading preview/);
});

test("the inactive local player cannot bypass its disabled transport with the Space shortcut", async () => {
  const ui = await harness({ active: false });
  ui.tree.props.onKeyDown({ code: "Space", target: { closest: () => null }, preventDefault() {} }); await flush();
  assert.equal(ui.video.pending.length, 0);
  await ui.update({ active: true });
  ui.button("Play").props.onClick(); await flush();
  assert.equal(ui.video.paused, false);
  await ui.update({ active: false });
  assert.equal(ui.video.paused, true);
});

test("music replacement pauses the old element and ignores its late metadata", async () => {
  const ui = await harness({ audio });
  const oldElement = new Media();
  const oldAudio = ui.nodes.find((node) => node.type === "audio");
  oldAudio.props.ref(oldElement);
  ui.button("Play").props.onClick(); await flush();
  assert.equal(oldElement.paused, false);
  ui.nodes.find((node) => node.type === "input" && node.props.type === "file").props.onChange({ target: { files: [{ name: "New song.wav" }], value: "old-input" } }); await flush();
  assert.equal(oldElement.paused, true);
  assert.equal(ui.props.audio.url, "blob:new");
  const newElement = new Media();
  const newAudio = ui.nodes.find((node) => node.type === "audio");
  newAudio.props.ref(newElement);
  oldAudio.props.onLoadedMetadata({ currentTarget: oldElement }); await flush();
  assert.equal(ui.props.audio.url, "blob:new", "stale metadata cannot restore an old object URL");
  newAudio.props.onLoadedMetadata({ currentTarget: newElement }); await flush();
  assert.equal(ui.props.audio.duration, 20);
  newElement.paused = false;
  newAudio.props.ref(null);
  assert.equal(newElement.paused, true, "detaching the audio element stops playback before losing its ref");
});

test("removing failed music clears its error and leaves video playback usable", async () => {
  const ui = await harness({ audio });
  const node = new Media(), element = ui.nodes.find((item) => item.type === "audio");
  element.props.ref(node);
  element.props.onError({ currentTarget: node }); await flush();
  assert.match(text(ui.tree), /audio format could not be played/);
  ui.button("Remove").props.onClick(); await flush();
  assert.equal(ui.props.audio, null);
  assert.equal(ui.nodes.some((item) => item.props?.role === "alert"), false);
  element.props.ref(null);
  ui.button("Play").props.onClick(); await flush();
  assert.equal(ui.video.paused, false);
});

test("the shared program timeline hides only the duplicate scrubber and reports output seconds", async () => {
  const times = [];
  const ui = await harness({ hideScrubber: true, onTimeChange: (time) => times.push(time) });
  assert.equal(ui.nodes.some((node) => node.props?.["aria-label"] === "Shared seam-relative playhead"), false);
  assert.ok(ui.button("Play")); assert.ok(ui.button("Loop seam"));
  await ui.update({ externalSeek: { jobId: "one", time: 3.5, token: 1 } });
  assert.ok(Math.abs(ui.video.currentTime - 3.5) < .0001);
  assert.equal(times.at(-1), 3.5, "the external timeline receives output seconds rather than seam-relative phase");
  assert.equal(ui.button("Loop seam").props["aria-pressed"], false, "seeking outside the seam expands playback to the full pair");
  ui.nodes.find((node) => node.props?.["aria-label"] === "Next output frame").props.onClick(); await flush();
  assert.ok(Math.abs(times.at(-1) - 3.5 - 1 / 30) < .00001);
});

test("an external program seek made while inactive is applied once without later rewinding", async () => {
  const times = [];
  const ui = await harness({ active: false, hideScrubber: true, onTimeChange: (time) => times.push(time) });
  const prior = ui.video.currentTime;
  await ui.update({ externalSeek: { jobId: "one", time: 2.4, token: 3 } });
  assert.equal(ui.video.currentTime, prior); assert.deepEqual(times, []);
  await ui.update({ active: true });
  assert.ok(Math.abs(ui.video.currentTime - 2.4) < .0001); assert.equal(times.at(-1), 2.4);
  ui.button("Play").props.onClick(); await flush(); await ui.advance(100);
  const progressed = times.at(-1);
  assert.ok(progressed > 2.4);
  await ui.update({ active: false });
  assert.equal(ui.video.paused, true); assert.equal(ui.frames.size, 0);
  await ui.update({ active: true });
  assert.ok(Math.abs(times.at(-1) - progressed) < .0001, "returning to the program preserves progress after a consumed external seek");
  await ui.update({ active: false, externalSeek: { jobId: "one", time: 1.5, token: 4 } });
  await ui.update({ active: true });
  assert.equal(times.at(-1), 1.5, "a new pending token is still applied after activation");
});

test("a cold program preview retains its external seek through metadata and ignores old-job seeks", async () => {
  const times = [];
  const ui = await harness({ hideScrubber: true, onTimeChange: (time) => times.push(time) });
  const pane = () => ui.nodes.find((node) => node.type?.name === "VideoPane");
  ui.video.readyState = 0;
  await ui.update({ externalSeek: { jobId: "one", time: .4, token: 10 } });
  const unloadedTime = ui.video.currentTime;
  assert.ok(Math.abs(times.at(-1) - .4) < .0001);
  ui.video.readyState = 4; pane().props.onReady("one", true); await flush();
  assert.notEqual(ui.video.currentTime, unloadedTime);
  assert.ok(Math.abs(ui.video.currentTime - .4) < .0001);
  const oldReady = pane().props.onReady;
  const next = { ...job, id: "next", result: { ...job.result, preview_url: "/next.mp4", transition_start: .8, transition_end: 1.2 } };
  await ui.update({ job: next, active: false, externalSeek: { jobId: "one", time: 3.5, token: 11 } });
  const newVideo = new Media(); pane().props.register("next", newVideo); pane().props.onReady("next", true); await flush();
  const correctTime = newVideo.currentTime;
  oldReady("one", true); await ui.update({ active: true });
  assert.equal(newVideo.currentTime, correctTime, "a pending intent for the previous result cannot seek a new job");
  await ui.update({ externalSeek: { jobId: "next", time: 2.8, token: 12 } });
  assert.ok(Math.abs(newVideo.currentTime - 2.8) < .0001);
});

test("an external program seek cancels a buffering restart and leaves the new frame paused", async () => {
  const times = [];
  const ui = await harness({ onTimeChange: (time) => times.push(time) });
  ui.button("Play").props.onClick(); await flush();
  ui.video.readyState = 2;
  ui.nodes.find((node) => node.type?.name === "VideoPane").props.onWaiting(); await flush();
  const plays = ui.video.pending.length;
  await ui.update({ externalSeek: { jobId: "one", time: 2.8, token: 20 } });
  ui.video.readyState = 4; ui.video.dispatchEvent(new Event("canplay")); await flush();
  assert.equal(ui.video.pending.length, plays); assert.equal(ui.video.paused, true);
  assert.equal(times.at(-1), 2.8); assert.equal(ui.frames.size, 0);
  assert.doesNotMatch(text(ui.tree), /Buffering|Playback wait cancelled/);
});

test("a program seek waiting for its result is not consumed by the old job", async () => {
  const pending = { jobId: "next", time: 2.6, token: 30 };
  const ui = await harness({ externalSeek: pending });
  const next = { ...job, id: "next", result: { ...job.result, preview_url: "/next.mp4" } };
  await ui.update({ job: next });
  const pane = ui.nodes.find((node) => node.type?.name === "VideoPane");
  const newVideo = new Media(); pane.props.register("next", newVideo); pane.props.onReady("next", true); await flush();
  assert.ok(Math.abs(newVideo.currentTime - 2.6) < .0001, "matching result activation applies the previously ineligible token");
  assert.equal(newVideo.pending.length, 0);
});

test("hiding the page pauses program media and music without a late-promise restart", async () => {
  const ui = await harness({ hideScrubber: true, audio });
  const music = new Media(); ui.nodes.find((node) => node.type === "audio").props.ref(music);
  ui.button("Play").props.onClick(); await flush();
  assert.equal(ui.video.paused, false); assert.equal(music.paused, false);
  ui.document.hidden = true; ui.document.dispatchEvent(new Event("visibilitychange")); await flush();
  assert.equal(ui.video.paused, true); assert.equal(music.paused, true); assert.equal(ui.frames.size, 0);
  ui.video.pending[0].reject(new Error("interrupted while hidden")); await flush();
  assert.equal(ui.nodes.some((node) => node.props?.role === "alert"), false);
  ui.document.hidden = false; ui.document.dispatchEvent(new Event("visibilitychange")); await flush();
  assert.equal(ui.video.paused, true); assert.equal(music.paused, true);
});
