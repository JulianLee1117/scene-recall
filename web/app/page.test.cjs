const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
const compile = (file) => ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const recipe = {};
vm.runInNewContext(compile("../lib/searchRecipe.ts"), { exports: recipe });
const movieHelpers = {};
vm.runInNewContext(compile("../lib/movieSuggestions.ts"), { exports: movieHelpers });
const mentionHelpers = {};
vm.runInNewContext(compile("../lib/movieMentions.ts"), { exports: mentionHelpers, require: () => movieHelpers });
const formatHelpers = {};
vm.runInNewContext(compile("../lib/format.ts"), { exports: formatHelpers });
const shotFilters = {};
vm.runInNewContext(compile("../lib/shotFilters.ts"), { exports: shotFilters });
const filmFilters = {};
vm.runInNewContext(compile("../lib/filmFilters.ts"), {
  exports: filmFilters,
  require: (name) => ({ "./format": formatHelpers, "./movieSuggestions": movieHelpers })[name],
});
const viewPrefs = {};
vm.runInNewContext(compile("../lib/viewPrefs.ts"), { exports: viewPrefs, window: {} });
const compiled = compile("page.tsx");
const compiledMovieInput = compile("../components/MovieSearchInput.tsx");
const result = (id, main = false) => ({ unit_id: id, film_id: "film", caption: "A scene", matches: [{ clause_id: "composition", facet: "composition" }, ...(main ? [{ clause_id: "main", facet: "all" }] : [])] });
const response = (results, hasMore = false) => ({ results, has_more: hasMore, next_limit: hasMore ? 96 : null, source_evidence: [] });

test("movie suggestions preserve plain Enter and apply a hard scope only on explicit selection", async () => {
  for (const accept of [false, true]) {
    const app = harness();
    const input = () => app.find((node) => node.props?.["aria-label"] === "Describe a scene").props;
    try {
      input().onFocus();
      input().onChange({ target: { value: "Before Sunrise couple listening to music" } });
      await app.flush();
      assert.ok(app.find((node) => node.props?.className === "movie-autocomplete-option"));
      const key = { key: "Enter", preventDefault() { this.prevented = true; } };
      if (accept) {
        app.find((node) => node.props?.className === "movie-autocomplete-option").props.onClick();
      } else {
        input().onKeyDown(key);
        if (!key.prevented) app.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} });
      }
      await app.flush();
      await app.runTimers();
      assert.equal(app.requests.length, 1);
      const request = JSON.parse(app.requests[0].init.body);
      assert.equal(request.clauses[0].text, accept ? "couple listening to music" : "Before Sunrise couple listening to music");
      assert.deepEqual(request.film_ids, accept ? ["before-sunrise"] : undefined);
      assert.equal(Boolean(app.find((node) => node.props?.className === "movie-keyword movie-keyword-confirmed")), accept);
      assert.equal(input().value, accept ? "@Before Sunrise (1995) couple listening to music" : "Before Sunrise couple listening to music");
    } finally { app.dispose(); }
  }
});

test("title-only selection browses the confirmed film and removal clears that scope", async () => {
  const app = harness();
  const input = () => app.find((node) => node.props?.["aria-label"] === "Describe a scene").props;
  try {
    input().onFocus();
    input().onChange({ target: { value: "Before Sunrise" } });
    await app.flush();
    app.find((node) => node.props?.className === "movie-autocomplete-option").props.onClick();
    await app.runTimers();
    assert.equal(input().value, "@Before Sunrise (1995) ");
    assert.equal(app.requests[0].url, "/library/scenes?film_id=before-sunrise");
    assert.equal(app.requests[0].init.body, undefined);
    await app.resolve(0, response([result("browse")], true));
    app.grid().props.onRequestMore();
    await app.flush();
    assert.equal(app.requests[1].url, "/library/scenes?film_id=before-sunrise&limit=96");
    input().onChange({ target: { value: "", selectionStart: 0 } });
    await app.flush();
    assert.equal(app.requests[1].init.signal.aborted, true);
    assert.equal(app.grid().props.results.length, 0);
    assert.equal(app.find((node) => node.props?.["aria-label"] === "Search").props.disabled, true);
  } finally { app.dispose(); }
});

test("Enter and Tab accept an active @ completion without also submitting the literal tag", async () => {
  for (const key of ["Enter", "Tab"]) {
    const app = harness();
    const input = () => app.find((node) => node.props?.["aria-label"] === "Describe a scene").props;
    try {
      input().onChange({ target: { value: "red @Before", selectionStart: 11, selectionEnd: 11, scrollLeft: 0 } });
      await app.flush();
      const event = { key, preventDefault() { this.prevented = true; } };
      input().onKeyDown(event);
      if (!event.prevented && key === "Enter") app.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} });
      await app.flush();
      await app.runTimers();
      assert.equal(event.prevented, true);
      assert.equal(input().value, "red @Before Sunrise (1995) ");
      assert.equal(app.requests.length, 1);
      const request = JSON.parse(app.requests[0].init.body);
      assert.equal(request.clauses[0].text, "red");
      assert.deepEqual(request.film_ids, ["before-sunrise"]);
    } finally { app.dispose(); }
  }
});

test("Escape dismisses a movie suggestion without editing the query or selecting a film", async () => {
  const app = harness();
  const input = () => app.find((node) => node.props?.["aria-label"] === "Describe a scene").props;
  try {
    input().onFocus();
    input().onChange({ target: { value: "Before Sunrise" } });
    await app.flush();
    input().onKeyDown({ key: "Escape", preventDefault() {} });
    await app.flush();
    assert.equal(app.find((node) => node.props?.className === "movie-autocomplete-option"), undefined);
    assert.equal(input().value, "Before Sunrise");
    assert.equal(app.requests.length, 0);
    assert.equal(app.find((node) => node.props?.className === "search-film-chip"), undefined);
  } finally { app.dispose(); }
});

function harness() {
  const hooks = [], requests = [];
  const timers = new Map();
  let nextTimerId = 0;
  const shotFacets = [{ key: "dialogue", label: "Dialogue", values: [{ value: "none", label: "None", count: 3 }, { value: "spoken", label: "Spoken", count: 9 }] }];
  let films = [{ film_id: "before-sunrise", title: "Before Sunrise (1995)", filename: "Before Sunrise (1995).mkv", status: "indexed" }];
  let cursor = 0, output, scheduled = false, disposed = false, effects = [];
  const schedule = () => { if (!scheduled && !disposed) { scheduled = true; queueMicrotask(render); } };
  const react = {
    useId() { cursor++; return "search-status"; },
    useState(initial) {
      const i = cursor++; hooks[i] ??= { value: typeof initial === "function" ? initial() : initial };
      return [hooks[i].value, (update) => {
        const value = typeof update === "function" ? update(hooks[i].value) : update;
        if (!Object.is(value, hooks[i].value)) { hooks[i].value = value; schedule(); }
      }];
    },
    useRef(initial) { return hooks[cursor++] ??= { current: initial }; },
    useCallback(callback, deps) {
      const i = cursor++; if (!hooks[i] || !same(deps, hooks[i].deps)) hooks[i] = { callback, deps };
      return hooks[i].callback;
    },
    useMemo(factory, deps) {
      const i = cursor++; if (!hooks[i] || !same(deps, hooks[i].deps)) hooks[i] = { value: factory(), deps };
      return hooks[i].value;
    },
    useEffect(effect, deps) {
      const i = cursor++;
      if (!hooks[i] || !same(deps, hooks[i].deps)) {
        const old = hooks[i]; hooks[i] = { deps, cleanup: old?.cleanup };
        effects.push(() => { hooks[i].cleanup?.(); hooks[i].cleanup = effect(); });
      }
    },
  };
  react.useLayoutEffect = react.useEffect;
  const bookmarks = { bookmarks: [], bookmarkByUnit: new Map(), pendingUnitIds: new Set(), loading: false, error: null };
  const speech = { status: "idle", error: null, isSupported: false, cancel() {}, clearError() {} };
  const referenceSearch = { facet: null, query: "", results: [], close() {} };
  const exports = {};
  const movieInputExports = {};
  const jsx = (type, props) => typeof type === "function" ? type(props) : ({ type, props });
  const context = {
    exports, process: { env: {} }, AbortController, FormData, URLSearchParams, Element: class Element {},
    URL: { createObjectURL: () => "blob:reference", revokeObjectURL() {} },
    fetch(url, init) { return new Promise((resolve) => requests.push({ url, init, resolve })); },
    window: {
      clearTimeout(id) { timers.delete(id); },
      setTimeout(callback) { const id = ++nextTimerId; timers.set(id, callback); return id; },
      requestAnimationFrame: (callback) => callback(),
    },
    require(name) {
      if (name === "react") return react;
      if (name === "react/jsx-runtime") return { jsx, jsxs: jsx, Fragment: "fragment" };
      if (name === "next/dynamic") return { default: (loader) => {
        const source = String(loader);
        if (source.includes("@/components/LibraryView")) return "LibraryView";
        if (source.includes("@/features/info/InfoView")) return "InfoView";
        throw new Error(`Unexpected dynamic module: ${source}`);
      } };
      if (name === "@/lib/searchRecipe") return recipe;
      if (name === "@/lib/movieSuggestions") return movieHelpers;
      if (name === "@/lib/movieMentions") return mentionHelpers;
      if (name === "@/lib/filmFilters") return filmFilters;
      if (name === "@/lib/shotFilters") return shotFilters;
      if (name === "@/lib/viewPrefs") return viewPrefs;
      if (name === "@/lib/appClient") return { APP_CLIENT_HEADERS: { "X-Scene-Recall-Client": "app" } };
      if (name === "@/components/MovieSearchInput") return movieInputExports;
      if (name === "@/hooks/useSearchFilms") return { useSearchFilms: () => films };
      if (name === "@/hooks/useShotFacets") return { useShotFacets: () => shotFacets };
      if (name === "@/hooks/useGlide") return { useGlide() {} };
      if (name === "@/hooks/useMediaQuery") return { useMediaQuery: () => false };
      if (name === "@/hooks/useBookmarks") return { useBookmarks: () => bookmarks };
      if (name === "@/hooks/useSpeechRecognition") return { useSpeechRecognition: () => speech };
      if (name === "@/hooks/useFacetSourceSearch") return { useFacetSourceSearch: () => referenceSearch };
      if (name.startsWith("@/components/")) return { default: name.slice("@/components/".length) };
      throw new Error(`Unexpected module: ${name}`);
    },
  };
  vm.runInNewContext(compiledMovieInput, { ...context, exports: movieInputExports });
  vm.runInNewContext(compiled, context);
  function render() {
    if (disposed) return;
    scheduled = false; cursor = 0; output = exports.default();
    const run = effects; effects = []; run.forEach((effect) => effect());
  }
  const find = (predicate) => nodes(output).find(predicate);
  const flush = async () => { for (let i = 0; i < 25; i++) await Promise.resolve(); };
  render();
  return {
    // Like the real catalog hook, each refresh is a new array.
    async setFilms(next) { films = [...next]; schedule(); await flush(); },
    async runTimers() { const pending = [...timers.values()]; timers.clear(); pending.forEach((callback) => callback()); await flush(); },
    requests, flush, find,
    get state() { return output; },
    grid: () => find((node) => node.type === "ResultGrid"),
    note: () => find((node) => node.props?.className === "search-no-overlap"),
    async search(query = "women running", facet = "composition") {
      if (query) { find((node) => node.props?.["aria-label"] === "Describe a scene").props.onChange({ target: { value: query } }); await flush(); }
      find((node) => node.type === "MatchByRail").props.onSource(facet, { kind: "source", facet, source: { unit_id: "reference", frame_index: 0 } });
      await flush();
    },
    async resolve(index, body) { requests[index].resolve({ ok: true, json: async () => body }); await flush(); },
    dispose() { disposed = true; hooks.forEach((hook) => hook?.cleanup?.()); },
  };
}

test("partial titles stay quiet until @ requests completion and buttons retain film identity", async () => {
  const app = harness();
  const input = () => app.find((node) => node.props?.["aria-label"] === "Describe a scene").props;
  try {
    // Typing must work when browser autofocus happened before hydration.
    for (const query of ["the", "bef", "Before"]) {
      input().onChange({ target: { value: query } });
      await app.flush();
      assert.equal(app.find((node) => node.props?.className === "movie-autocomplete-option"), undefined);
    }
    input().onChange({ target: { value: "@Before" } });
    await app.flush();
    assert.ok(app.find((node) => node.props?.className === "movie-autocomplete-option"));
    await app.setFilms([
      { film_id: "rain", title: "Before Rain (1992)", filename: "Before Rain (1992).mkv", status: "indexed" },
      { film_id: "before-sunrise", title: "Before Sunrise (1995)", filename: "Before Sunrise (1995).mkv", status: "indexed" },
    ]);
    app.find((node) => node.props?.["aria-label"] === "Use Before Sunrise (1995) as a movie filter").props.onClick();
    await app.runTimers();
    assert.equal(input().value, "@Before Sunrise (1995) ");
    assert.equal(app.requests[0].url, "/library/scenes?film_id=before-sunrise");
  } finally { app.dispose(); }
});

test("removing film-only scope clears an error from its failed browse", async () => {
  const app = harness();
  const input = () => app.find((node) => node.props?.["aria-label"] === "Describe a scene").props;
  try {
    input().onFocus();
    input().onChange({ target: { value: "Before Sunrise" } });
    await app.flush();
    app.find((node) => node.props?.className === "movie-autocomplete-option").props.onClick();
    await app.runTimers();
    app.requests[0].resolve({ ok: false, status: 503, json: async () => ({ detail: "Library is being published" }) });
    await app.flush();
    assert.match(text(app.state), /Library is being published/);
    input().onChange({ target: { value: "", selectionStart: 0 } });
    await app.flush();
    assert.doesNotMatch(text(app.state), /Library is being published/);
  } finally { app.dispose(); }
});

test("inline boundary deletion removes only that mention, while selections and IME remain native", async () => {
  for (const scenario of [
    { key: "Backspace", start: 26, end: 26, removes: true },
    { key: "Delete", start: 4, end: 4, removes: true },
    { key: "Backspace", start: 0, end: 0, removes: false },
    { key: "Backspace", start: 10, end: 10, removes: false },
    { key: "Backspace", start: 2, end: 10, removes: false },
    { key: "Backspace", start: 26, end: 26, composing: true, removes: false },
    { key: "Backspace", start: 26, end: 26, ctrlKey: true, removes: false },
  ]) {
    const app = harness();
    const input = () => app.find((node) => node.props?.["aria-label"] === "Describe a scene").props;
    const scope = () => nodes(app.find((node) => node.type === "MatchByRail").props.filter).find((node) => node.type === "SearchFilter").props;
    try {
      input().onFocus();
      input().onChange({ target: { value: "red @Before" } });
      await app.flush();
      app.find((node) => node.props?.className === "movie-autocomplete-option").props.onClick();
      await app.flush();
      assert.equal(input().value, "red @Before Sunrise (1995) ");
      const event = {
        key: scenario.key, ctrlKey: scenario.ctrlKey,
        nativeEvent: { isComposing: scenario.composing },
        currentTarget: { selectionStart: scenario.start, selectionEnd: scenario.end },
        preventDefault() { this.prevented = true; },
      };
      input().onKeyDown(event);
      await app.flush();
      assert.equal(Boolean(event.prevented), scenario.removes);
      assert.deepEqual(Array.from(scope().mentionedFilmIds), scenario.removes ? [] : ["before-sunrise"]);
      assert.equal(input().value, scenario.removes ? "red " : "red @Before Sunrise (1995) ");
    } finally { app.dispose(); }
  }
});

test("Backspace after a film-only mention clears scope and cancels its browse", async () => {
  const app = harness();
  const input = () => app.find((node) => node.props?.["aria-label"] === "Describe a scene").props;
  try {
    input().onFocus();
    input().onChange({ target: { value: "@Before Sunrise" } });
    await app.flush();
    app.find((node) => node.props?.className === "movie-autocomplete-option").props.onClick();
    await app.runTimers();
    assert.equal(app.requests[0].url, "/library/scenes?film_id=before-sunrise");
    input().onKeyDown({ key: "Backspace", currentTarget: { selectionStart: 22, selectionEnd: 22 }, preventDefault() {} });
    await app.flush();
    assert.equal(app.requests[0].init.signal.aborted, true);
    assert.equal(app.find((node) => node.props?.className === "search-film-chip"), undefined);
    assert.equal(app.find((node) => node.props?.["aria-label"] === "Search").props.disabled, true);
  } finally { app.dispose(); }
});

test("middle mentions preserve visible prose and scope the search", async () => {
  const app = harness();
  const input = () => app.find((node) => node.props?.["aria-label"] === "Describe a scene").props;
  const scope = () => nodes(app.find((node) => node.type === "MatchByRail").props.filter).find((node) => node.type === "SearchFilter").props;
  try {
    input().onChange({ target: { value: "the scene in @Before where they listen", selectionStart: 20, selectionEnd: 20 } });
    await app.flush();
    input().onKeyDown({ key: "Enter", preventDefault() {} });
    await app.runTimers();
    assert.equal(input().value, "the scene in @Before Sunrise (1995) where they listen");
    assert.equal(JSON.parse(app.requests[0].init.body).clauses[0].text, "the scene where they listen");
    assert.deepEqual(JSON.parse(app.requests[0].init.body).film_ids, ["before-sunrise"]);
    assert.deepEqual(Array.from(scope().mentionedFilmIds), ["before-sunrise"]);
  } finally { app.dispose(); }
});

test("editing an accepted title cancels stale scoped results and searches remaining literal text", async () => {
  const app = harness();
  const input = () => app.find((node) => node.props?.["aria-label"] === "Describe a scene").props;
  try {
    input().onChange({ target: { value: "red @Before" } });
    await app.flush();
    input().onKeyDown({ key: "Enter", preventDefault() {} });
    await app.runTimers();
    const edited = input().value.replace("Sunrise", "Sunset");
    input().onChange({ target: { value: edited, selectionStart: edited.indexOf("Sunset") + 6 } });
    await app.runTimers();
    assert.equal(app.requests[0].init.signal.aborted, true);
    assert.equal(JSON.parse(app.requests[1].init.body).film_ids, undefined);
    assert.equal(JSON.parse(app.requests[1].init.body).clauses[0].text, edited.trim());
    await app.resolve(0, response([result("stale")]));
    assert.equal(app.grid().props.results.length, 0);
  } finally { app.dispose(); }
});

test("IME title edits clear stale scope immediately but search only after composition completes", async () => {
  const app = harness();
  const input = () => app.find((node) => node.props?.["aria-label"] === "Describe a scene").props;
  try {
    input().onChange({ target: { value: "red @Before" } });
    await app.flush();
    input().onKeyDown({ key: "Enter", preventDefault() {} });
    await app.runTimers();
    input().onCompositionStart();
    await app.flush();
    const edited = input().value.replace("Sunrise", "朝");
    const native = { value: edited, selectionStart: edited.indexOf("朝") + 1, selectionEnd: edited.indexOf("朝") + 1, scrollLeft: 0 };
    input().onChange({ target: native, nativeEvent: { isComposing: true } });
    await app.runTimers();
    assert.equal(app.requests[0].init.signal.aborted, true);
    assert.equal(app.requests.length, 1, "partial composition must not dispatch a new search");
    input().onCompositionEnd({ currentTarget: native });
    await app.flush();
    input().onChange({ target: native, nativeEvent: { isComposing: false } });
    await app.runTimers();
    assert.equal(app.requests.length, 2);
    assert.equal(JSON.parse(app.requests[1].init.body).film_ids, undefined);
    assert.equal(JSON.parse(app.requests[1].init.body).clauses[0].text, edited.trim());
  } finally { app.dispose(); }
});

test("Framing results without main-description overlap remain visible and can load more", async () => {
  const app = harness();
  try {
    await app.search();
    const first = [result("first")];
    await app.resolve(0, response(first, true));
    assert.equal(app.grid().props.results, first, "the backend result stream must never become a client-side empty state");
    assert.equal(app.grid().props.hasMore, true);
    assert.match(text(app.note()), /this result set/);
    assert.doesNotMatch(text(app.state), /No results found|No matches for both/);
    const streamKey = app.grid().props.streamKey;

    app.grid().props.onRequestMore(); await app.flush();
    assert.equal(JSON.parse(app.requests[1].init.body).limit, 96);
    assert.equal(app.grid().props.results, first, "existing results stay visible while the search deepens");
    assert.equal(app.grid().props.revealDisabled, true);
    const deeper = [...first, result("overlap", true)];
    await app.resolve(1, response(deeper));
    assert.equal(app.grid().props.results, deeper);
    assert.equal(app.grid().props.streamKey, streamKey);
    assert.equal(app.grid().props.hasMore, false);
    assert.equal(app.note(), undefined, "the advisory disappears when any current result has main-description evidence");
  } finally { app.dispose(); }
});

test("an empty backend result set retains the real no-results state without an overlap advisory", async () => {
  const app = harness();
  try {
    await app.search(); await app.resolve(0, response([]));
    assert.equal(app.grid().props.results.length, 0);
    assert.equal(app.grid().props.hasMore, false);
    assert.equal(app.note(), undefined);
    assert.match(text(app.state), /No results found/);
  } finally { app.dispose(); }
});

test("the overlap advisory requires both main text and a Framing reference", async () => {
  for (const scenario of [{ query: "", facet: "composition" }, { query: "women running", facet: "look" }]) {
    const app = harness();
    try {
      await app.search(scenario.query, scenario.facet);
      const results = [result("visual")];
      await app.resolve(0, response(results, true));
      assert.equal(app.grid().props.results, results);
      assert.equal(app.grid().props.hasMore, true);
      assert.equal(app.note(), undefined);
    } finally { app.dispose(); }
  }
});

test("clearing the last search input keeps results and layout until an explicit Home reset", async () => {
  for (const kind of ["main text", "category text", "category reference", "uploaded image", "empty category text"]) {
    const app = harness();
    const rail = () => app.find((node) => node.type === "MatchByRail").props;
    const input = () => app.find((node) => node.props?.["aria-label"] === "Describe a scene").props;
    try {
      if (kind === "main text") {
        input().onChange({ target: { value: "red" } }); await app.flush();
        app.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} });
      } else if (kind === "uploaded image") {
        rail().onImageFile(new Blob(["image"], { type: "image/png" }), "look");
      } else if (kind === "category reference") {
        await app.search("", "look");
      } else {
        rail().onCommitText("look", "red");
      }
      await app.flush();
      const results = [result(kind)];
      await app.resolve(0, response(results, true));
      const streamKey = app.grid().props.streamKey;

      if (kind === "main text") input().onChange({ target: { value: "" } });
      else if (kind === "uploaded image") rail().onRemoveImage();
      else if (kind === "empty category text") rail().onCommitText("look", "");
      else rail().onRemove("look");
      await app.flush();

      assert.equal(app.grid().props.results, results, kind);
      assert.equal(app.grid().props.streamKey, streamKey, "clearing must not collapse already revealed results");
      assert.equal(app.find((node) => node.props?.className === "search-hero")?.type, "div", kind);
      assert.equal(rail().clauseCount, 0);
      assert.equal(app.requests.length, 1, "an empty draft must not submit a new search");
      assert.equal(app.grid().props.hasMore, false, "old pagination must not run against the empty draft");
      assert.equal(app.find((node) => node.props?.["aria-label"] === "Search").props.disabled, true);

      app.find((node) => node.props?.["aria-label"] === "Return to Scene Recall home").props.onClick();
      await app.flush();
      assert.equal(app.grid().props.results.length, 0);
      assert.ok(app.find((node) => node.props?.className === "search-hero is-home"));
    } finally { app.dispose(); }
  }
});

test("clearing during a search keeps previous scenes, ignores the canceled response and allows a new search", async () => {
  const app = harness();
  const input = () => app.find((node) => node.props?.["aria-label"] === "Describe a scene").props;
  const submit = () => app.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} });
  try {
    input().onChange({ target: { value: "red" } }); await app.flush();
    submit(); await app.flush();
    const first = [result("first")];
    await app.resolve(0, response(first));
    input().onChange({ target: { value: "blue" } }); await app.flush();
    submit(); await app.flush();
    input().onChange({ target: { value: "" } }); await app.flush();
    assert.equal(app.requests[1].init.signal.aborted, true);
    assert.equal(app.grid().props.revealDisabled, false);
    await app.resolve(1, response([result("late")]));
    assert.equal(app.grid().props.results, first);
    assert.ok(app.find((node) => node.props?.className === "search-hero"));

    input().onChange({ target: { value: "green" } }); await app.flush();
    submit(); await app.flush();
    assert.equal(JSON.parse(app.requests[2].init.body).clauses[0].text, "green");
    const next = [result("next")];
    await app.resolve(2, response(next));
    assert.equal(app.grid().props.results, next);
  } finally { app.dispose(); }
});

test("Info navigation mounts its own view without search UI or a search request", async () => {
  const app = harness();
  const tab = (label) => app.find((node) => node.type === "button" && text(node) === label);
  try {
    tab("Info").props.onClick(); await app.flush();
    assert.ok(app.find((node) => node.type === "InfoView"));
    assert.equal(tab("Info").props["aria-current"], "page");
    assert.equal(app.find((node) => node.type === "LibraryView"), undefined);
    assert.equal(app.find((node) => node.props?.["aria-label"] === "Describe a scene"), undefined);
    assert.equal(app.grid(), undefined);
    assert.equal(app.requests.length, 0);

    const transfer = { types: ["Files"], files: { item: () => new Blob(["image"], { type: "image/png" }) } };
    const event = { dataTransfer: transfer, target: {}, preventDefault() {} };
    app.state.props.onDragOver(event);
    app.state.props.onDrop(event); await app.flush();
    assert.equal(app.requests.length, 0, "a file drop on Info must not start image search");
    assert.ok(app.find((node) => node.type === "InfoView"));

    tab("Films").props.onClick(); await app.flush();
    assert.ok(app.find((node) => node.type === "LibraryView"));
    assert.equal(app.find((node) => node.type === "InfoView"), undefined);
    tab("Search").props.onClick(); await app.flush();
    assert.ok(app.find((node) => node.props?.["aria-label"] === "Describe a scene"));
    assert.equal(app.find((node) => node.type === "InfoView"), undefined);
    assert.equal(app.requests.length, 0);
  } finally { app.dispose(); }
});

test("Related starts a fresh search from the chosen scene and marks the request as the app's own", async () => {
  const app = harness();
  const input = () => app.find((node) => node.props?.["aria-label"] === "Describe a scene").props;
  try {
    await app.search("women running", "composition");
    assert.equal(app.requests[0].init.headers["X-Scene-Recall-Client"], "app", "only app requests reach the taste log");
    const scene = { ...result("chosen"), keyframe_index: 4, matched_frame_timestamp: 12 };
    await app.resolve(0, response([scene]));
    app.grid().props.onUseInSearch(scene, "look"); await app.flush();
    const body = JSON.parse(app.requests[1].init.body);
    assert.equal(input().value, "", "typed text clears");
    assert.deepEqual(body.clauses.map((clause) => clause.facet), ["look"], "earlier references clear; only the chosen scene remains");
    assert.equal(body.clauses[0].source.unit_id, "chosen");
    assert.equal(app.requests[1].init.headers["X-Scene-Recall-Client"], "app");
  } finally { app.dispose(); }
});

test("film filters narrow the search to matching movies and say when none match", async () => {
  const app = harness();
  const input = () => app.find((node) => node.props?.["aria-label"] === "Describe a scene").props;
  const filter = () => nodes(app.find((node) => node.type === "MatchByRail").props.filter).find((node) => node.type === "SearchFilter").props;
  const body = (index) => JSON.parse(app.requests[index].init.body);
  try {
    await app.setFilms([
      { film_id: "heat", title: "Heat (1995)", filename: "Heat (1995).mkv", status: "indexed", year: 1995, genres: ["Crime", "Drama"], directors: ["Michael Mann"] },
      { film_id: "elf", title: "Elf (2003)", filename: "Elf (2003).mkv", status: "indexed", year: 2003, genres: ["Comedy"], directors: ["Jon Favreau"] },
    ]);
    input().onChange({ target: { value: "rain at night" } });
    await app.flush();
    filter().onApply({ film: { genre: ["Crime"] }, shot: {} });
    await app.runTimers();
    assert.deepEqual(body(0).film_ids, ["heat"]);
    assert.equal(body(0).clauses[0].text, "rain at night");
    // Active filters show in the refinements row; a chip reopens the panel.
    // Active filters show beside the result count; a chip reopens the menu at its section.
    await app.resolve(0, response([result("heat")]));
    const summary = () => nodes(app.grid().props.status).find((node) => node.type === "ActiveFilters");
    assert.deepEqual(JSON.parse(JSON.stringify(summary().props.filters)), { film: { genre: ["Crime"] }, shot: {} });
    assert.equal(filter().view, null);
    summary().props.onEdit("genre");
    await app.flush();
    assert.equal(filter().view, "genre");

    // Crime and the 2000s share no movie: nothing is searched, and the page says why.
    filter().onApply({ film: { genre: ["Crime"], era: ["2000s"] }, shot: {} });
    await app.runTimers();
    assert.equal(app.requests.length, 1);
    const none = app.find((node) => node.props?.className === "search-filter-none");
    assert.match(text(none), /No movies match these filters/);

    nodes(none).find((node) => node.type === "button").props.onClick();
    await app.runTimers();
    assert.equal(app.requests.length, 2);
    assert.equal(body(1).film_ids, undefined);
    assert.equal(app.find((node) => node.props?.className === "search-filter-none"), undefined);
  } finally { app.dispose(); }
});

test("the View menu reorders the current search, shows a non-default order as a chip, and sizes the grid", async () => {
  const app = harness();
  const rail = () => app.find((node) => node.type === "MatchByRail").props;
  const chip = () => nodes(app.grid().props.status).find((node) => node.type === "ActiveChip");
  try {
    app.find((node) => node.props?.["aria-label"] === "Describe a scene").props.onChange({ target: { value: "rain at night" } });
    await app.flush();
    app.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} });
    await app.flush();
    assert.equal(rail().controls.type, "ViewMenu");
    assert.equal(chip(), undefined);

    rail().controls.props.onChange({ order: "famous" });
    await app.runTimers();
    assert.equal(JSON.parse(app.requests.at(-1).init.body).preset, "famous");
    assert.equal(chip().props.value, "Famous");

    rail().controls.props.onChange({ size: "large" });
    await app.flush();
    assert.equal(app.grid().props.size, "large");
    const searches = app.requests.length;
    chip().props.onClear();
    await app.runTimers();
    assert.equal(app.requests.length, searches + 1, "returning to Balanced reranks once");
    assert.equal(JSON.parse(app.requests.at(-1).init.body).preset, undefined);
    assert.equal(chip(), undefined);
  } finally { app.dispose(); }
});

test("quick changes run one search: order switching settles on the last choice, and an unchanged apply runs none", async () => {
  const app = harness();
  const rail = () => app.find((node) => node.type === "MatchByRail").props;
  const filter = () => nodes(rail().filter).find((node) => node.type === "SearchFilter").props;
  const body = () => JSON.parse(app.requests.at(-1).init.body);
  try {
    await app.setFilms([
      { film_id: "heat", title: "Heat (1995)", filename: "Heat (1995).mkv", status: "indexed", year: 1995, genres: ["Crime"], directors: ["Michael Mann"] },
      { film_id: "elf", title: "Elf (2003)", filename: "Elf (2003).mkv", status: "indexed", year: 2003, genres: ["Comedy"], directors: ["Jon Favreau"] },
    ]);
    app.find((node) => node.props?.["aria-label"] === "Describe a scene").props.onChange({ target: { value: "rain" } });
    await app.flush();
    app.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} });
    await app.flush();
    const searches = app.requests.length;

    for (const order of ["famous", "gems", "balanced", "gems"]) {
      rail().controls.props.onChange({ order });
      await app.flush();
    }
    await app.runTimers();
    assert.equal(app.requests.length, searches + 1, "one search for four quick order changes");
    assert.equal(body().preset, "gems");

    filter().onApply({ film: {}, shot: {} });
    await app.runTimers();
    assert.equal(app.requests.length, searches + 1, "applying no filters again runs no search");

    filter().onApply({ film: { era: ["1990s"] }, shot: {} });
    filter().onApply({ film: { era: ["1990s"], genre: ["Crime"] }, shot: {} });
    await app.runTimers();
    assert.equal(app.requests.length, searches + 2, "back-to-back applies settle into one search");
    assert.deepEqual(body().film_ids, ["heat"]);
  } finally { app.dispose(); }
});

test("movies picked in Filter are a filter: the search text is untouched and clearing removes them", async () => {
  const app = harness();
  const input = () => app.find((node) => node.props?.["aria-label"] === "Describe a scene").props;
  const filter = () => nodes(app.find((node) => node.type === "MatchByRail").props.filter).find((node) => node.type === "SearchFilter").props;
  const body = () => JSON.parse(app.requests.at(-1).init.body);
  try {
    await app.setFilms([
      { film_id: "heat", title: "Heat (1995)", filename: "Heat (1995).mkv", status: "indexed", year: 1995, genres: ["Crime"], directors: ["Michael Mann"] },
      { film_id: "elf", title: "Elf (2003)", filename: "Elf (2003).mkv", status: "indexed", year: 2003, genres: ["Comedy"], directors: ["Jon Favreau"] },
    ]);
    input().onChange({ target: { value: "rain" } });
    await app.flush();
    filter().onApply({ film: { movie: ["Heat (1995)"] }, shot: {} });
    await app.runTimers();
    assert.deepEqual(body().film_ids, ["heat"]);
    assert.equal(input().value, "rain", "no @mentions are written into the search");
    await app.resolve(0, response([result("heat")]));
    assert.deepEqual(JSON.parse(JSON.stringify(nodes(app.grid().props.status).find((node) => node.type === "ActiveFilters").props.filters)),
      { film: { movie: ["Heat (1995)"] }, shot: {} });

    filter().onApply({ film: {}, shot: {} });
    await app.runTimers();
    assert.equal(body().film_ids, undefined);
    assert.equal(input().value, "rain");
  } finally { app.dispose(); }
});

test("shot filters travel with the search and with browsing a movie, and clear like film filters", async () => {
  const app = harness();
  const input = () => app.find((node) => node.props?.["aria-label"] === "Describe a scene").props;
  const filter = () => nodes(app.find((node) => node.type === "MatchByRail").props.filter).find((node) => node.type === "SearchFilter").props;
  try {
    input().onChange({ target: { value: "rain" } });
    await app.flush();
    filter().onApply({ film: {}, shot: { dialogue: ["none"] } });
    await app.runTimers();
    assert.deepEqual(JSON.parse(app.requests.at(-1).init.body).shot_filters, { dialogue: ["none"] });
    assert.deepEqual(filter().shotFacets.map((facet) => facet.key), ["dialogue"]);

    // Browsing a named movie with no description keeps the shot filters.
    input().onFocus();
    input().onChange({ target: { value: "@Before" } });
    await app.flush();
    app.find((node) => node.props?.className === "movie-autocomplete-option").props.onClick();
    await app.runTimers();
    assert.match(app.requests.at(-1).url, /\/library\/scenes\?film_id=before-sunrise&shot=dialogue%3Anone/);

    filter().onApply({ film: {}, shot: {} });
    await app.runTimers();
    assert.doesNotMatch(app.requests.at(-1).url, /shot=/);
  } finally { app.dispose(); }
});

test("home keeps Refine and Filter alone under a placeholder that names the ways in", async () => {
  const app = harness();
  const rail = () => app.find((node) => node.type === "MatchByRail").props;
  const input = () => app.find((node) => node.props?.["aria-label"] === "Describe a scene").props;
  try {
    assert.equal(rail().controls, null);
    assert.equal(input().placeholder, "Describe a scene, quote a line, or @ a movie…");
    input().onChange({ target: { value: "rain at night" } });
    await app.flush();
    app.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} });
    await app.flush();
    assert.equal(rail().controls.type, "ViewMenu");
  } finally { app.dispose(); }
});
