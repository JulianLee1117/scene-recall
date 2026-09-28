const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const compiled = ts.transpileModule(fs.readFileSync(path.join(__dirname, "MusicEditTimeline.tsx"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
function loadHelper(file) {
  const exported = {};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, { exports: exported });
  return exported;
}

function harness(overrides = {}) {
  const state = [], events = [], observers = [];
  let cursor = 0, tree, pendingEffects = [];
  const react = {
    useId: () => "timeline-hint",
    useEffect(effect, dependencies) {
      const index = cursor++;
      const previous = state[index];
      if (!previous || !dependencies || dependencies.some((value, i) => !Object.is(value, previous.dependencies[i]))) {
        pendingEffects.push(effect);
      }
      state[index] = { dependencies };
    },
    useMemo: (compute) => compute(),
    useRef(initial) { return state[cursor++] ??= { current: initial }; },
    useState(initial) {
      const index = cursor++;
      state[index] ??= { value: initial };
      return [state[index].value, (next) => { state[index].value = typeof next === "function" ? next(state[index].value) : next; }];
    },
  };
  const jsx = (type, props) => ({ type, props });
  const exported = {};
  vm.runInNewContext(compiled, {
    exports: exported,
    ResizeObserver: class {
      constructor(callback) { this.callback = callback; }
      observe() { observers.push(this.callback); }
      disconnect() {}
    },
    require(name) {
      if (name === "react") return react;
      if (name === "react/jsx-runtime") return { jsx, jsxs: jsx };
      if (name === "@/lib/lab") return { mediaUrl: (value) => value, seconds: (value) => value.toFixed(2) };
      if (name === "./musicEdit") return { directionOf: () => ({ query: "A hopeful wide image", search_facet: "look" }) };
      if (name === "./musicCues") return { musicCues: () => [] };
      if (name === "./audioWaveform") return loadHelper("audioWaveform.ts");
      if (name === "./timelineViewport") return loadHelper("timelineViewport.ts");
      if (name === "./dialogueAudio") return loadHelper("dialogueAudio.ts");
      if (name.endsWith(".css")) return { default: new Proxy({}, { get: (_, key) => key }) };
      return { default: name.slice(2) };
    },
  });
  const props = {
    document: { passage: { start: 0, end: 30 }, track: { duration: 30 }, rhythm: {}, fps: 24,
      clips: [{ id: "placed", film_id: "film", source_start: 0, source_end: 10 }] },
    plan: { slots: [{ id: "first", start: 0, end: 10, clip_id: "placed" }, { id: "next", start: 10, end: 30 }] },
    filmTitles: { film: "Film" }, peaks: [], waveformStatus: "", playhead: 4, selectedId: "first", disabled: false,
    snap: false, canAddCut: true, canReplan: true, onSnap() {}, onSelect() {}, onSeek() {}, onCut() {}, onSplitAt() {},
    onDetectBeats: () => events.push("beats"), canSetEnd: true, onSetEnd: () => events.push("end"), canJoinNext: true, onJoinNext: () => events.push("join"),
    onAddCut: () => events.push("split"), onRemoveCut: () => events.push("remove"), onReplan: () => events.push("replan"),
    ...overrides,
  };
  function render() { cursor = 0; tree = exported.default(props); }
  render();
  return { events, render, props,
    resize(width) { observers.forEach((callback) => callback([{ contentRect: { width } }])); },
    flushEffects() { const queued = pendingEffects; pendingEffects = []; queued.forEach((effect) => effect()); },
    get tree() { return tree; }, get nodes() { return nodes(tree); } };
}

const keyEvent = (key, extra = {}) => ({ key, nativeEvent: {}, preventDefault() {}, target: { closest: () => null }, ...extra });

test("dialogue selection seeks its own song position and Delete removes voice without deleting the selected B-roll", () => {
  const selected = [], sought = [], removed = [];
  const ui = harness({ document: { passage: { start: 10, end: 40 }, clips: [], rhythm: {}, fps: 24,
    dialogue_clips: [{ id: "voice", film_id: "film", start: 15, source_start: 100, source_end: 103, text: "A line", gain_db: 6 }] },
    onSelectDialogue: (id) => selected.push(id), onRemoveDialogue: (id) => removed.push(id), onSeek: (time) => sought.push(time) });
  const voice = ui.nodes.find((node) => node.type === "button" && node.props["aria-label"]?.startsWith("Dialogue from"));
  voice.props.onClick(); assert.deepEqual(selected, ["voice"]); assert.deepEqual(sought, [15]);
  let stopped = false;
  voice.props.onKeyDown({ key: "Delete", repeat: false, preventDefault() {}, stopPropagation() { stopped = true; } });
  assert.equal(stopped, true); assert.deepEqual(removed, ["voice"]);
  assert.deepEqual(ui.events, []);
});

test("dragging a voice keeps its width visible, permits overlap, and commits once while cancellation commits nothing", () => {
  const moved = [], seek = [];
  const ui = harness({ document: { passage: { start: 10, end: 40 }, clips: [], rhythm: {}, fps: 24,
    dialogue_clips: [
      { id: "voice", film_id: "film", start: 15, source_start: 100, source_end: 103, text: "A line", gain_db: 6 },
      { id: "neighbor", film_id: "film", start: 21, source_start: 100, source_end: 104, text: "Another", gain_db: 0 },
    ] }, onSelectDialogue() {}, onMoveDialogue: (id, start) => moved.push([id, start]), onSeek: (time) => seek.push(time) });
  const canvas = () => ui.nodes.find((node) => node.props?.className === "timelineCanvas");
  const block = () => ui.nodes.find((node) => node.type === "button" && node.props["aria-label"]?.startsWith("Dialogue from"));
  canvas().props.ref.current = { getBoundingClientRect: () => ({ left: 0, width: 900 }) }; ui.flushEffects();
  let captured = false;
  const target = { focus() {}, setPointerCapture() { captured = true; }, hasPointerCapture: () => captured, releasePointerCapture() { captured = false; } };
  const begin = () => block().props.onPointerDown({ button: 0, pointerId: 1, clientX: 150, currentTarget: target, preventDefault() {}, stopPropagation() {} });
  const originalWidth = block().props.style.width;
  begin(); canvas().props.onPointerMove({ pointerId: 1, clientX: 300 }); ui.render();
  assert.equal(block().props["data-moving"], true); assert.equal(block().props.style.width, originalWidth);
  assert.ok(Math.abs(parseFloat(block().props.style.left) - 100 * 10 / 30) < .001, "voices can overlap without a collision jump");
  for (const x of [330, 390, 450, 480]) canvas().props.onPointerMove({ pointerId: 1, clientX: x }); ui.render();
  assert.equal(moved.length, 0, "intermediate pointer events cannot consume Undo");
  canvas().props.onPointerUp({ pointerId: 1 }); ui.render();
  assert.deepEqual(moved, [["voice", 26]], "drag can move beyond another voice into a valid gap");
  assert.equal(captured, false); assert.equal(block().props["data-moving"], false);
  const afterDrag = seek.length; block().props.onClick(); assert.equal(seek.length, afterDrag, "release click must not seek the old position");
  begin(); canvas().props.onPointerMove({ pointerId: 1, clientX: -400 }); ui.render();
  assert.equal(block().props.style.left, "0%", "cannot leave the song passage");
  canvas().props.onPointerCancel({ pointerId: 1 }); ui.render();
  assert.equal(moved.length, 1); assert.equal(captured, false);
  assert.equal(block().props.style.width, originalWidth);
});

test("Delete removes only an explicitly selected placed scene and prioritizes a selected cut", () => {
  for (const key of ["Delete", "Backspace"]) {
    const removed = [];
    const ui = harness({ onRemoveScene: (id) => removed.push(id) });
    ui.tree.props.onKeyDown(keyEvent(key));
    assert.deepEqual(removed, ["first"]);
    ui.nodes.find((node) => node.props?.["data-cut-marker"]).props.onFocus(); ui.render();
    ui.tree.props.onKeyDown(keyEvent(key));
    assert.deepEqual(removed, ["first"], "cut deletion never also removes the scene");
    assert.deepEqual(ui.events, ["remove"]);
  }
});

test("scene removal ignores gaps, no selection, locks, repeats, text entry and hidden or busy editing", () => {
  for (const options of [{ selectedId: null }, { selectedId: "next" }, { disabled: true },
    { document: { passage: { start: 0, end: 30 }, track: { duration: 30 }, rhythm: {}, fps: 24,
      clips: [{ id: "placed", film_id: "film", source_start: 0, source_end: 10, locked: true }] } }]) {
    const removed = [];
    const ui = harness({ ...options, onRemoveScene: (id) => removed.push(id) });
    ui.tree.props.onKeyDown(keyEvent("Delete"));
    assert.deepEqual(removed, []);
  }
  const removed = [];
  const ui = harness({ onRemoveScene: (id) => removed.push(id) });
  for (const extra of [{ repeat: true }, { ctrlKey: true }, { altKey: true }, { metaKey: true },
    { defaultPrevented: true }, { nativeEvent: { isComposing: true } }])
    ui.tree.props.onKeyDown(keyEvent("Delete", extra));
  for (const selector of ["input", "textarea", "select", "[popover]", "dialog", "[role='dialog']", "[role='textbox']"])
    ui.tree.props.onKeyDown(keyEvent("Backspace", { target: { closest: (query) => query.includes(selector) } }));
  assert.deepEqual(removed, []);
});

test("Fill gaps is a compact optional timeline action that obeys operation locks", () => {
  const events = [];
  const ui = harness({ onFillGaps: () => events.push("fill"), canFillGaps: true });
  const button = () => ui.nodes.find((node) => node.type === "button" && text(node) === " Fill gaps");
  assert.equal(button().props.disabled, false);
  button().props.onClick();
  assert.deepEqual(events, ["fill"]);
  ui.props.disabled = true; ui.render();
  assert.equal(button().props.disabled, true);
  ui.props.disabled = false; ui.props.canFillGaps = false; ui.render();
  assert.equal(button().props.disabled, true);
  assert.equal(harness().nodes.some((node) => node.type === "button" && text(node) === " Fill gaps"), false);
});

test("cut tools close before changing timing and preserve disabled controls", () => {
  const ui = harness();
  const menu = ui.nodes.find((node) => node.props?.triggerLabel === "Cut tools");
  const content = menu.props.children(() => ui.events.push("close"));
  nodes(content).find((node) => node.type === "button" && text(node).includes("Suggest cuts")).props.onClick();
  assert.deepEqual(ui.events, ["close", "replan"]);
  const disabled = harness({ canReplan: false, canSetEnd: false, canJoinNext: false });
  const disabledMenu = disabled.nodes.find((node) => node.props?.triggerLabel === "Cut tools");
  assert.ok(nodes(disabledMenu.props.children(() => {})).filter((node) => node.type === "button").every((node) => node.props.disabled));
});

test("beat detection stays in guides and does not invoke cut regeneration", () => {
  const ui = harness();
  const menu = ui.nodes.find((node) => node.props?.triggerLabel === "Beat guides");
  nodes(menu.props.children(() => ui.events.push("close"))).find((node) => node.type === "button").props.onClick();
  assert.deepEqual(ui.events, ["close", "beats"]);
});

test("timeline edit shortcuts do not run behind a focused popover", () => {
  const ui = harness();
  ui.nodes.find((node) => node.props?.["data-cut-marker"]).props.onFocus();
  ui.render();
  const event = (key, inside) => ({ key, nativeEvent: {}, preventDefault() {}, target: { closest: (selector) => inside && selector.includes("[popover]") } });
  ui.tree.props.onKeyDown(event("m", true));
  ui.tree.props.onKeyDown(event("Delete", true));
  assert.deepEqual(ui.events, []);
  ui.tree.props.onKeyDown(event("m", false));
  assert.deepEqual(ui.events, ["split"]);
});

test("closing an earlier cue cannot dismiss a newly opened cue", () => {
  const ui = harness();
  const guides = ui.nodes.find((node) => node.props?.triggerLabel === "Beat guides");
  nodes(guides.props.children(() => {})).filter((node) => node.type === "input")[1].props.onChange({ target: { checked: true } });
  ui.render();
  const cuePanel = () => ui.nodes.find((node) => node.type === "MusicalCues");
  cuePanel().props.onSelect({ id: "older", start: 1 }); ui.render();
  cuePanel().props.onSelect({ id: "newer", start: 2 }); ui.render();
  cuePanel().props.onClose("older"); ui.render();
  assert.equal(cuePanel().props.selectedId, "newer");
  cuePanel().props.onClose("newer"); ui.render();
  assert.equal(cuePanel().props.selectedId, null);
});

test("placed clips label generated text as search intent and Expand remains separate", () => {
  const ui = harness();
  const placed = ui.nodes.find((node) => node.props?.["data-lab-scene-slot"] === "first");
  assert.match(placed.props["aria-label"], /Search intent:/);
  assert.match(text(placed), /Search intent/);
  ui.nodes.find((node) => node.props?.["aria-label"] === "Expand timeline").props.onClick(); ui.render();
  assert.ok(ui.nodes.some((node) => node.props?.["aria-label"] === "Collapse timeline"));
});

test("dense fit view keeps short clip selection separate from the cut marker strip", () => {
  const boundaries = [0, 3, 6, 10, 13, 15, 19, 22, 25, 28, 31, 35, 38, 41, 45, 48, 50, 53, 56, 59, 62, 65, 68, 71, 74, 77, 80, 83, 86, 88, 90];
  const calls = [];
  const ui = harness({
    document: { passage: { start: 0, end: 90 }, track: { duration: 90 }, rhythm: {}, fps: 24, clips: [] },
    plan: { slots: boundaries.slice(0, -1).map((start, index) => ({ id: `clip-${index + 1}`, start, end: boundaries[index + 1] })) },
    selectedId: "clip-1", onSelect: (id) => calls.push(["select", id]), onSeek: (time) => calls.push(["seek", time]),
    onCut: () => calls.push(["cut"]),
  });
  const strip = ui.nodes.find((node) => node.props?.["data-timeline-cut-strip"]);
  assert.equal(nodes(strip).filter((node) => node.props?.["data-cut-marker"]).length, 29);
  assert.equal(nodes(strip).filter((node) => node.props?.["data-lab-scene-slot"]).length, 0);
  ui.nodes.find((node) => node.props?.["aria-label"] === "Cut 16").props.onFocus(); ui.render();
  const target = ui.nodes.find((node) => node.props?.["data-lab-scene-slot"] === "clip-16");
  target.props.onClick(); ui.render();
  assert.deepEqual(calls, [["select", "clip-16"], ["seek", 48]]);
  assert.equal(ui.nodes.some((node) => node.props?.["aria-label"] === "Selected cut"), false);
});

test("cut strip markers retain captured drag and frame nudging", () => {
  const calls = [];
  const ui = harness({ onSeek: (time) => calls.push(["seek", time]), onCut: (index, time) => calls.push(["cut", index, time]) });
  const canvas = ui.nodes.find((node) => node.props?.className === "timelineCanvas");
  canvas.props.ref.current = { getBoundingClientRect: () => ({ left: 0, width: 900 }) };
  const marker = ui.nodes.find((node) => node.props?.["data-cut-marker"]);
  let captured = false;
  const target = { focus() {}, setPointerCapture() { captured = true; }, hasPointerCapture() { return captured; }, releasePointerCapture() { captured = false; } };
  marker.props.onPointerDown({ button: 0, pointerId: 1, clientX: 300, currentTarget: target, preventDefault() {}, stopPropagation() {} });
  assert.equal(captured, true);
  canvas.props.onPointerMove({ pointerId: 1, clientX: 330, altKey: false });
  canvas.props.onPointerUp({ pointerId: 1 });
  assert.equal(captured, false);
  assert.deepEqual(calls, [["seek", 10], ["cut", 1, 11]]);
  marker.props.onKeyDown({ key: "ArrowRight", preventDefault() {} });
  assert.equal(calls.at(-1)[2], 10 + 1 / 24);
});

test("dense cuts hide overlapping numbers until focused or sufficiently zoomed, retaining accessible handles", () => {
  const ui = harness({
    document: { passage: { start: 0, end: 148 }, track: { duration: 148 }, rhythm: {}, fps: 24, clips: [] },
    plan: { slots: Array.from({ length: 148 }, (_, index) => ({ id: `clip-${index}`, start: index, end: index + 1 })) },
  });
  const markers = () => ui.nodes.filter((node) => node.props?.["data-cut-marker"]);
  assert.equal(markers().length, 147);
  assert.ok(markers().every((node) => node.props.className.includes("crowdedCut")));
  const cut = markers().find((node) => node.props["aria-label"] === "Cut 101");
  assert.equal(cut.props.role, "slider"); assert.equal(cut.props["aria-valuenow"], 101);
  assert.equal(cut.props.disabled, false);
  cut.props.onFocus(); ui.render();
  const selected = markers().find((node) => node.props["aria-label"] === "Cut 101");
  assert.ok(selected.props.className.includes("selectedCut"));
  assert.equal(selected.props.className.includes("crowdedCut"), false);
  assert.ok(markers().find((node) => node.props["aria-label"] === "Cut 100").props.className.includes("crowdedCut"));
  for (let i = 0; i < 3; i++) {
    const zoom = ui.nodes.find((node) => node.props?.["aria-label"] === "Timeline zoom");
    nodes(zoom).filter((node) => node.type === "button")[1].props.onClick(); ui.render();
  }
  assert.ok(markers().every((node) => !node.props.className.includes("crowdedCut")), "4× zoom provides over 20px between these cuts");
});

test("cut label density uses neighboring pixel distances, not the total shot count", () => {
  const ui = harness({ plan: { slots: [
    { id: "a", start: 0, end: 1 }, { id: "b", start: 1, end: 1.5 },
    { id: "c", start: 1.5, end: 20 }, { id: "d", start: 20, end: 30 },
  ] } });
  const markers = ui.nodes.filter((node) => node.props?.["data-cut-marker"]);
  assert.deepEqual(markers.map((node) => node.props.className.includes("crowdedCut")), [true, true, false]);
});

test("missing scene navigation selects and seeks an empty position without editing cuts", () => {
  for (const selectedId of ["first", "next"]) {
    const calls = [];
    const ui = harness({ selectedId, onSelect: (id) => calls.push(["select", id]),
      onSeek: (time) => calls.push(["seek", time]), onCut: () => calls.push(["cut"]) });
    ui.nodes.find((node) => node.props?.["aria-label"] === "Review 1 missing scene").props.onClick();
    assert.deepEqual(calls, [["select", "next"], ["seek", 10]]);
  }
  const complete = harness({ plan: { slots: [{ id: "first", start: 0, end: 30, clip_id: "placed" }] } });
  assert.equal(complete.nodes.some((node) => node.props?.["aria-label"]?.startsWith("Review ")), false);
});

test("zoomed playback follows only outside view and stays put while paused or dragging", () => {
  const ui = harness({ playing: false });
  const scrollNode = { clientWidth: 900, scrollLeft: 0 };
  ui.nodes.find((node) => node.props?.className === "timelineScroll").props.ref.current = scrollNode;
  ui.flushEffects();
  // Match the actual toolbar label instead of assuming icon text.
  const zoomControl = ui.nodes.find((node) => node.props?.["aria-label"] === "Timeline zoom");
  nodes(zoomControl).filter((node) => node.type === "button")[1].props.onClick();
  ui.render(); ui.flushEffects();
  scrollNode.scrollLeft = 0;
  ui.props.playhead = 20;
  ui.render(); ui.flushEffects();
  assert.equal(scrollNode.scrollLeft, 0, "paused seeks leave horizontal position alone");
  ui.props.playing = true;
  ui.render(); ui.flushEffects();
  assert.equal(scrollNode.scrollLeft, 900, "playback outside the viewport pages forward and clamps");
  ui.props.playhead = 22;
  ui.render(); ui.flushEffects();
  assert.equal(scrollNode.scrollLeft, 900, "visible playback never scrolls continuously");
  const canvas = ui.nodes.find((node) => node.props?.className === "timelineCanvas");
  canvas.props.ref.current = { getBoundingClientRect: () => ({ left: 0, width: 1800 }) };
  const target = { focus() {}, setPointerCapture() {}, hasPointerCapture: () => true, releasePointerCapture() {} };
  ui.nodes.find((node) => node.props?.className === "musicLane").props.onPointerDown({
    button: 0, pointerId: 1, clientX: 120, currentTarget: target, preventDefault() {}, stopPropagation() {},
  });
  ui.props.playhead = 2;
  ui.render(); ui.flushEffects();
  assert.equal(scrollNode.scrollLeft, 900, "manual scrub owns the viewport even while audio is playing");
  canvas.props.onPointerUp({ pointerId: 1 });
  ui.props.playhead = 2.1;
  ui.render(); ui.flushEffects();
  assert.equal(scrollNode.scrollLeft, 0, "follow resumes after the gesture ends");
});

test("a hidden mounted timeline keeps zoom, waveform width and horizontal position", () => {
  const ui = harness();
  const scrollNode = { clientWidth: 900, scrollLeft: 0 };
  const scroller = () => ui.nodes.find((node) => node.props?.className === "timelineScroll");
  scroller().props.ref.current = scrollNode;
  ui.flushEffects(); ui.resize(900);
  const zoom = ui.nodes.find((node) => node.props?.["aria-label"] === "Timeline zoom");
  nodes(zoom).filter((node) => node.type === "button")[1].props.onClick();
  ui.render(); ui.flushEffects();
  scrollNode.scrollLeft = 650;
  scroller().props.onScroll({ currentTarget: scrollNode });
  ui.render();
  assert.equal(ui.nodes.find((node) => node.type === "svg").props.style.left, 650);
  scrollNode.clientWidth = 0;
  scrollNode.scrollLeft = 0;
  ui.props.disabled = true;
  ui.props.playing = true;
  ui.props.playhead = 25;
  ui.render(); ui.flushEffects(); ui.resize(0);
  scroller().props.onScroll({ currentTarget: scrollNode });
  ui.render(); ui.flushEffects();
  assert.equal(ui.nodes.find((node) => node.type === "svg").props.viewBox, "0 0 900 72");
  assert.equal(ui.nodes.find((node) => node.type === "svg").props.style.left, 650);
  scrollNode.clientWidth = 900;
  ui.props.playing = false;
  ui.props.disabled = false;
  ui.render(); ui.flushEffects(); ui.resize(900); ui.render();
  assert.equal(scrollNode.scrollLeft, 650, "reveal restores the last visible viewport instead of recentering");
  assert.equal(ui.nodes.find((node) => node.props?.className === "timelineCanvas").props.style.width, "200%");
});

test("disabling the editor cancels pointer gestures and blocks subsequent seeks and changes", () => {
  for (const kind of ["seek", "cut"]) {
    const calls = [];
    const ui = harness({ onSeek: (time) => calls.push(["seek", time]), onCut: (index, time) => calls.push(["cut", index, time]) });
    const canvas = () => ui.nodes.find((node) => node.props?.className === "timelineCanvas");
    canvas().props.ref.current = { getBoundingClientRect: () => ({ left: 0, width: 900 }) };
    ui.flushEffects();
    let captured = false;
    const target = { focus() {}, setPointerCapture() { captured = true; }, hasPointerCapture: () => captured,
      releasePointerCapture() { captured = false; } };
    const pointer = { button: 0, pointerId: 1, clientX: 300, currentTarget: target, preventDefault() {}, stopPropagation() {} };
    const control = () => kind === "cut" ? ui.nodes.find((node) => node.props?.["data-cut-marker"])
      : ui.nodes.find((node) => node.props?.className === "musicLane");
    control().props.onPointerDown(pointer);
    canvas().props.onPointerMove({ pointerId: 1, clientX: 330 });
    const before = calls.length;
    assert.equal(captured, true);
    ui.props.disabled = true; ui.render(); ui.flushEffects();
    assert.equal(captured, false);
    canvas().props.onPointerUp({ pointerId: 1 });
    control().props.onPointerDown(pointer);
    canvas().props.onPointerMove({ pointerId: 1, clientX: 360 });
    ui.nodes.find((node) => node.props?.["aria-label"] === "Edit playhead").props.onKeyDown(keyEvent("ArrowRight"));
    ui.nodes.find((node) => node.props?.["data-lab-scene-slot"] === "first").props.onClick();
    assert.equal(calls.length, before);
    assert.equal(captured, false);
  }
});

test("scrubbing sends changed positions once and finishes cleanly after pointer release or cancellation", () => {
  for (const finish of ["onPointerUp", "onPointerCancel"]) {
    const calls = [];
    const ui = harness({ onSeek: (time) => calls.push(time) });
    const canvas = ui.nodes.find((node) => node.props?.className === "timelineCanvas");
    canvas.props.ref.current = { getBoundingClientRect: () => ({ left: 0, width: 900 }) };
    ui.flushEffects();
    let captured = false;
    const target = { focus() {}, setPointerCapture() { captured = true; }, hasPointerCapture() { return captured; }, releasePointerCapture() { captured = false; } };
    const begin = () => ui.nodes.find((node) => node.props?.className === "musicLane").props.onPointerDown({
      button: 0, pointerId: 1, clientX: 300, currentTarget: target, preventDefault() {}, stopPropagation() {},
    });
    begin();
    for (let i = 0; i < 100; i++) canvas.props.onPointerMove({ pointerId: 1, clientX: 300 });
    canvas.props.onPointerMove({ pointerId: 2, clientX: 400 });
    canvas.props.onPointerMove({ pointerId: 1, clientX: 330 });
    for (const x of [900, 910, 990, 1000]) canvas.props.onPointerMove({ pointerId: 1, clientX: x });
    for (const x of [0, -10, -50]) canvas.props.onPointerMove({ pointerId: 1, clientX: x });
    assert.deepEqual(calls, [10, 11, 30, 0], "stationary or clamped events must not flood the player with seek commands");
    canvas.props[finish]({ pointerId: 1 });
    assert.equal(captured, false);
    canvas.props.onPointerMove({ pointerId: 1, clientX: 600 });
    assert.equal(calls.length, 4, "an ended gesture never keeps scrubbing");
    begin();
    assert.equal(calls.at(-1), 10, "a new click is always an intentional seek");
    canvas.props[finish]({ pointerId: 1 });
  }
});
