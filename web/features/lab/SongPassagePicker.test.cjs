const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const compile = (file) => ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const limits = {};
vm.runInNewContext(compile("../../lib/labLimits.ts"), { exports: limits });
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
function harness(duration, passage = { start: 0, end: 30 }) {
  const hooks = [], events = [], mediaEvents = [], exported = {};
  let cursor = 0, tree;
  const captures = new Set();
  const surface = {
    getBoundingClientRect: () => ({ left: 100, width: 1000, right: 1100, top: 80, bottom: 180, height: 100 }),
    setPointerCapture(id) { captures.add(id); },
    hasPointerCapture(id) { return captures.has(id); },
    releasePointerCapture(id) { captures.delete(id); },
    focus() {},
  };
  let currentTime = passage.start;
  const audioNode = () => nodes(tree).find((node) => node.type === "audio");
  const player = {
    get currentTime() { return currentTime; },
    set currentTime(value) { currentTime = value; mediaEvents.push({ kind: "seek", value }); },
    volume: 1, paused: true,
    pause() { mediaEvents.push({ kind: "pause" }); if (!this.paused) { this.paused = true; audioNode()?.props.onPause(); } },
    async play() { mediaEvents.push({ kind: "play" }); this.paused = false; audioNode()?.props.onPlay(); },
  };
  const document = { track: { id: "song", name: "Song", duration }, passage, clips: [], audio_fade_in_seconds: 1 };
  const props = { document, disabled: false, upload: async () => {}, onApply: (range, fade) => events.push({ kind: "apply", range: { ...range }, fade }), onClose: () => events.push({ kind: "close" }) };
  const react = {
    useMemo: (compute) => compute(), useEffect() {},
    useRef(initial) { return hooks[cursor++] ??= { current: initial }; },
    useState(initial) {
      const index = cursor++;
      hooks[index] ??= { value: initial };
      return [hooks[index].value, (value) => { hooks[index].value = typeof value === "function" ? value(hooks[index].value) : value; }];
    },
  };
  vm.runInNewContext(compile("SongPassagePicker.tsx"), { exports: exported, require(name) {
    if (name === "react") return react;
    if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }), Fragment: "fragment" };
    if (name === "@/lib/labLimits") return limits;
    if (name === "@/lib/lab") return { mediaUrl: (value) => value, seconds: (value) => `${Math.floor(value / 60)}:${(value % 60).toFixed(2).padStart(5, "0")}` };
    if (name === "./useAudioWaveform") return { useAudioWaveform: () => ({ peaks: [], status: "" }) };
    if (name.endsWith(".css")) return { default: new Proxy({}, { get: (_, key) => key }) };
    return {};
  } });
  const render = () => {
    cursor = 0; tree = exported.default(props);
    nodes(tree).forEach((node) => {
      if (node.props.ref) node.props.ref.current = node.type === "audio" ? player : surface;
    });
  };
  render();
  return { document, events, mediaEvents, render, player, captures,
    button: (label) => nodes(tree).find((node) => node.type === "button" && text(node).trim().startsWith(label)),
    find: (label) => nodes(tree).find((node) => node.props?.["aria-label"] === label),
    get range() { return [this.find("Section start seconds")?.props.value ?? this.find("Section start").props["aria-valuenow"], this.find("Section end seconds")?.props.value ?? this.find("Section end").props["aria-valuenow"]]; },
    get head() { return this.find("Song playhead")?.props["aria-valuenow"] ?? player.currentTime; },
    get view() {
      const start = this.find("Scroll waveform")?.props.value ?? 0;
      return { start, end: start + duration / (2 ** this.find("Waveform zoom").props.value) };
    },
    inDetails(label) { return nodes(tree).some((node) => node.type === "details" && nodes(node).includes(this.find(label))); },
    startPlayback(time) {
      currentTime = time; player.paused = false; audioNode().props.onPlay();
      audioNode().props.onTimeUpdate({ currentTarget: player }); render();
    },
    advancePlayback(time) { currentTime = time; audioNode().props.onTimeUpdate({ currentTarget: player }); render(); },
    pointer(seconds, extra = {}) {
      const span = duration / (2 ** this.find("Waveform zoom").props.value);
      const viewStart = this.find("Scroll waveform")?.props.value ?? 0;
      return { pointerId: 1, button: 0, isPrimary: true, currentTarget: surface,
        clientX: 100 + ((seconds - viewStart) / span) * 1000,
        preventDefault() {}, stopPropagation() {}, ...extra };
    },
  };
}

const key = (key, extra = {}) => ({ key, shiftKey: false, altKey: false, preventDefault() {}, ...extra });
const closeRange = (actual, expected, message) => actual.forEach((value, index) => assert.ok(Math.abs(value - expected[index]) < 0.00001, message ?? `${value} should be ${expected[index]}`));

test("a ten-minute saved section opens intact and longer sections are clamped to the supported maximum", () => {
  const full = harness(900, { start: 30, end: 630 });
  assert.deepEqual(full.range, [30, 630]);
  assert.equal(full.button("Use this section").props.disabled, false);
  assert.deepEqual(harness(900, { start: 30, end: 630.1 }).range, [30, 630]);
  assert.deepEqual(harness(560, { start: 0, end: 600 }).range, [0, 560]);
});

test("the 30-second starter stays short until full-song selection and Cancel never applies its draft", () => {
  const ui = harness(560);
  assert.deepEqual(ui.range, [0, 30]);
  ui.button("Use full song").props.onClick(); ui.render();
  assert.deepEqual(ui.range, [0, 560]);
  assert.deepEqual(ui.events, []);
  ui.button("Cancel").props.onClick();
  assert.deepEqual(ui.events, [{ kind: "close" }]);
  assert.deepEqual(ui.document.passage, { start: 0, end: 30 });
});

test("Use full song applies the whole source only through the existing confirmation action", () => {
  const ui = harness(600, { start: 100, end: 130 });
  ui.button("Use full song").props.onClick(); ui.render();
  ui.button("Use this section").props.onClick();
  assert.deepEqual(ui.events, [{ kind: "apply", range: { start: 0, end: 600 }, fade: 1 }]);
});

test("Use 10 minutes stays within a longer source and the selection can be repositioned", () => {
  const ui = harness(900, { start: 800, end: 830 });
  ui.button("Use 10 minutes").props.onClick(); ui.render();
  assert.deepEqual(ui.range, [300, 900]);
  ui.find("Move selected section").props.onKeyDown({ key: "Home", preventDefault() {} }); ui.render();
  assert.deepEqual(ui.range, [0, 600]);
  ui.find("Section end").props.onKeyDown({ key: "ArrowRight", shiftKey: true, preventDefault() {} }); ui.render();
  assert.deepEqual(ui.range, [0, 600], "the end handle cannot grow beyond ten minutes");
  ui.button("Fit song").props.onClick(); ui.render();
  assert.deepEqual(ui.range, [0, 600], "waveform framing never changes the selected section");
});

test("the preview scrub lane moves only the playhead and clamps it inside the crop", () => {
  const ui = harness(240, { start: 30, end: 60 });
  assert.equal(ui.head, 30, "the preview opens at the crop start");
  const lane = ui.find("Scrub section preview");
  assert.ok(lane, "scrubbing has its own target above the trim controls");
  lane.props.onPointerDown(ui.pointer(45)); ui.render();
  assert.equal(ui.head, 45); assert.equal(ui.player.currentTime, 45);
  closeRange(ui.range, [30, 60]);
  lane.props.onPointerMove(ui.pointer(100)); ui.render();
  assert.equal(ui.head, 60); closeRange(ui.range, [30, 60]);
  lane.props.onPointerMove(ui.pointer(5)); ui.render();
  assert.equal(ui.head, 30); closeRange(ui.range, [30, 60]);
  lane.props.onPointerUp(ui.pointer(5)); ui.render();
  assert.equal(ui.captures.size, 0);
  assert.deepEqual(ui.events, [], "preview gestures never apply a draft");
  assert.deepEqual(ui.document.passage, { start: 30, end: 60 });
});

test("clicking waveform background outside the crop previews its nearest edge without moving the selection", () => {
  const ui = harness(240, { start: 30, end: 60 });
  for (const [time, expected] of [[10, 30], [100, 60]]) {
    const waveform = ui.find("Song waveform");
    waveform.props.onPointerDown(ui.pointer(time));
    waveform.props.onPointerUp(ui.pointer(time)); ui.render();
    assert.equal(ui.head, expected);
    closeRange(ui.range, [30, 60]);
  }
  ui.button("Cancel").props.onClick();
  assert.deepEqual(ui.events, [{ kind: "close" }]);
});

test("the selection remains movable while a stationary selection click only scrubs", () => {
  const ui = harness(240, { start: 30, end: 60 });
  const selection = ui.find("Move selected section");
  selection.props.onPointerDown(ui.pointer(40));
  selection.props.onPointerMove(ui.pointer(50));
  selection.props.onPointerUp(ui.pointer(50)); ui.render();
  closeRange(ui.range, [40, 70]); assert.equal(ui.head, 40);
  const moved = ui.find("Move selected section");
  moved.props.onPointerDown(ui.pointer(55));
  moved.props.onPointerUp(ui.pointer(55)); ui.render();
  closeRange(ui.range, [40, 70]); closeRange([ui.head], [55]);
  assert.deepEqual(ui.events, []);
});

test("Alt arrows adjust trim edges by a hundredth while default and Shift retain useful larger steps", () => {
  const ui = harness(240, { start: 30, end: 60 });
  ui.find("Section start").props.onKeyDown(key("ArrowRight", { altKey: true })); ui.render();
  closeRange(ui.range, [30.01, 60]); assert.equal(ui.head, 30.01);
  ui.find("Section end").props.onKeyDown(key("ArrowLeft", { altKey: true })); ui.render();
  closeRange(ui.range, [30.01, 59.99]); assert.equal(ui.head, 30.01);
  ui.find("Section start").props.onKeyDown(key("ArrowRight")); ui.render();
  closeRange(ui.range, [30.11, 59.99]);
  ui.find("Section end").props.onKeyDown(key("ArrowLeft", { shiftKey: true })); ui.render();
  closeRange(ui.range, [30.11, 58.99]);
  assert.deepEqual(ui.events, []);
});

test("playhead keyboard controls and Restart section preview never change the trim", () => {
  const ui = harness(240, { start: 30, end: 60 });
  ui.find("Song playhead").props.onKeyDown(key("ArrowRight", { altKey: true })); ui.render();
  assert.equal(ui.head, 30.01); closeRange(ui.range, [30, 60]);
  ui.find("Song playhead").props.onKeyDown(key("End")); ui.render();
  assert.equal(ui.head, 60); closeRange(ui.range, [30, 60]);
  ui.find("Song playhead").props.onKeyDown(key("ArrowRight", { shiftKey: true })); ui.render();
  assert.equal(ui.head, 60); closeRange(ui.range, [30, 60]);
  ui.find("Restart section preview").props.onClick(); ui.render();
  assert.equal(ui.head, 30); assert.equal(ui.player.currentTime, 30);
  closeRange(ui.range, [30, 60]); assert.deepEqual(ui.events, []);
});

test("visible precision fields accept hundredths and apply only through Use this section", () => {
  const ui = harness(240, { start: 30, end: 60 });
  for (const label of ["Section start seconds", "Section end seconds"]) {
    assert.equal(ui.find(label).props.step, 0.01);
    assert.equal(ui.inDetails(label), false, "precise trimming must not be hidden behind More options");
  }
  ui.find("Section start seconds").props.onChange({ target: { value: "", valueAsNumber: NaN } }); ui.render();
  closeRange(ui.range, [30, 60]); assert.equal(ui.head, 30, "clearing a number field must not jump the crop to zero");
  ui.find("Section start seconds").props.onChange({ target: { value: "31.27", valueAsNumber: 31.27 } }); ui.render();
  ui.find("Section end seconds").props.onChange({ target: { value: "58.43", valueAsNumber: 58.43 } }); ui.render();
  closeRange(ui.range, [31.27, 58.43]); assert.equal(ui.head, 31.27);
  assert.deepEqual(ui.events, []);
  assert.deepEqual(ui.document.passage, { start: 30, end: 60 });
  ui.button("Use this section").props.onClick();
  assert.equal(ui.events.length, 1);
  assert.equal(ui.events[0].kind, "apply");
  closeRange([ui.events[0].range.start, ui.events[0].range.end], [31.27, 58.43]);
});

test("zoom, scrolling and fitting the section change only waveform framing", () => {
  const ui = harness(240, { start: 30, end: 60 });
  const zoom = ui.find("Waveform zoom");
  assert.equal(ui.inDetails("Waveform zoom"), false, "zoom belongs beside the waveform");
  zoom.props.onChange({ target: { value: String(zoom.props.max) } }); ui.render();
  closeRange([ui.view.end - ui.view.start], [1], "zoom supports one-second precision");
  closeRange(ui.range, [30, 60]);
  ui.find("Scroll waveform").props.onChange({ target: { value: "100" } }); ui.render();
  closeRange(ui.range, [30, 60]);
  ui.find("Fit selected section").props.onClick(); ui.render();
  closeRange(ui.range, [30, 60]);
  assert.ok(ui.find("Section start") && ui.find("Section end"), "fit makes both trim edges visible");
  ui.find("Fit song waveform").props.onClick(); ui.render();
  assert.equal(ui.find("Waveform zoom").props.value, 0);
  closeRange(ui.range, [30, 60]);
  assert.deepEqual(ui.events, []);
  ui.button("Cancel").props.onClick();
  assert.deepEqual(ui.events, [{ kind: "close" }]);
});

test("another pointer cannot replace or cancel an active trim gesture", () => {
  const ui = harness(240, { start: 30, end: 60 });
  const start = ui.find("Section start"), end = ui.find("Section end");
  start.props.onPointerDown(ui.pointer(30));
  end.props.onPointerDown(ui.pointer(60, { pointerId: 2 }));
  end.props.onPointerMove(ui.pointer(90, { pointerId: 2 }));
  end.props.onPointerCancel(ui.pointer(90, { pointerId: 2 })); ui.render();
  closeRange(ui.range, [30, 60]);
  assert.deepEqual([...ui.captures], [1]);
  start.props.onPointerMove(ui.pointer(35)); ui.render();
  closeRange(ui.range, [35, 60]); assert.equal(ui.head, 35);
  start.props.onPointerCancel(ui.pointer(35)); ui.render();
  closeRange(ui.range, [30, 60]); assert.equal(ui.captures.size, 0);
  assert.deepEqual(ui.events, []);
});

for (const [label, from, to, expected] of [
  ["Section start", 30, 40, [40, 90]],
  ["Section end", 90, 80, [30, 80]],
  ["Move selected section", 40, 70, [60, 120]],
]) {
  test(`${label} can move during playback without pausing, seeking or muting the preview`, () => {
    const ui = harness(240, { start: 30, end: 90 });
    ui.startPlayback(50);
    const handle = ui.find(label);
    handle.props.onPointerDown(ui.pointer(from));
    handle.props.onPointerMove(ui.pointer(to)); ui.render();
    closeRange(ui.range, expected);
    ui.advancePlayback(50.3);
    assert.equal(ui.player.paused, false);
    assert.equal(ui.player.currentTime, 50.3);
    assert.equal(ui.player.volume, 1, "moving IN beyond the live head must not mute it");
    assert.deepEqual(ui.mediaEvents, [], "a trim gesture changes boundaries without manipulating playback");
    handle.props.onPointerUp(ui.pointer(to)); ui.render();
    assert.equal(ui.player.paused, false);
    assert.equal(ui.player.currentTime, 50.3, "release must never restart from IN");
    assert.deepEqual(ui.events, []);
  });
}

test("crossing OUT during an active trim waits until release, then stops in place without restarting", () => {
  const ui = harness(240, { start: 30, end: 90 });
  ui.startPlayback(50);
  const end = ui.find("Section end");
  end.props.onPointerDown(ui.pointer(90));
  end.props.onPointerMove(ui.pointer(45)); ui.render();
  ui.advancePlayback(50.5);
  assert.equal(ui.player.paused, false, "audition continues while deciding where OUT belongs");
  assert.equal(ui.player.currentTime, 50.5);
  assert.deepEqual(ui.mediaEvents, []);
  end.props.onPointerUp(ui.pointer(45)); ui.render();
  assert.equal(ui.player.paused, true);
  assert.equal(ui.player.currentTime, 50.5);
  assert.equal(ui.head, 50.5);
  assert.equal(ui.mediaEvents.some(({ kind }) => kind === "play"), false);
  assert.equal(ui.mediaEvents.some(({ kind }) => kind === "seek"), false);
  closeRange(ui.range, [30, 45]);
});

test("paused trimming preserves the current audition point until a boundary excludes it", () => {
  const ui = harness(240, { start: 30, end: 90 });
  ui.advancePlayback(50);
  const change = (label, value) => { ui.find(label).props.onChange({ target: { value: String(value), valueAsNumber: value } }); ui.render(); };
  change("Section end seconds", 85);
  change("Section start seconds", 35);
  assert.equal(ui.player.currentTime, 50); assert.equal(ui.head, 50);
  assert.deepEqual(ui.mediaEvents.filter(({ kind }) => kind === "seek"), []);
  change("Section end seconds", 45);
  assert.equal(ui.player.currentTime, 45); assert.equal(ui.head, 45);
  closeRange(ui.range, [35, 45]);
  assert.equal(ui.player.paused, true);
});

test("pointer and keyboard scrubbing seek while preserving active playback and crop boundaries", () => {
  const ui = harness(240, { start: 30, end: 90 });
  ui.startPlayback(40);
  const lane = ui.find("Scrub section preview");
  lane.props.onPointerDown(ui.pointer(50));
  lane.props.onPointerMove(ui.pointer(55));
  lane.props.onPointerUp(ui.pointer(55)); ui.render();
  closeRange([ui.player.currentTime], [55]);
  assert.equal(ui.player.paused, false);
  ui.find("Song playhead").props.onKeyDown(key("ArrowRight", { shiftKey: true })); ui.render();
  closeRange([ui.player.currentTime], [56]);
  assert.equal(ui.player.paused, false);
  assert.equal(ui.mediaEvents.some(({ kind }) => kind === "pause"), false);
  closeRange(ui.range, [30, 90]); assert.deepEqual(ui.events, []);
});

test("logarithmic zoom makes a small slider change gentle and plus/minus change scale by two", () => {
  const ui = harness(240, { start: 30, end: 90 });
  const slider = ui.find("Waveform zoom");
  assert.equal(slider.props.min, 0); assert.equal(slider.props.step, 0.05);
  slider.props.onChange({ target: { value: "0.05" } }); ui.render();
  const firstSpan = ui.view.end - ui.view.start;
  assert.ok(firstSpan > 230 && firstSpan < 240, "one slider step must not collapse the entire song into a tiny viewport");
  closeRange([firstSpan], [240 / (2 ** 0.05)]);
  ui.find("Zoom in waveform").props.onClick(); ui.render();
  closeRange([ui.view.end - ui.view.start], [firstSpan / 2]);
  ui.find("Zoom out waveform").props.onClick(); ui.render();
  closeRange([ui.view.end - ui.view.start], [firstSpan]);
  closeRange(ui.range, [30, 90]); assert.deepEqual(ui.events, []);
});

test("zoom follows the actual live audio position instead of a stale trim-edge focus", () => {
  const ui = harness(240, { start: 30, end: 90 });
  ui.startPlayback(50);
  ui.find("Waveform zoom").props.onChange({ target: { value: "3" } }); ui.render();
  ui.find("Section end seconds").props.onFocus?.();
  ui.player.currentTime = 72; // Audio can advance between React renders.
  const fraction = (72 - ui.view.start) / (ui.view.end - ui.view.start);
  ui.find("Zoom in waveform").props.onClick(); ui.render();
  assert.ok(ui.view.start <= 72 && ui.view.end >= 72);
  closeRange([(72 - ui.view.start) / (ui.view.end - ui.view.start)], [fraction], "zoom preserves the live head's horizontal position");
  closeRange([ui.view.end - ui.view.start], [15]);
  assert.equal(ui.player.paused, false);
  closeRange(ui.range, [30, 90]);
  ui.player.currentTime = 85;
  assert.ok(85 > ui.view.end, "playback can pass the viewport before the next UI update");
  ui.find("Zoom in waveform").props.onClick(); ui.render();
  assert.ok(ui.view.start <= 85 && ui.view.end >= 85, "zoom reveals an offscreen running playhead");
  closeRange([ui.view.end - ui.view.start], [7.5]);
  assert.equal(ui.player.paused, false);
});

test("zooming a paused, panned waveform keeps the visible area rather than jumping to an old head", () => {
  const ui = harness(240, { start: 30, end: 90 });
  ui.find("Waveform zoom").props.onChange({ target: { value: "3" } }); ui.render();
  ui.find("Scroll waveform").props.onChange({ target: { value: "100" } }); ui.render();
  ui.find("Section start seconds").props.onFocus?.();
  ui.find("Zoom in waveform").props.onClick(); ui.render();
  closeRange([(ui.view.start + ui.view.end) / 2], [115]);
  closeRange([ui.view.end - ui.view.start], [15]);
  assert.equal(ui.player.currentTime, 30);
  closeRange(ui.range, [30, 90]);
});

test("Play and Restart reveal the section start when the waveform was panned elsewhere", () => {
  for (const label of ["Play section", "Restart section preview"]) {
    const ui = harness(240, { start: 30, end: 90 });
    ui.find("Waveform zoom").props.onChange({ target: { value: "4" } }); ui.render();
    ui.find("Scroll waveform").props.onChange({ target: { value: "150" } }); ui.render();
    assert.equal(ui.find("Song playhead"), undefined);
    ui.find(label).props.onClick(); ui.render();
    assert.ok(ui.view.start <= 30 && ui.view.end >= 30, `${label} must put the current preview on screen`);
    assert.ok(ui.find("Song playhead"));
    closeRange([ui.view.end - ui.view.start], [15], "revealing playback preserves the chosen zoom");
    closeRange(ui.range, [30, 90]);
  }
});

test("playback follows only after leaving the visible window and holds that window steady during trimming", () => {
  const ui = harness(240, { start: 30, end: 90 });
  ui.startPlayback(50);
  ui.find("Waveform zoom").props.onChange({ target: { value: "4" } }); ui.render();
  const opening = ui.view;
  ui.advancePlayback(52);
  closeRange([ui.view.start, ui.view.end], [opening.start, opening.end], "the view must not drift with every playback tick");
  ui.advancePlayback(opening.end + 1);
  assert.ok(ui.find("Song playhead"), "playback outside the viewport becomes visible again");
  closeRange([ui.view.end - ui.view.start], [15]);
  const held = ui.view, selection = ui.find("Move selected section");
  selection.props.onPointerDown(ui.pointer(ui.player.currentTime));
  ui.advancePlayback(held.end + 1);
  closeRange([ui.view.start, ui.view.end], [held.start, held.end], "trim geometry must stay stable under the pointer");
  selection.props.onPointerUp(ui.pointer(ui.player.currentTime)); ui.render();
  closeRange(ui.range, [30, 90]);
});

test("lost capture rolls back a paused trim including its fade and original audition point", () => {
  const ui = harness(240, { start: 30, end: 90 });
  ui.find("Section fade in seconds").props.onChange({ target: { value: "8" } }); ui.render();
  ui.advancePlayback(50);
  const end = ui.find("Section end");
  end.props.onPointerDown(ui.pointer(90));
  end.props.onPointerMove(ui.pointer(31)); ui.render();
  closeRange(ui.range, [30, 31]);
  assert.equal(ui.find("Section fade in seconds").props.value, 1);
  assert.equal(ui.player.currentTime, 31);
  ui.find("Song waveform").props.onLostPointerCapture(ui.pointer(31)); ui.render();
  closeRange(ui.range, [30, 90]);
  assert.equal(ui.find("Section fade in seconds").props.value, 8);
  assert.equal(ui.player.currentTime, 50);
  assert.equal(ui.player.paused, true);
  assert.equal(ui.captures.size, 0);
  assert.deepEqual(ui.events, []);
});
