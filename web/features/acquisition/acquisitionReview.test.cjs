const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

// Model native dialog events as queued tasks: close() changes `open` now,
// while its close event may arrive after React has replayed the mount effect.
function reviewHarness({ busy = false } = {}) {
  const effects = [];
  const pendingCloseEvents = [];
  let parentCloses = 0;
  let output;
  let cleanups = [];
  const dialog = {
    open: false,
    showModal() { this.open = true; },
    close() {
      if (!this.open) return;
      this.open = false;
      pendingCloseEvents.push(() => output.props.onClose({ currentTarget: dialog }));
    },
  };
  const react = {
    useRef: (initial) => ({ current: initial }),
    useState: (initial) => [typeof initial === "function" ? initial() : initial, () => {}],
    useEffect: (effect) => effects.push(effect),
  };
  const source = fs.readFileSync(path.join(__dirname, "AcquisitionReview.tsx"), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, {
    exports,
    require(name) {
      if (name === "react") return react;
      if (name === "./model") return { formatBytes: (value) => `${value} B` };
      if (name.endsWith(".css")) return { default: {} };
      if (name === "react/jsx-runtime") return {
        jsx: (type, props) => ({ type, props }),
        jsxs: (type, props) => ({ type, props }),
      };
      throw new Error(`Unexpected module: ${name}`);
    },
  });
  output = exports.default({
    item: {
      id: "review-film", title: "Film", year: 1985, revision: 17,
      review: {
        selected_video: "film.mkv",
        videos: [{ relative_path: "film.mkv", name: "film.mkv", size: 1000 }],
        subtitles: [{ relative_path: "english.srt", excerpt: "Hello" }],
      },
    },
    busy, error: null,
    onClose: () => { parentCloses += 1; },
    onSubmit: async () => { throw new Error("Lifecycle checks must not submit a review"); },
  });
  assert.equal(output.type, "dialog");
  output.props.ref.current = dialog;
  const setup = () => { cleanups = effects.map((effect) => effect()); };
  const cleanup = () => { cleanups.forEach((close) => close?.()); cleanups = []; };
  setup();
  return {
    dialog,
    get parentCloses() { return parentCloses; },
    get pendingCloseCount() { return pendingCloseEvents.length; },
    replayMountEffect() { cleanup(); setup(); },
    deliverCloseEvents() { while (pendingCloseEvents.length) pendingCloseEvents.shift()(); },
    pressEscape() {
      const event = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
      output.props.onCancel(event);
      if (!event.defaultPrevented) dialog.close();
      return event;
    },
    dispose: cleanup,
  };
}

test("Strict Mode replay does not dismiss review when the cleanup close event arrives after reopening", () => {
  const app = reviewHarness();
  try {
    assert.equal(app.dialog.open, true);
    app.replayMountEffect();
    assert.equal(app.dialog.open, true, "the second effect setup reopened the dialog");
    assert.equal(app.pendingCloseCount, 1, "cleanup queued a native close event");
    app.deliverCloseEvents();
    assert.equal(app.parentCloses, 0, "a stale cleanup event must not unmount the reopened review");
    assert.equal(app.dialog.open, true);
  } finally { app.dispose(); }
});

test("a real native close still dismisses review", () => {
  const app = reviewHarness();
  try {
    app.dialog.close();
    assert.equal(app.dialog.open, false);
    app.deliverCloseEvents();
    assert.equal(app.parentCloses, 1);
  } finally { app.dispose(); }
});

test("Escape dismisses an idle review through the native cancel and close lifecycle", () => {
  const app = reviewHarness();
  try {
    const event = app.pressEscape();
    assert.equal(event.defaultPrevented, false);
    assert.equal(app.dialog.open, false);
    app.deliverCloseEvents();
    assert.equal(app.parentCloses, 1);
  } finally { app.dispose(); }
});

test("a busy review prevents Escape cancellation and does not notify dismissal from a close event", () => {
  const app = reviewHarness({ busy: true });
  try {
    const event = app.pressEscape();
    assert.equal(event.defaultPrevented, true);
    assert.equal(app.dialog.open, true);
    assert.equal(app.pendingCloseCount, 0);
    assert.equal(app.parentCloses, 0);
    app.dialog.close();
    app.deliverCloseEvents();
    assert.equal(app.parentCloses, 0);
  } finally { app.dispose(); }
});
