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
vm.runInNewContext(compile("../lab/editorDirection.ts"), { exports: helpers });
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];

function harness(overrides = {}) {
  const state = [], listeners = {}, timers = new Map(), events = [], exported = {};
  let cursor = 0, tree, effects = [], serial = 0;
  const react = {
    useMemo: (compute) => compute(),
    useRef(initial) { return state[cursor++] ??= { current: initial }; },
    useState(initial) { const index = cursor++; state[index] ??= { value: typeof initial === "function" ? initial() : initial };
      return [state[index].value, (next) => { state[index].value = typeof next === "function" ? next(state[index].value) : next; }]; },
    useEffect(effect, dependencies) { const index = cursor++; const previous = state[index];
      if (!previous || dependencies.some((value, i) => !Object.is(value, previous.dependencies[i]))) {
        effects.push(() => { previous?.cleanup?.(); state[index].cleanup = effect(); });
      }
      state[index] = { dependencies, cleanup: previous?.cleanup };
    },
  };
  const player = { currentTime: 10, paused: true, pauses: 0, pause() { this.paused = true; this.pauses++; }, async play() { this.paused = false; } };
  const surface = { getBoundingClientRect: () => ({ left: 0, width: 1000 }),
    setPointerCapture(id) { this.capture = id; }, hasPointerCapture(id) { return this.capture === id; }, releasePointerCapture() { this.capture = null; } };
  const dom = { hidden: false, dialogOpen: false, querySelector() { return this.dialogOpen ? {} : null; } };
  const jsx = (type, props) => ({ type, props });
  vm.runInNewContext(compile("MusicDirectionRanges.tsx"), {
    exports: exported, crypto: { randomUUID: () => `range-${++serial}` },
    ResizeObserver: class { observe() {} disconnect() {} },
    window: { document: dom, addEventListener: (name, handler) => { listeners[name] = handler; }, removeEventListener: (name) => { delete listeners[name]; },
      setInterval: (handler) => { const id = ++serial; timers.set(id, handler); return id; }, clearInterval: (id) => timers.delete(id) },
    require(name) {
      if (name === "react") return react;
      if (name === "react/jsx-runtime") return { jsx, jsxs: jsx };
      if (name === "@/lib/lab") return { mediaUrl: (value) => value, seconds: (value) => value.toFixed(2) };
      if (name === "@/lib/playbackShortcut") return { isPlaybackSpace: (event) => event.key === " " && !event.textInput };
      if (name === "./useAudioWaveform") return { useAudioWaveform: () => ({ peaks: [], status: "" }) };
      if (name === "./audioWaveform") return { waveformPath: () => "" };
      if (name === "@/features/lab/editorDirection") return helpers;
      if (name.endsWith(".css")) return { default: new Proxy({}, { get: (_, key) => key }) };
      return { default: name };
    },
  });
  const props = { document: { track: { id: "song", duration: 90 }, passage: { start: 10, end: 40 }, editor_direction: { instruction: "", ranges: [] } },
    disabled: false, active: true, playhead: 10, onSeek(time) { props.playhead = time; events.push({ type: "seek", time }); },
    onChange(update, group) { props.document = update(props.document); events.push({ type: "change", group }); }, onEndChange: () => events.push({ type: "end" }), ...overrides };
  function render() {
    cursor = 0; tree = exported.default(props);
    for (const node of nodes(tree)) {
      if (node.type === "audio") node.props.ref.current = player;
      if (node.props?.className === "directionWave") node.props.ref.current = surface;
    }
  }
  render();
  return { props, player, surface, events, dom, listeners, timers, render,
    flush() { const pending = effects; effects = []; pending.forEach((effect) => effect()); },
    get wave() { return nodes(tree).find((node) => node.props?.className === "directionWave").props; },
    get nodes() { return nodes(tree); },
    pointer(x, id = 1) { return { button: 0, pointerId: id, clientX: x, currentTarget: surface, stopPropagation() {} }; },
  };
}

test("waveform click seeks while dragging creates a direction in source-track seconds", () => {
  const ui = harness(); ui.flush();
  ui.wave.onPointerDown(ui.pointer(100)); ui.wave.onPointerUp(ui.pointer(100));
  assert.equal(ui.props.playhead, 13);
  assert.equal(ui.props.document.editor_direction.ranges.length, 0);
  ui.wave.onPointerDown(ui.pointer(100)); ui.wave.onPointerMove(ui.pointer(400)); ui.wave.onPointerUp(ui.pointer(400));
  assert.deepEqual(JSON.parse(JSON.stringify(ui.props.document.editor_direction.ranges)), [{ id: "range-1", start: 13, end: 22, instruction: "" }]);
});

test("range creation includes the release position even without a final move event", () => {
  const ui = harness(); ui.flush();
  ui.wave.onPointerDown(ui.pointer(100)); ui.wave.onPointerMove(ui.pointer(200)); ui.wave.onPointerUp(ui.pointer(400));
  assert.equal(ui.props.document.editor_direction.ranges[0].start, 13);
  assert.equal(ui.props.document.editor_direction.ranges[0].end, 22);
});

test("dragging a range and trimming an edge stop at neighboring instructions", () => {
  const ui = harness({ document: { track: { id: "song", duration: 90 }, passage: { start: 10, end: 40 }, editor_direction: { instruction: "Global", ranges: [
    { id: "first", start: 13, end: 18, instruction: "A" }, { id: "next", start: 22, end: 30, instruction: "B" },
  ] } } }); ui.flush();
  ui.nodes.find((node) => node.props?.className === "rangeBody").props.onPointerDown(ui.pointer(150));
  ui.wave.onPointerMove(ui.pointer(700)); ui.wave.onPointerUp(ui.pointer(700)); ui.render();
  assert.equal(ui.props.document.editor_direction.ranges[0].start, 17);
  assert.equal(ui.props.document.editor_direction.ranges[0].end, 22);
  ui.nodes.find((node) => node.props?.["aria-label"] === "Adjust direction 1 start").props.onPointerDown(ui.pointer(230));
  ui.wave.onPointerMove(ui.pointer(-500)); ui.wave.onPointerUp(ui.pointer(-500));
  assert.equal(ui.props.document.editor_direction.ranges[0].start, 0);
  assert.equal(ui.props.document.editor_direction.ranges[1].start, 22);
  assert.ok(ui.events.some((event) => event.group === "direction-range:first"));
});

test("inactive tab pauses audition, ends gestures and ignores playback shortcuts", () => {
  const ui = harness(); ui.flush();
  ui.player.paused = false;
  ui.wave.onPointerDown(ui.pointer(100)); ui.wave.onPointerMove(ui.pointer(200));
  ui.props.active = false; ui.render(); ui.flush();
  assert.equal(ui.player.paused, true);
  assert.equal(ui.surface.capture, null);
  ui.wave.onPointerUp(ui.pointer(200));
  assert.equal(ui.props.document.editor_direction.ranges.length, 0);
  let prevented = false;
  ui.listeners.keydown({ key: " ", preventDefault() { prevented = true; } });
  assert.equal(prevented, false);
});

test("typing Space and open dialogs never start the song", () => {
  const ui = harness(); ui.flush();
  let prevented = false;
  ui.listeners.keydown({ key: " ", textInput: true, preventDefault() { prevented = true; } });
  assert.equal(prevented, false);
  ui.dom.dialogOpen = true;
  ui.listeners.keydown({ key: " ", preventDefault() { prevented = true; } });
  assert.equal(prevented, false);
  assert.equal(ui.player.paused, true);
});

test("cancelling creation does not add a range and locked settings allow audition without mutation", () => {
  const ui = harness(); ui.flush();
  ui.wave.onPointerDown(ui.pointer(100)); ui.wave.onPointerMove(ui.pointer(500)); ui.wave.onPointerCancel(ui.pointer(500));
  assert.equal(ui.props.document.editor_direction.ranges.length, 0);
  ui.props.disabled = true; ui.render(); ui.flush();
  ui.wave.onPointerDown(ui.pointer(200)); ui.wave.onPointerMove(ui.pointer(500)); ui.wave.onPointerUp(ui.pointer(500));
  assert.equal(ui.props.playhead, 16);
  assert.equal(ui.props.document.editor_direction.ranges.length, 0);
});

test("precision inputs are section-relative while saved ranges keep source-track seconds", () => {
  const ui = harness({ document: { track: { id: "song", duration: 90 }, passage: { start: 10, end: 40 }, editor_direction: { instruction: "Global", ranges: [
    { id: "first", start: 8, end: 18, instruction: "A" },
  ] } } }); ui.flush();
  ui.nodes.find((node) => node.props?.className === "rangeBody").props.onClick(); ui.render();
  const inputs = ui.nodes.filter((node) => node.type === "./DirectionTimeInput");
  assert.equal(inputs[0].props.value, -2);
  assert.equal(inputs[1].props.value, 8);
  inputs[0].props.onChange(1);
  assert.equal(ui.props.document.editor_direction.ranges[0].start, 11);
  assert.equal(ui.props.document.editor_direction.ranges[0].end, 18);
});

test("keyboard focus selects the range and modified arrows do not move it", () => {
  const ui = harness({ document: { track: { id: "song", duration: 90 }, passage: { start: 10, end: 40 }, editor_direction: { instruction: "", ranges: [
    { id: "range", start: 13, end: 18, instruction: "Hold" },
  ] } } }); ui.flush();
  const edge = ui.nodes.find((node) => node.props?.["aria-label"] === "Adjust direction 1 end");
  edge.props.onFocus(); ui.render();
  assert.ok(ui.nodes.some((node) => node.type === "textarea" && node.props.value === "Hold"));
  let prevented = false;
  edge.props.onKeyDown({ key: "ArrowLeft", ctrlKey: true, nativeEvent: {}, preventDefault() { prevented = true; } });
  assert.equal(prevented, false);
  assert.equal(ui.props.document.editor_direction.ranges[0].end, 18);
  edge.props.onKeyDown({ key: "ArrowLeft", nativeEvent: {}, preventDefault() { prevented = true; } });
  assert.equal(ui.props.document.editor_direction.ranges[0].end, 17.9);
  edge.props.onBlur();
  assert.equal(ui.events.at(-1).type, "end");
});

test("Escape cancels live trim changes and Undo releases the gesture without a second mutation", () => {
  const ui = harness({ document: { track: { id: "song", duration: 90 }, passage: { start: 10, end: 40 }, editor_direction: { instruction: "", ranges: [
    { id: "range", start: 13, end: 18, instruction: "Hold" },
  ] } } }); ui.flush();
  const body = () => ui.nodes.find((node) => node.props?.className === "rangeBody");
  body().props.onPointerDown(ui.pointer(150)); ui.wave.onPointerMove(ui.pointer(400));
  assert.notEqual(ui.props.document.editor_direction.ranges[0].start, 13);
  ui.listeners.keydown({ key: "Escape", preventDefault() {} });
  assert.equal(ui.props.document.editor_direction.ranges[0].start, 13);
  assert.equal(ui.surface.capture, null);
  ui.render(); body().props.onPointerDown(ui.pointer(150)); ui.wave.onPointerMove(ui.pointer(400));
  const changes = ui.events.filter((event) => event.type === "change").length;
  ui.listeners.keydown({ key: "z", ctrlKey: true, preventDefault() {} });
  assert.equal(ui.surface.capture, null);
  ui.wave.onPointerMove(ui.pointer(800));
  assert.equal(ui.events.filter((event) => event.type === "change").length, changes);
});

test("touch cancellation restores a trimmed range, and precision bounds account for neighbors", () => {
  const ui = harness({ document: { track: { id: "song", duration: 90 }, passage: { start: 10, end: 40 }, editor_direction: { instruction: "", ranges: [
    { id: "first", start: 10, end: 12, instruction: "A" }, { id: "range", start: 13, end: 18, instruction: "B" },
    { id: "next", start: 20, end: 30, instruction: "C" },
  ] } } }); ui.flush();
  const edge = ui.nodes.find((node) => node.props?.["aria-label"] === "Adjust direction 2 end");
  edge.props.onFocus(); edge.props.onPointerDown(ui.pointer(260));
  ui.wave.onPointerMove(ui.pointer(600)); ui.wave.onPointerCancel(ui.pointer(600)); ui.render();
  assert.equal(ui.props.document.editor_direction.ranges[1].end, 18);
  const inputs = ui.nodes.filter((node) => node.type === "./DirectionTimeInput");
  assert.equal(inputs[0].props.min, 2);
  assert.equal(inputs[1].props.max, 10);
});
