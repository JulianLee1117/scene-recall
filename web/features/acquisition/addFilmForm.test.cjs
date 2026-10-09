const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

function load(file, modules = {}) {
  const output = ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInNewContext(output, { exports, FormData, AbortController, require(name) {
    if (name in modules) return modules[name];
    if (name === "@/components/DirectionIcon") return load("../../components/DirectionIcon.tsx");
    if (name.endsWith(".css")) return { default: {} };
    if (name === "react/jsx-runtime") {
      const jsx = (type, props) => typeof type === "function" ? type(props) : ({ type, props });
      return { jsx, jsxs: jsx };
    }
    throw new Error(`Unexpected module: ${name}`);
  } });
  return exports;
}
const metadata = load("releaseMetadata.ts");
const model = load("model.ts");
const plain = (value) => JSON.parse(JSON.stringify(value));
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
const deferred = () => { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return { promise, resolve }; };
const status = { downloader: { configured: true, available: true }, search: { configured: true, available: true }, monitor: { running: true } };
const releases = [
  { id: "first", title: "Challengers.2024.1080p.WEB-DL.DDP5.1.H.264-GROUP", size: 3 * 1024 ** 3, seeders: 14, indexer: "Indexer A" },
  { id: "second", title: "Challengers (2024) 2160p BluRay HEVC HDR10", size: 12 * 1024 ** 3, seeders: 7, indexer: "Indexer B" },
];

function harness({ request = async () => ({ results: releases }), onQueue = async () => true, initialProps = {} } = {}) {
  const hooks = [], focused = [], elements = new Map();
  const props = { status, busy: false, onQueue, ...initialProps };
  let cursor = 0, output, scheduled = false, disposed = false, effects = [];
  const schedule = () => { if (!scheduled && !disposed) { scheduled = true; queueMicrotask(render); } };
  const react = {
    useState(initial) {
      const i = cursor++; hooks[i] ??= { value: typeof initial === "function" ? initial() : initial };
      return [hooks[i].value, (update) => { const value = typeof update === "function" ? update(hooks[i].value) : update;
        if (!Object.is(value, hooks[i].value)) { hooks[i].value = value; schedule(); } }];
    },
    useRef(initial) { const i = cursor++; return hooks[i] ??= { current: initial }; },
    useEffect(effect, deps) {
      const i = cursor++;
      if (!hooks[i] || !same(deps, hooks[i].deps)) { const old = hooks[i]; hooks[i] = { deps, cleanup: old?.cleanup };
        effects.push(() => { hooks[i].cleanup?.(); hooks[i].cleanup = effect(); }); }
    },
  };
  const form = load("AddFilmForm.tsx", { react, "./model": model, "./releaseMetadata": metadata,
    "./api": { acquisitionRequest: request, messageOf: (problem) => problem.message } }).default;
  function render() {
    if (disposed) return;
    scheduled = false; cursor = 0; output = form(props);
    for (const node of nodes(output)) {
      if (!node.props?.ref || typeof node.props.ref !== "object") continue;
      const ref = node.props.ref;
      if (!elements.has(ref)) elements.set(ref, { focus: (options) => focused.push({ type: node.type, placeholder: node.props.placeholder, options }) });
      ref.current = elements.get(ref);
    }
    const run = effects; effects = []; run.forEach((effect) => effect());
  }
  const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
  render();
  return { focused, flush,
    get state() { return output; },
    find: (predicate) => nodes(output).find(predicate),
    button: (label) => nodes(output).find((node) => node.type === "button" && text(node) === label),
    input: (label) => nodes(nodes(output).find((node) => node.type === "label" && text(node).startsWith(label))).find((node) => node.type === "input"),
    async search(query = "Challengers 2024") {
      this.input("Search movie releases").props.onChange({ target: { value: query } }); await flush();
      this.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} }); await flush();
    },
    async choose(id = "first") { this.find((node) => node.props?.["data-release-id"] === id).props.onClick(); await flush(); },
    async submit() { nodes(output).filter((node) => node.type === "form").at(-1).props.onSubmit({ preventDefault() {} }); await flush(); },
    async update(next) { Object.assign(props, next); render(); await flush(); },
    dispose() { disposed = true; hooks.forEach((hook) => hook.cleanup?.()); },
  };
}

test("release hints retain numeric titles and only parse one unambiguous later year", () => {
  assert.deepEqual(plain(metadata.releaseMetadata(releases[0].title)), { title: "Challengers", year: "2024", quality: ["1080p", "WEB-DL", "H.264"] });
  for (const [name, title, year] of [
    ["1917.2019.1080p.BluRay.mkv", "1917", "2019"],
    ["2001.A.Space.Odyssey.1968.2160p", "2001 A Space Odyssey", "1968"],
    ["Mirror (1975) 1080p", "Mirror", "1975"],
    ["WALL-E.2008.1080p", "WALL-E", "2008"],
  ]) {
    assert.equal(metadata.releaseMetadata(name).title, title, name);
    assert.equal(metadata.releaseMetadata(name).year, year, name);
  }
  for (const name of ["Challengers.1080p", "Film.2023.2024.1080p", "Blade.Runner.2049.2017.1080p", "[GROUP].Mirror.1975.1080p", "Film.Collection.2024.1080p", "Film.2024.S01E01", "https://example.com/Film.2024", "Film.1870", "Film.2101"]) {
    assert.equal(metadata.releaseMetadata(name).title, "", name);
    assert.equal(metadata.releaseMetadata(name).year, "", name);
  }
});

test("explicit camera and telesync release tokens stay visible without matching title words", () => {
  assert.deepEqual(plain(metadata.releaseMetadata("Challengers.2024.HDTS.c1nem4.x264").quality), ["Telesync", "H.264"]);
  assert.deepEqual(plain(metadata.releaseMetadata("Challengers.2024.720p.HD-TS").quality), ["720p", "Telesync"]);
  for (const token of ["CAM", "HDCAM", "HD-CAM"]) {
    assert.deepEqual(plain(metadata.releaseMetadata(`Challengers.2024.${token}.x264`).quality), ["Camera recording", "H.264"], token);
  }
  for (const token of ["TS", "HDTS", "HD-TS", "telesync"]) {
    assert.ok(metadata.releaseMetadata(`Challengers.2024.${token}`).quality.includes("Telesync"), token);
  }
  for (const name of ["Cam.2018.1080p.WEB-DL", "The.TS.Club.2024.1080p", "Challengers.2024.1080p.DTS", "Challengers.2024.1080p.CAMPAIGN", "Challengers.2024.1080p"]) {
    const hints = metadata.releaseMetadata(name).quality;
    assert.equal(hints.includes("Camera recording"), false, name);
    assert.equal(hints.includes("Telesync"), false, name);
  }
});

test("search progressively reveals a compact choice and editable confirmation without queuing automatically", async () => {
  const calls = [];
  const app = harness({ onQueue: async (...args) => { calls.push(args); return true; } });
  try {
    assert.equal(app.input("Film title"), undefined); assert.equal(app.button("Queue film"), undefined);
    await app.search();
    assert.equal(app.input("Film title"), undefined);
    assert.match(text(app.state), /Challengers \(2024\)/); assert.match(text(app.state), /1080p · WEB-DL · H.264/);
    assert.ok(app.find((node) => node.type === "details" && text(node).includes(releases[0].title)), "exact filename stays discoverable");
    await app.choose();
    assert.equal(app.find((node) => node.props?.["data-release-id"]), undefined);
    assert.equal(app.input("Film title").props.value, "Challengers"); assert.equal(app.input("Year").props.value, "2024");
    assert.equal(calls.length, 0);
    assert.equal(app.find((node) => node.type === "details" && text(node).startsWith("Add edition")).props.open, undefined);
    await app.submit();
    assert.deepEqual(plain(calls), [["/release", { title: "Challengers", year: 2024, edition: "", release_id: "first" }]]);
  } finally { app.dispose(); }
});

test("changing releases preserves manual corrections while a new query starts fresh", async () => {
  const calls = [];
  const app = harness({ onQueue: async (...args) => { calls.push(args); return true; } });
  try {
    await app.search(); await app.choose();
    app.input("Film title").props.onChange({ target: { value: "My corrected title" } });
    app.input("Year").props.onChange({ target: { value: "2023" } });
    app.input("Edition").props.onChange({ target: { value: "Restored" } }); await app.flush();
    app.button("Change release").props.onClick(); await app.flush(); await app.choose("second");
    assert.equal(app.input("Film title").props.value, "My corrected title");
    assert.equal(app.input("Year").props.value, "2023"); assert.equal(app.input("Edition").props.value, "Restored");
    await app.search("Another query"); await app.choose("second");
    assert.equal(app.input("Film title").props.value, "Challengers");
    assert.equal(app.input("Year").props.value, "2024"); assert.equal(app.input("Edition").props.value, "");
    assert.equal(calls.length, 0);
    app.input("Film title").props.onChange({ target: { value: "Corrected film" } }); await app.flush();
    await app.submit();
    assert.deepEqual(plain(calls), [["/release", { title: "Corrected film", year: 2024, edition: "", release_id: "second" }]]);
  } finally { app.dispose(); }
});

test("source changes abort pending searches so late results cannot replace the current flow", async () => {
  const pending = deferred(); let signal;
  const app = harness({ request: (_url, init) => { signal = init.signal; return pending.promise; } });
  try {
    await app.search();
    app.button("Magnet link").props.onClick(); await app.flush();
    assert.equal(signal.aborted, true);
    pending.resolve({ results: releases }); await app.flush();
    app.button("Search releases").props.onClick(); await app.flush();
    assert.equal(app.find((node) => node.props?.["data-release-id"]), undefined);
    assert.equal(app.button("Queue film"), undefined);
  } finally { app.dispose(); }
});

test("downloaded files opens independently of downloader availability and only when provided", () => {
  const downloadedFiles = { type: "section", props: { children: "Incoming files to review" } };
  const app = harness({ initialProps: {
    status: { ...status, downloader: { configured: false, available: false } },
    initialSource: "downloaded", downloadedFiles,
  } });
  const withoutSlot = harness({ initialProps: { initialSource: "downloaded" } });
  try {
    assert.equal(app.button("Downloaded files").props["aria-pressed"], true);
    assert.match(text(app.state), /Incoming files to review/);
    assert.equal(app.find((node) => node.type === "form"), undefined);
    assert.equal(app.input("Film title"), undefined);
    assert.equal(app.button("Queue film"), undefined);
    assert.equal(app.focused.length, 1);
    assert.equal(app.focused[0].type, "button", "incoming shortcut transfers focus to the selected source button");
    assert.equal(app.focused[0].options.preventScroll, true);
    assert.equal(withoutSlot.button("Downloaded files"), undefined);
    assert.ok(withoutSlot.input("Search movie releases"), "missing incoming content retains the usual source");
  } finally { app.dispose(); withoutSlot.dispose(); }
});

test("switching to downloaded files cancels searches, clears errors and preserves deliberate download edits", async () => {
  const pending = deferred(); let signal, queued = 0;
  const app = harness({
    request: (_url, init) => { signal = init.signal; return pending.promise; },
    onQueue: async () => { queued++; return true; },
    initialProps: {
      downloadedFiles: { type: "section", props: { children: "Incoming files to review" } },
      onClearRequestError: () => { void app.update({ requestError: null }); },
    },
  });
  try {
    await app.search();
    await app.update({ requestError: "Previous download failed." });
    const focusCount = app.focused.length;
    app.button("Downloaded files").props.onClick(); await app.flush();
    assert.equal(signal.aborted, true);
    assert.equal(app.find((node) => node.props?.role === "alert"), undefined);
    assert.equal(app.focused.length, focusCount + 1);
    assert.equal(app.focused.at(-1).type, "button", "source selection keeps focus on the activated source button");
    pending.resolve({ results: releases }); await app.flush();
    assert.match(text(app.state), /Incoming files to review/);
    app.button("Search releases").props.onClick(); await app.flush();
    assert.equal(app.find((node) => node.props?.["data-release-id"]), undefined);
    app.button("Magnet link").props.onClick(); await app.flush();
    app.input("Magnet link").props.onChange({ target: { value: "magnet:?xt=urn:btih:example" } });
    app.input("Film title").props.onChange({ target: { value: "Corrected title" } }); await app.flush();
    app.button("Downloaded files").props.onClick(); await app.flush();
    assert.equal(app.button("Queue film"), undefined);
    app.button("Magnet link").props.onClick(); await app.flush();
    assert.equal(app.input("Film title").props.value, "Corrected title");
    assert.equal(app.input("Magnet link").props.value, "magnet:?xt=urn:btih:example");
    assert.equal(queued, 0, "browsing incoming files never submits a download");
  } finally { app.dispose(); }
});

test("online sources explain download setup and connection issues without blocking downloaded files", async () => {
  for (const [connection, message, disabled] of [
    [{ ...status, downloader: { configured: false, available: false } }, /Set up downloads/, true],
    [{ ...status, downloader: { configured: true, available: false } }, /downloader is unavailable/, false],
    [{ ...status, monitor: { running: false } }, /queued until the monitor starts/, false],
  ]) {
    const app = harness({ initialProps: {
      status: connection,
      downloadedFiles: { type: "section", props: { children: "Incoming files to review" } },
    } });
    try {
      assert.match(text(app.state), message);
      assert.match(text(app.state), /Queue → Download settings/);
      app.button("Magnet link").props.onClick(); await app.flush();
      assert.equal(app.button("Queue film").props.disabled, disabled);
      app.button("Downloaded files").props.onClick(); await app.flush();
      assert.doesNotMatch(text(app.state), /Queue → Download settings/);
      assert.match(text(app.state), /Incoming files to review/);
      assert.equal(app.button("Downloaded files").props.disabled, false);
    } finally { app.dispose(); }
  }
});

test("empty release searches suggest a title and year and clear that guidance when edited", async () => {
  const calls = [];
  const app = harness({ request: async (url) => { calls.push(url); return { results: [] }; } });
  try {
    await app.search("  Moonlight  ");
    assert.deepEqual(calls, ["/search?q=Moonlight"]);
    const message = app.find((node) => node.props?.role === "status" && text(node).includes("configured search"));
    assert.match(text(message), /configured search returned no releases/);
    assert.match(text(message), /title and release year/);
    assert.match(text(message), /magnet link/);
    assert.equal(app.find((node) => node.props?.role === "alert"), undefined);
    assert.equal(app.button("Search").props.disabled, false);
    assert.equal(app.button("Queue film"), undefined);
    app.input("Search movie releases").props.onChange({ target: { value: "aftersun 2022" } }); await app.flush();
    assert.equal(text(app.state).includes("configured search returned no releases"), false);
    app.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} }); await app.flush();
    assert.equal(calls.at(-1), "/search?q=aftersun%202022");
  } finally { app.dispose(); }
});

test("provider failures show an error rather than the previous empty-search guidance", async () => {
  let fail = false;
  const app = harness({ request: async () => {
    if (fail) throw new Error("Acquisition provider is unavailable; check its configuration and connection");
    return { results: [] };
  } });
  try {
    await app.search("Moonlight");
    assert.match(text(app.state), /configured search returned no releases/);
    fail = true;
    app.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} }); await app.flush();
    assert.match(text(app.find((node) => node.props?.role === "alert")), /Acquisition provider is unavailable/);
    assert.equal(text(app.state).includes("configured search returned no releases"), false);
    assert.equal(app.button("Searching…"), undefined);
    assert.equal(app.button("Search").props.disabled, false);
    assert.equal(app.input("Search movie releases").props.value, "Moonlight");
  } finally { app.dispose(); }
});

test("failed queue requests retain the selected release and corrections and prevent duplicate submissions", async () => {
  const pending = deferred(), calls = [];
  const app = harness({ onQueue: async (...args) => { calls.push(args); return pending.promise; } });
  try {
    await app.search(); await app.choose();
    app.input("Film title").props.onChange({ target: { value: "Edited name" } }); await app.flush();
    await app.submit(); await app.submit(); assert.equal(calls.length, 1);
    pending.resolve(false); await app.flush(); await app.update({ requestError: "This release is already queued." });
    assert.equal(app.input("Film title").props.value, "Edited name");
    assert.equal(app.input("Year").props.value, "2024");
    const queueForm = nodes(app.state).filter((node) => node.type === "form").at(-1);
    assert.match(text(queueForm), /This release is already queued\./);
    assert.ok(app.button("Change release"));
  } finally { app.dispose(); }
});

test("a duplicate-release error survives refresh but clears as soon as the user starts another search", async () => {
  const pending = deferred(); let nextSearch = false, clears = 0, calls = 0;
  const app = harness({
    request: async () => nextSearch ? pending.promise : { results: releases },
    onQueue: async () => { calls++; return false; },
    initialProps: { onClearRequestError: () => { clears++; void app.update({ requestError: null }); } },
  });
  try {
    await app.search(); await app.choose(); await app.submit();
    await app.update({ requestError: "Challengers is already queued." });
    const clearsAfterFailure = clears;
    await app.update({ status: { ...status } });
    assert.match(text(app.state), /Challengers is already queued\./);
    assert.equal(clears, clearsAfterFailure, "background prop updates do not clear actionable errors");
    app.input("Search movie releases").props.onChange({ target: { value: "Past Lives 2023" } }); await app.flush();
    assert.equal(app.find((node) => node.props?.role === "alert"), undefined);
    assert.equal(clears, clearsAfterFailure + 1);
    assert.equal(app.input("Film title"), undefined);
    nextSearch = true;
    app.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} }); await app.flush();
    assert.match(text(app.state), /Finding releases/);
    assert.equal(app.find((node) => node.props?.role === "alert"), undefined);
    pending.resolve({ results: [] }); await app.flush();
    assert.equal(app.find((node) => node.props?.role === "alert"), undefined);
    assert.equal(calls, 1, "changing a search never queues a film");
  } finally { app.dispose(); }
});

test("metadata corrections and choosing another source clear the previous submission error", async () => {
  let clears = 0;
  const app = harness({ initialProps: { onClearRequestError: () => { clears++; void app.update({ requestError: null }); } } });
  try {
    await app.search(); await app.choose();
    for (const [label, value] of [["Film title", "Corrected title"], ["Year", "2023"], ["Edition", "Restored"]]) {
      await app.update({ requestError: "Check the film name." });
      const before = clears;
      app.input(label).props.onChange({ target: { value } }); await app.flush();
      assert.equal(app.find((node) => node.props?.role === "alert"), undefined, label);
      assert.equal(clears, before + 1, label);
    }
    await app.update({ requestError: "Try another source." });
    app.button("Magnet link").props.onClick(); await app.flush();
    assert.equal(app.find((node) => node.props?.role === "alert"), undefined);
    assert.equal(app.input("Film title").props.value, "Corrected title", "error recovery preserves deliberate metadata edits");
  } finally { app.dispose(); }
});

test("focus follows search, release selection, confirmation and source changes without scrolling", async () => {
  const app = harness();
  try {
    assert.match(app.focused.at(-1).placeholder, /Challengers/);
    await app.search(); assert.equal(app.focused.at(-1).type, "fieldset");
    await app.choose(); assert.equal(app.focused.at(-1).type, "input");
    app.button("Change release").props.onClick(); await app.flush(); assert.equal(app.focused.at(-1).type, "fieldset");
    app.button("Magnet link").props.onClick(); await app.flush(); assert.match(app.focused.at(-1).placeholder, /magnet/);
    assert.ok(app.focused.every((entry) => entry.options.preventScroll));
  } finally { app.dispose(); }
});
