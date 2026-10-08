const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const source = (facet) => ({ kind: "source", facet, source: { unit_id: "shot-4", frame_index: 2 }, display: { filmTitle: "Film", timestamp: 12, keyframeUrl: "/frame.jpg" } });
const image = (facet) => ({ facet, file: {}, display: { label: "Uploaded still", previewUrl: "blob:still" } });
const transfer = () => ({
  data: new Map(), effectAllowed: "uninitialized", dropEffect: "none", files: { item: () => null },
  get types() { return [...this.data.keys()]; },
  setData(type, value) { this.data.set(type, value); }, getData(type) { return this.data.get(type) ?? ""; },
  setDragImage(element) { this.preview = element; },
});
function event(extra = {}) {
  return { defaultPrevented: false, stopped: false, preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; }, ...extra };
}

function setup(overrides = {}) {
  const states = new Map(), listeners = new Map(), windowListeners = new Map(), effects = [], cache = new Map(), calls = [];
  let hooks, cursor, tree, hit;
  let layout = { rail: { left: 100, width: 880, top: 100, bottom: 160 }, trigger: { left: 400, width: 112, top: 100, bottom: 140 } };
  const createElement = () => ({
    style: {}, className: "", children: [],
    get classList() { return { add: (...names) => { this.className += ` ${names.join(" ")}`; } }; },
    setAttribute() {}, remove() {}, append(...children) { this.children.push(...children); },
    getBoundingClientRect: () => ({ width: 240, height: 140 }),
  });
  const document = {
    body: { append() {} }, createElement,
    addEventListener(name, fn) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(fn); },
    removeEventListener(name, fn) { listeners.get(name)?.delete(fn); },
    dispatchEvent(value) { [...(listeners.get(value.type) ?? [])].forEach((fn) => fn(value)); },
    elementFromPoint: () => hit,
  };
  const react = {
    useId: () => "clue-editor",
    useRef(initial) { return hooks[cursor++] ??= { current: initial }; },
    useState(initial) {
      const slot = hooks[cursor++] ??= { value: initial };
      return [slot.value, (next) => { slot.value = typeof next === "function" ? next(slot.value) : next; }];
    },
    useEffect(effect, deps) {
      const slot = hooks[cursor++] ??= {};
      if (deps && slot.deps && deps.length === slot.deps.length && deps.every((value, i) => Object.is(value, slot.deps[i]))) return;
      slot.deps = deps;
      effects.push(() => { slot.cleanup?.(); slot.cleanup = effect(); });
    },
  };
  function load(relative) {
    if (cache.has(relative)) return cache.get(relative);
    const exports = {};
    cache.set(relative, exports);
    const compiled = ts.transpileModule(fs.readFileSync(path.join(__dirname, relative), "utf8"), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
    }).outputText;
    vm.runInNewContext(compiled, {
      exports, document, performance: { now: () => 1000 }, process: { env: {} },
      window: {
        innerWidth: 1200, innerHeight: 800, requestAnimationFrame: (fn) => fn(), setTimeout: (fn) => fn(), clearTimeout() {},
        addEventListener(name, fn) { if (!windowListeners.has(name)) windowListeners.set(name, new Set()); windowListeners.get(name).add(fn); },
        removeEventListener(name, fn) { windowListeners.get(name)?.delete(fn); },
      },
      CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
      require(name) {
        if (name === "react") return react;
        if (name === "react/jsx-runtime") return { jsx: (type, props, key) => ({ type, props, key }), jsxs: (type, props, key) => ({ type, props, key }), Fragment: "fragment" };
        if (name === "@/lib/searchRecipe") return load("../lib/searchRecipe.ts");
        if (name === "@/lib/searchClues") return load("../lib/searchClues.ts");
        if (name === "@/lib/matchReasons") return load("../lib/matchReasons.ts");
        if (name === "@/hooks/useScenePointerDrag") return load("../hooks/useScenePointerDrag.ts");
        if (name === "@/lib/nativeDragPreview") return load("../lib/nativeDragPreview.ts");
        if (name === "@/lib/format") return { formatTime: String };
        return { default: name };
      },
    });
    return exports;
  }
  const props = { clauseCount: 1, drafts: {}, onSource: (...args) => calls.push(["source", ...args]), onMoveImage: (facet) => calls.push(["image", facet]), onLimit: () => calls.push(["limit"]), onBrowse: (facet) => calls.push(["browse", facet]), onCommitText: (...args) => calls.push(["text", ...args]), onRemove: (facet) => calls.push(["remove", facet]), onRemoveImage: () => calls.push(["remove-image"]), ...overrides };
  const component = load("MatchByRail.tsx").default;
  function expand(node, address) {
    if (!node || typeof node !== "object") return node;
    if (Array.isArray(node)) return node.map((child, i) => expand(child, `${address}/${child?.key ?? i}`));
    if (typeof node.type === "function") {
      hooks = states.get(address) ?? [];
      states.set(address, hooks); cursor = 0;
      return expand(node.type(node.props), `${address}/render`);
    }
    node.props = { ...node.props, children: expand(node.props.children, `${address}/children`) };
    const element = {
      dataset: { facet: node.props["data-facet"] },
      closest: () => element, contains: () => false,
      getBoundingClientRect: () => node.props["data-browse-facet"] ? layout.trigger : layout.rail,
      querySelector(selector) {
        const facet = selector.match(/^\[data-browse-facet="([a-z]+)"\]$/)?.[1];
        return facet ? nodes(tree).find((candidate) => candidate.props?.["data-browse-facet"] === facet)?.element : null;
      },
      focus() {},
      setPointerCapture() {}, hasPointerCapture: () => true, releasePointerCapture() {},
    };
    node.element = element;
    if (node.props.ref) node.props.ref.current = element;
    return node;
  }
  function render() {
    tree = expand({ type: component, props }, "rail");
    effects.splice(0).forEach((effect) => effect());
  }
  render();
  return {
    props, calls, render,
    chip: (facet) => nodes(tree).find((node) => node.type === "button" && node.props["data-browse-facet"] === facet),
    tile: (facet) => nodes(tree).find((node) => node.props?.["data-facet"] === facet),
    find: (predicate) => nodes(tree).find(predicate),
    button: (label) => nodes(tree).find((node) => node.type === "button" && text(node).trim() === label),
    hit(facet) { hit = this.tile(facet).element; },
    setLayout(next) { layout = next; },
    resize() { [...(windowListeners.get("resize") ?? [])].forEach((listener) => listener()); render(); },
    get resizeListenerCount() { return windowListeners.get("resize")?.size ?? 0; },
    cleanup() { states.forEach((state) => state.forEach((slot) => slot?.cleanup?.())); },
  };
}

test("collapsed references, including Framing, carry move origin through native drag", () => {
  for (const origin of ["scene", "words", "look", "composition", "mood"]) {
    const app = setup({ drafts: { [origin]: source(origin) }, clauseCount: 3 });
    try {
      const target = origin === "look" ? "scene" : "look";
      const chip = app.chip(origin), dataTransfer = transfer();
      assert.equal(chip.props.draggable, true);
      chip.props.onDragStart(event({ dataTransfer }));
      assert.equal(dataTransfer.effectAllowed, "move");
      assert.equal(dataTransfer.preview.children[0].src, "/frame.jpg", "native movement carries the selected scene image");
      assert.equal(dataTransfer.preview.children[1].children[1].textContent, "Film");
      assert.match(dataTransfer.preview.children[1].children[0].textContent, /^Moving /);
      const over = event({ dataTransfer });
      app.tile(target).props.onDragOver(over);
      assert.equal(over.defaultPrevented, true, "moves still fit when three clauses are active");
      assert.equal(dataTransfer.dropEffect, "move");
      app.tile(target).props.onDrop(event({ dataTransfer }));
      assert.equal(app.calls.length, 1);
      assert.equal(app.calls[0][0], "source");
      assert.equal(app.calls[0][1], target);
      assert.equal(app.calls[0][2].source.unit_id, "shot-4");
      assert.equal(app.calls[0][2].source.frame_index, 2);
      assert.equal(app.calls[0][3], origin, "the existing state transition can remove the origin instead of copying");
    } finally { app.cleanup(); }
  }
});

test("header pointer drag moves Framing onto occupied Look and suppresses its trailing click", () => {
  const app = setup({ drafts: { composition: source("composition"), look: { kind: "text", facet: "look", text: "red" } }, clauseCount: 3 });
  try {
    const chip = app.chip("composition");
    const pointer = { currentTarget: chip.element, pointerId: 1, button: 0, isPrimary: true };
    chip.props.onPointerDown(event({ ...pointer, clientX: 10, clientY: 10 }));
    const duplicateNative = event({ dataTransfer: transfer() });
    chip.props.onDragStart(duplicateNative);
    assert.equal(duplicateNative.defaultPrevented, true);
    app.hit("look");
    chip.props.onPointerMove(event({ ...pointer, clientX: 120, clientY: 10 }));
    chip.props.onPointerUp(event({ ...pointer, clientX: 120, clientY: 10 }));
    assert.equal(app.calls.length, 1);
    assert.equal(app.calls[0][1], "look");
    assert.equal(app.calls[0][3], "composition");
    const click = event();
    chip.props.onClickCapture(click);
    assert.equal(click.defaultPrevented, true, "a completed move must not also open the old category");
    assert.equal(click.stopped, true);
  } finally { app.cleanup(); }
});

test("dropping an indexed reference back on its own category does nothing", () => {
  const app = setup({ drafts: { look: source("look") } });
  try {
    const dataTransfer = transfer();
    app.chip("look").props.onDragStart(event({ dataTransfer }));
    app.tile("look").props.onDrop(event({ dataTransfer }));
    assert.equal(app.calls.length, 0);
  } finally { app.cleanup(); }
});

test("uploaded header references move between Look and Framing and reject semantic destinations", () => {
  for (const origin of ["look", "composition"]) {
    const app = setup({ image: image(origin), clauseCount: 3, targetFacet: origin });
    try {
      const target = origin === "look" ? "composition" : "look";
      const dataTransfer = transfer();
      assert.equal(app.chip(origin).props.draggable, true);
      app.chip(origin).props.onDragStart(event({ dataTransfer }));
      assert.equal(dataTransfer.effectAllowed, "move");
      assert.equal(dataTransfer.types.includes("application/x-scene-recall-source"), false);
      app.tile(origin).props.onDrop(event({ dataTransfer }));
      assert.equal(app.calls.length, 0, "dropping the upload on its current category does nothing");
      for (const semantic of ["scene", "words", "mood"]) {
        const over = event({ dataTransfer });
        app.tile(semantic).props.onDragOver(over);
        assert.equal(over.defaultPrevented, false);
      }
      app.tile(target).props.onDrop(event({ dataTransfer }));
      assert.deepEqual(app.calls, [["image", target]]);
      const fileDrop = transfer();
      fileDrop.setData("Files", "");
      const over = event({ dataTransfer: fileDrop });
      app.tile(target).props.onDragOver(over);
      assert.equal(over.defaultPrevented, false, "reference lookup still does not admit new file drops");
    } finally { app.cleanup(); }
  }
});

test("text and empty chips remain ordinary edit controls with no drag source", () => {
  const app = setup({ drafts: { words: { kind: "text", facet: "words", text: "hello" } } });
  try {
    assert.equal(app.chip("words").props.draggable, false);
    assert.equal(app.chip("look").props.draggable, false);
    app.chip("words").props.onClick();
    app.render();
    assert.equal(app.chip("words").props["aria-expanded"], true);
    assert.equal(app.find((node) => node.type === "input").props.value, "hello");
  } finally { app.cleanup(); }
});

test("indexed and uploaded references open their current reference without a text input", () => {
  const cases = [
    ...["scene", "words", "look", "composition", "mood"].map((facet) => ({ facet, drafts: { [facet]: source(facet) } })),
    ...["look", "composition"].map((facet) => ({ facet, image: image(facet) })),
  ];
  for (const { facet, ...overrides } of cases) {
    const app = setup(overrides);
    try {
      app.chip(facet).props.onClick(); app.render();
      assert.equal(app.chip(facet).props["aria-expanded"], true);
      assert.equal(app.find((node) => node.type === "input"), undefined, `${facet} shows the source, not an empty replacement query`);
      assert.ok(app.find((node) => node.props?.className === "clue-source"));
      assert.ok(app.button("Change scene"));
      assert.deepEqual(app.calls, [], "opening an existing reference does not start lookup or change search state");
      assert.equal(Boolean(app.button("Use text instead")), facet !== "composition", "Framing has no text adapter");
    } finally { app.cleanup(); }
  }
});

test("empty Framing opens reference lookup while empty text categories open text editing", () => {
  const app = setup();
  try {
    app.chip("composition").props.onClick(); app.render();
    assert.deepEqual(app.calls, [["browse", "composition"]]);
    app.calls.length = 0;
    app.chip("words").props.onClick(); app.render();
    assert.equal(app.find((node) => node.type === "input").props.value, "");
    assert.deepEqual(app.calls, []);
  } finally { app.cleanup(); }
});

test("switching a reference to text stages a replacement until nonempty text is applied", () => {
  for (const overrides of [{ drafts: { look: source("look") } }, { image: image("look") }]) {
    const app = setup(overrides);
    try {
      app.chip("look").props.onClick(); app.render();
      app.button("Use text instead").props.onClick(); app.render();
      const input = app.find((node) => node.type === "input");
      assert.equal(input.props.value, "");
      assert.equal(app.button("Apply").props.disabled, true);
      input.props.onKeyDown(event({ key: "Enter" }));
      assert.deepEqual(app.calls, [], "Enter cannot apply an empty replacement and remove the reference");
      app.button("Keep reference").props.onClick(); app.render();
      assert.equal(app.find((node) => node.type === "input"), undefined);
      assert.deepEqual(app.calls, [], "cancelling staged text preserves the reference without a new search");
      app.button("Use text instead").props.onClick(); app.render();
      app.find((node) => node.type === "input").props.onChange({ target: { value: "blue haze" } }); app.render();
      assert.equal(app.button("Apply").props.disabled, false);
      app.button("Apply").props.onClick(); app.render();
      assert.deepEqual(app.calls, [["text", "look", "blue haze"]]);
      assert.equal(app.chip("look").props["aria-expanded"], false);
    } finally { app.cleanup(); }
  }
});

test("closing staged reference text and opening another category does not leak replacement mode", () => {
  const app = setup({ drafts: { look: source("look"), mood: source("mood") } });
  try {
    app.chip("look").props.onClick(); app.render();
    app.button("Use text instead").props.onClick(); app.render();
    app.find((node) => node.type === "input").props.onChange({ target: { value: "unfinished" } }); app.render();
    app.find((node) => node.props?.["aria-label"] === "Close detail editor").props.onClick(); app.render();
    app.chip("look").props.onClick(); app.render();
    assert.equal(app.find((node) => node.type === "input"), undefined);
    app.button("Use text instead").props.onClick(); app.render();
    app.chip("mood").props.onClick(); app.render();
    assert.equal(app.find((node) => node.type === "input"), undefined);
    assert.ok(app.button("Keep reference") === undefined);
    assert.deepEqual(app.calls, []);
  } finally { app.cleanup(); }
});

test("changing or removing a reference uses explicit editor actions", () => {
  for (const overrides of [{ drafts: { look: source("look") } }, { image: image("look") }]) {
    const app = setup(overrides);
    try {
      assert.equal(app.find((node) => node.props?.["aria-label"] === "Remove Look clue"), undefined, "removal is not appended to the collapsed chip");
      app.chip("look").props.onClick(); app.render();
      app.button("Change scene").props.onClick(); app.render();
      assert.deepEqual(app.calls, [["browse", "look"]]);
      app.calls.length = 0;
      app.chip("look").props.onClick(); app.render();
      app.find((node) => node.props?.["aria-label"] === "Remove Look clue").props.onClick(); app.render();
      assert.deepEqual(app.calls, overrides.image ? [["remove-image"]] : [["remove", "look"]]);
      assert.equal(app.chip("look").props["aria-expanded"], false);
    } finally { app.cleanup(); }
  }
});

test("collapsed text chips expose the clue accessibly without adding inline query content", () => {
  const app = setup({ drafts: { words: { kind: "text", facet: "words", text: "a long remembered line of dialogue" } } });
  try {
    assert.equal(text(app.chip("words")), "Words");
    assert.match(app.chip("words").props["aria-label"], /a long remembered line of dialogue/);
    assert.equal(nodes(app.tile("words")).filter((node) => node.type === "button").length, 1);
    app.chip("words").props.onClick(); app.render();
    assert.equal(app.find((node) => node.type === "input").props.value, "a long remembered line of dialogue");
  } finally { app.cleanup(); }
});

test("native indexed and image moves close their old editor and active reference lookup", () => {
  for (const overrides of [{ drafts: { look: source("look") } }, { image: image("look") }]) {
    let closed = 0;
    const app = setup({ ...overrides, onCloseReference: () => { closed += 1; } });
    try {
      app.chip("look").props.onClick(); app.render();
      const dataTransfer = transfer();
      app.chip("look").props.onDragStart(event({ dataTransfer }));
      app.tile("composition").props.onDrop(event({ dataTransfer })); app.render();
      assert.equal(app.chip("look").props["aria-expanded"], false, "moving closes the old category editor");
      app.props.targetFacet = "look";
      app.render();
      app.chip("look").props.onDragStart(event({ dataTransfer }));
      app.tile("composition").props.onDrop(event({ dataTransfer }));
      assert.equal(closed, 1, "the successful move dismisses the old reference lookup");
    } finally { app.cleanup(); }
  }
});

test("Refine disclosure, Escape, and committing a detail keep the panel state consistent", () => {
  const app = setup();
  const panel = () => app.find((node) => node.props?.className?.split(" ").includes("clues-panel"));
  const toggle = () => app.find((node) => node.props?.className === "clues-refine-toggle");
  try {
    assert.equal(toggle().props["aria-expanded"], false);
    assert.equal(panel().props.hidden, true);
    assert.equal(toggle().props["aria-controls"], panel().props.id);
    toggle().props.onClick(); app.render();
    assert.equal(toggle().props["aria-expanded"], true);
    assert.equal(panel().props.hidden, false);
    app.find((node) => node.type === "section").props.onKeyDown(event({ key: "Escape" })); app.render();
    assert.equal(toggle().props["aria-expanded"], false);
    assert.equal(panel().props.hidden, true, "Escape dismisses even before choosing a category");
    toggle().props.onClick(); app.render();
    app.chip("mood").props.onClick(); app.render();
    app.find((node) => node.type === "input").props.onChange({ target: { value: "wistful" } }); app.render();
    app.button("Apply").props.onClick(); app.render();
    assert.deepEqual(app.calls, [["text", "mood", "wistful"]]);
    assert.equal(panel().props.hidden, true, "applying returns attention to results");
    assert.equal(toggle().props["aria-expanded"], false);
    assert.equal(app.resizeListenerCount, 0, "the shared surface sizes in CSS without per-button measurements");
  } finally { app.cleanup(); }
});

test("active refinements stay visible when collapsed and reopen their own editor", () => {
  const app = setup({ drafts: { mood: { kind: "text", facet: "mood", text: "quiet anticipation" } } });
  try {
    const summary = app.find((node) => node.props?.className === "clue-summary");
    assert.match(text(summary), /Moodquiet anticipation/);
    assert.equal(app.find((node) => node.props?.className === "clues-refine-count").props.children, 1);
    summary.props.onClick(); app.render();
    assert.equal(app.find((node) => node.type === "input").props.value, "quiet anticipation");
    assert.equal(app.chip("mood").props["aria-expanded"], true);
  } finally { app.cleanup(); }
});

test("opening Refine hides idle suggestions and incoming scene drags reveal all targets", () => {
  const idleContent = { type: "span", props: { "data-idle": true, children: "Try a search" } };
  const app = setup({ idleContent, drafts: { look: source("look") } });
  try {
    assert.ok(app.find((node) => node.props?.["data-idle"]));
    app.find((node) => node.props?.className === "clues-refine-toggle").props.onClick(); app.render();
    assert.equal(app.find((node) => node.props?.["data-idle"]), undefined);
    app.find((node) => node.props?.className === "clues-refine-toggle").props.onClick(); app.render();
    const summary = app.find((node) => node.props?.className === "clue-summary");
    const pointer = { currentTarget: summary.element, pointerId: 2, button: 0, isPrimary: true };
    summary.props.onPointerDown(event({ ...pointer, clientX: 10, clientY: 10 }));
    summary.props.onPointerMove(event({ ...pointer, clientX: 120, clientY: 10 })); app.render();
    assert.equal(app.find((node) => node.props?.className?.split(" ").includes("clues-panel")).props.hidden, false);
    assert.ok(app.tile("composition"));
  } finally { app.cleanup(); }
});

test("Search without Framing closes its reference editor but preserves another category editor", () => {
  for (const overrides of [{ drafts: { composition: source("composition") } }, { image: image("composition") }]) {
    const app = setup({ hasMainText: true, ...overrides });
    try {
      app.chip("composition").props.onClick(); app.render();
      app.button("Search without Framing").props.onClick(); app.render();
      assert.equal(app.chip("composition").props["aria-expanded"], false);
      assert.equal(app.find((node) => node.props?.className?.split(" ").includes("clue-editor")), undefined);
      assert.deepEqual(app.calls, overrides.image ? [["remove-image"]] : [["remove", "composition"]]);
      app.calls.length = 0;
      app.chip("look").props.onClick(); app.render();
      app.button("Search without Framing").props.onClick(); app.render();
      assert.equal(app.chip("look").props["aria-expanded"], true, "removing Framing does not dismiss a different category being edited");
    } finally { app.cleanup(); }
  }
});

test("Change aspect stages an indexed reference move at the three-clause limit and explicitly labels replacements", () => {
  for (const occupied of [false, true]) {
    const original = source("look");
    const app = setup({ clauseCount: 3, drafts: { look: original,
      ...(occupied ? { mood: { kind: "text", facet: "mood", text: "uneasy" } } : {}) } });
    try {
      app.chip("look").props.onClick(); app.render();
      app.button("Change aspect").props.onClick(); app.render();
      const select = app.find((node) => node.type === "select");
      assert.deepEqual(nodes(select).filter((node) => node.type === "option").map((node) => node.props.value), ["scene", "words", "look", "composition", "mood"]);
      assert.equal(select.props.value, "look");
      assert.equal(app.button("Move to Look").props.disabled, true);
      select.props.onChange({ target: { value: "mood" } }); app.render();
      assert.deepEqual(app.calls, [], "changing the selector alone never edits the recipe");
      const commit = app.button(occupied ? "Replace Mood" : "Move to Mood");
      assert.ok(commit);
      assert.equal(commit.props.disabled, false, "moving an existing clause does not consume a fourth slot");
      commit.props.onClick(); app.render();
      assert.equal(app.calls.length, 1);
      assert.deepEqual(JSON.parse(JSON.stringify(app.calls[0])), ["source", "mood", { ...original, facet: "mood" }, "look"]);
      assert.equal(app.calls[0][2].source, original.source, "the same indexed frame remains the reference");
      assert.equal(app.chip("look").props["aria-expanded"], false);
      assert.equal(app.find((node) => node.type === "select"), undefined);
    } finally { app.cleanup(); }
  }
});

test("uploaded references expose only Look and Framing and use the existing image move callback", () => {
  for (const origin of ["look", "composition"]) {
    const target = origin === "look" ? "composition" : "look";
    const app = setup({ clauseCount: 3, image: image(origin), drafts: { [target]: source(target) } });
    try {
      app.chip(origin).props.onClick(); app.render();
      app.button("Change aspect").props.onClick(); app.render();
      const select = app.find((node) => node.type === "select");
      assert.deepEqual(nodes(select).filter((node) => node.type === "option").map((node) => node.props.value), ["look", "composition"]);
      select.props.onChange({ target: { value: target } }); app.render();
      const commit = app.button(`Replace ${target === "look" ? "Look" : "Framing"}`);
      assert.ok(commit);
      assert.equal(commit.props.disabled, false);
      commit.props.onClick(); app.render();
      assert.deepEqual(app.calls, [["image", target]]);
    } finally { app.cleanup(); }
  }
});

test("canceling or leaving Change aspect preserves the reference and does not leak its staged destination", () => {
  const app = setup({ drafts: { look: source("look"), mood: source("mood") } });
  try {
    app.chip("look").props.onClick(); app.render();
    app.button("Change aspect").props.onClick(); app.render();
    app.find((node) => node.type === "select").props.onChange({ target: { value: "composition" } }); app.render();
    app.button("Cancel").props.onClick(); app.render();
    assert.equal(app.find((node) => node.type === "select"), undefined);
    assert.deepEqual(app.calls, []);
    app.button("Change aspect").props.onClick(); app.render();
    assert.equal(app.find((node) => node.type === "select").props.value, "look");
    app.chip("mood").props.onClick(); app.render();
    assert.equal(app.find((node) => node.type === "select"), undefined);
    app.button("Change aspect").props.onClick(); app.render();
    assert.equal(app.find((node) => node.type === "select").props.value, "mood");
    app.button("Use text instead").props.onClick(); app.render();
    assert.equal(app.find((node) => node.type === "select"), undefined);
    assert.deepEqual(app.calls, []);
  } finally { app.cleanup(); }
});

test("an over-limit draft can only move a reference when the move restores the three-clause limit", () => {
  const app = setup({ clauseCount: 4, drafts: { look: source("look"), mood: source("mood") } });
  try {
    app.chip("look").props.onClick(); app.render();
    app.button("Change aspect").props.onClick(); app.render();
    app.find((node) => node.type === "select").props.onChange({ target: { value: "words" } }); app.render();
    assert.equal(app.button("Move to Words").props.disabled, true);
    app.button("Move to Words").props.onClick();
    assert.deepEqual(app.calls, [["limit"]]);
    app.calls.length = 0;
    app.find((node) => node.type === "select").props.onChange({ target: { value: "mood" } }); app.render();
    assert.equal(app.button("Replace Mood").props.disabled, false);
    app.button("Replace Mood").props.onClick();
    assert.equal(app.calls[0][0], "source");
  } finally { app.cleanup(); }
});

test("IME Enter confirms composition without committing a detail, while ordinary Enter still applies", () => {
  const app = setup();
  try {
    app.chip("mood").props.onClick(); app.render();
    app.find((node) => node.type === "input").props.onChange({ target: { value: "静かな" } }); app.render();
    for (const properties of [{ nativeEvent: { isComposing: true } }, { nativeEvent: {}, keyCode: 229 }]) {
      const key = event({ key: "Enter", ...properties });
      app.find((node) => node.type === "input").props.onKeyDown(key);
      assert.equal(key.defaultPrevented, false);
      assert.deepEqual(app.calls, []);
    }
    const key = event({ key: "Enter", nativeEvent: { isComposing: false } });
    app.find((node) => node.type === "input").props.onKeyDown(key); app.render();
    assert.equal(key.defaultPrevented, true);
    assert.deepEqual(app.calls, [["text", "mood", "静かな"]]);
    assert.equal(app.chip("mood").props["aria-expanded"], false);
  } finally { app.cleanup(); }
});

test("uploaded Look discloses its visual gate and removal leaves unrelated reference editing alone", () => {
  const app = setup({ hasMainText: true, image: image("look"), drafts: { composition: source("composition"), mood: source("mood") } });
  try {
    const notices = nodes(app.find((node) => node.type === "section")).filter((node) => node.props?.className === "clues-framing-note");
    assert.equal(notices.length, 2);
    assert.ok(notices.some((node) => /Look image picks the visual shortlist/.test(text(node))));
    assert.ok(app.button("Search without Framing"));
    app.chip("mood").props.onClick(); app.render();
    app.button("Search without Look image").props.onClick(); app.render();
    assert.deepEqual(app.calls, [["remove-image"]]);
    assert.equal(app.chip("mood").props["aria-expanded"], true);
  } finally { app.cleanup(); }
  const indexed = setup({ hasMainText: true, drafts: { look: source("look") } });
  try {
    assert.equal(indexed.button("Search without Look image"), undefined);
    assert.equal(indexed.find((node) => node.props?.className === "clues-framing-note"), undefined, "indexed Look is a preference, not an uploaded-image gate");
  } finally { indexed.cleanup(); }
});

test("the shortlist note appears only once a description orders the shortlist", () => {
  const app = setup({ drafts: { composition: source("composition") } });
  try {
    assert.equal(app.find((node) => node.props?.className === "clues-framing-note"), undefined, "a Framing-only search has nothing to order");
    assert.equal(app.button("Search without Framing"), undefined);
  } finally { app.cleanup(); }
});

test("dragging a reference out of the Refine area removes it, and Undo puts it back", () => {
  const draft = source("look");
  const app = setup({ drafts: { look: draft } });
  try {
    const chip = app.chip("look");
    const pointer = { currentTarget: chip.element, pointerId: 3, button: 0, isPrimary: true };
    chip.props.onPointerDown(event({ ...pointer, clientX: 400, clientY: 120 }));
    chip.props.onPointerMove(event({ ...pointer, clientX: 400, clientY: 600 }));
    app.render();
    assert.match(text(app.find((node) => node.props?.className?.includes?.("clues-drag-hint"))), /Release to remove Look/);
    chip.props.onPointerUp(event({ ...pointer, clientX: 400, clientY: 600 }));
    assert.deepEqual(app.calls, [["remove", "look"]]);
    app.render();
    assert.match(text(app.find((node) => node.props?.className === "clues-removed")), /Look reference removed/);
    app.button("Undo").props.onClick();
    assert.deepEqual(app.calls[1], ["source", "look", draft], "undo restores the same scene reference");
  } finally { app.cleanup(); }
});

test("releasing a dragged reference inside the Refine area but off any category changes nothing", () => {
  const app = setup({ drafts: { look: source("look") } });
  try {
    const chip = app.chip("look");
    const pointer = { currentTarget: chip.element, pointerId: 4, button: 0, isPrimary: true };
    chip.props.onPointerDown(event({ ...pointer, clientX: 400, clientY: 120 }));
    chip.props.onPointerMove(event({ ...pointer, clientX: 600, clientY: 130 }));
    chip.props.onPointerUp(event({ ...pointer, clientX: 600, clientY: 130 }));
    assert.deepEqual(app.calls, []);
  } finally { app.cleanup(); }
});

test("an uploaded image dragged out natively is removed and Undo re-adds the same file", () => {
  const still = image("composition");
  const app = setup({ image: still, onImageFile: (file, facet) => app.calls.push(["image-file", facet, file]) });
  try {
    const chip = app.chip("composition"), dataTransfer = transfer();
    chip.props.onDragStart(event({ dataTransfer }));
    chip.props.onDragEnd(event({ dataTransfer, clientX: 400, clientY: 700 }));
    assert.deepEqual(app.calls, [["remove-image"]]);
    app.render();
    app.button("Undo").props.onClick();
    assert.deepEqual(app.calls[1], ["image-file", "composition", still.file]);
  } finally { app.cleanup(); }
});

test("a scene reference shows what its category reads from it, on the chip and in its editor", () => {
  const words = source("words");
  const evidence = { clause_id: "words", facet: "words", adapter: "dialogue+ocr", source: { unit_id: "shot-4", frame_index: 2 },
    evidence: [{ type: "text", view: "dialogue", text: "That is a beer." }, { type: "text", view: "ocr", text: "PENNY PACK" }] };
  const app = setup({ drafts: { words }, sourceEvidence: { words: evidence } });
  try {
    const summary = app.find((node) => node.type === "button" && node.props.className === "clue-summary");
    assert.match(text(summary), /Words“That is a beer.” · PENNY PACK/);
    assert.match(summary.props.title, /\(from Film\)/);
    app.chip("words").props.onClick(); app.render();
    const reading = text(app.find((node) => node.props?.className === "clue-reading"));
    assert.match(reading, /Searching for its words/);
    assert.match(reading, /DialogueThat is a beer\./);
    assert.match(reading, /On-screen textPENNY PACK/);
  } finally { app.cleanup(); }
  const stale = setup({ drafts: { words }, sourceEvidence: { words: { ...evidence, source: { unit_id: "other", frame_index: 0 } } } });
  try {
    const summary = stale.find((node) => node.type === "button" && node.props.className === "clue-summary");
    assert.match(text(summary), /WordsFilm/, "evidence for a different scene is never shown");
  } finally { stale.cleanup(); }
});

test("visual categories say they search the picture itself, not words", () => {
  const app = setup({ drafts: { composition: source("composition") } });
  try {
    app.chip("composition").props.onClick(); app.render();
    assert.match(text(app.find((node) => node.props?.className === "clue-reading")), /where people and things sit in the frame\. No words are used\./);
  } finally { app.cleanup(); }
});
