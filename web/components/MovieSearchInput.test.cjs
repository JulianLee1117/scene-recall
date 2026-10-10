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
vm.runInNewContext(compile("../lib/movieSuggestions.ts"), { exports: helpers });
const mentionHelpers = {};
vm.runInNewContext(compile("../lib/movieMentions.ts"), { exports: mentionHelpers, require: () => helpers });
const compiled = compile("MovieSearchInput.tsx");
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
const film = (id, title) => ({ film_id: id, title, filename: `${title}.mkv`, status: "indexed" });
const films = [film("sunrise", "Before Sunrise (1995)"), film("rain", "Before Rain (1992)")];

// This harness checks state and event contracts. Native layout/selection paint
// and pointer focus ordering still require the browser smoke check.
function setup(initial = "", overrides = {}, geometry = null) {
  const hooks = [], accepted = [], forwarded = [];
  const observers = new Set();
  let cursor = 0, output, scheduled = false, disposed = false, effects = [];
  const native = {
    value: initial, selectionStart: initial.length, selectionEnd: initial.length, scrollLeft: 0, focus() {},
    clientWidth: geometry?.width ?? 0,
    get scrollWidth() { return Math.max(this.clientWidth, this.value.length); },
    setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; },
    addEventListener() {}, removeEventListener() {},
  };
  let popoverStyle = {};
  const popover = { get offsetHeight() { return Math.min(geometry?.menuHeight ?? 240, popoverStyle.maxHeight ?? Infinity); } };
  const list = {
    scrollTop: 0,
    get scrollHeight() { return (geometry?.menuHeight ?? 240) - 68; },
    get clientHeight() { return Math.max(0, popover.offsetHeight - 68); },
    querySelector() {
      const options = nodes(output).filter((node) => node.props?.role === "option");
      const index = options.findIndex((node) => node.props["aria-selected"]);
      return index < 0 ? null : { offsetTop: index * 43, offsetHeight: 43 };
    },
  };
  const schedule = () => { if (!scheduled && !disposed) { scheduled = true; queueMicrotask(render); } };
  const props = {
    inputRef: { current: native }, value: initial, films, mentions: [], suggestionsEnabled: true,
    placeholder: "Describe a scene, or @ a movie", describedBy: "search-help",
    onChange(value, caret) {
      const next = mentionHelpers.editMovieText({ text: props.value, mentions: props.mentions }, value, caret);
      props.value = next.text; props.mentions = next.mentions; schedule();
    },
    onKeyDown(event) { forwarded.push(event); },
    onMovieSelect(suggestion) {
      accepted.push(suggestion);
      const next = mentionHelpers.acceptMovieMention({ text: props.value, mentions: props.mentions }, suggestion).draft;
      props.value = next.text; props.mentions = next.mentions;
      schedule();
    }, ...overrides,
  };
  const react = {
    useId() { return `movie-${cursor++}`; },
    useRef(initial) { return hooks[cursor++] ??= { current: initial }; },
    useState(initial) {
      const i = cursor++; hooks[i] ??= { value: initial };
      return [hooks[i].value, (update) => {
        const value = typeof update === "function" ? update(hooks[i].value) : update;
        if (!Object.is(value, hooks[i].value)) { hooks[i].value = value; schedule(); }
      }];
    },
    useMemo(factory, deps) {
      const i = cursor++; if (!hooks[i] || !same(deps, hooks[i].deps)) hooks[i] = { value: factory(), deps };
      return hooks[i].value;
    },
    useLayoutEffect(effect, deps) {
      const i = cursor++;
      if (!hooks[i] || !same(deps, hooks[i].deps)) {
        const old = hooks[i]; hooks[i] = { deps, cleanup: old?.cleanup };
        effects.push(() => { hooks[i].cleanup?.(); hooks[i].cleanup = effect(); });
      }
    },
  };
  const exports = {};
  const jsx = (type, props) => {
    if (geometry && props.className === "movie-caret-measure") props.ref.current = { offsetWidth: props.children.length };
    if (geometry?.rect && props.className === "movie-search-input") props.ref.current = {
      getBoundingClientRect: () => geometry.rect,
      closest: () => ({ getBoundingClientRect: () => geometry.rect }),
    };
    if (geometry?.rect && props.className === "movie-autocomplete") { popoverStyle = props.style; props.ref.current = popover; }
    if (geometry?.rect && props.role === "listbox") props.ref.current = list;
    return { type, props };
  };
  vm.runInNewContext(compiled, { exports,
    document: { documentElement: { clientWidth: geometry?.viewportWidth ?? 800 } },
    window: { innerHeight: geometry?.viewportHeight ?? 600, addEventListener() {}, removeEventListener() {},
      visualViewport: geometry?.visualHeight ? { height: geometry.visualHeight, offsetTop: geometry.visualTop ?? 0, addEventListener() {}, removeEventListener() {} } : undefined },
    ResizeObserver: class {
    constructor(callback) { this.callback = callback; }
    observe() { observers.add(this.callback); }
    disconnect() { observers.delete(this.callback); }
  }, require(name) {
    if (name === "react") return react;
    if (name === "react/jsx-runtime") return { jsx, jsxs: jsx, Fragment: "fragment" };
    if (name === "@/lib/movieSuggestions") return helpers;
    if (name === "@/lib/movieMentions") return mentionHelpers;
    throw new Error(`Unexpected module ${name}`);
  } });
  function render() {
    if (disposed) return;
    scheduled = false; cursor = 0; native.value = props.value;
    output = exports.default(props);
    const run = effects; effects = []; run.forEach((effect) => effect());
  }
  const find = (predicate) => nodes(output).find(predicate);
  const input = () => find((node) => node.type === "input").props;
  const flush = async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); };
  render();
  return {
    accepted, forwarded, input, find, flush, props, list,
    options: () => nodes(output).filter((node) => node.props?.role === "option"),
    async focus() { input().onFocus(); await flush(); },
    async type(value, caret = value.length) {
      Object.assign(native, { value, selectionStart: caret, selectionEnd: caret });
      input().onChange({ target: native }); await flush();
    },
    async select(start, end = start) {
      Object.assign(native, { selectionStart: start, selectionEnd: end });
      input().onSelect({ currentTarget: native }); await flush();
    },
    async scroll(left) { native.scrollLeft = left; input().onScroll({ currentTarget: native }); await flush(); },
    async resize(width) { native.clientWidth = width; [...observers].forEach((callback) => callback()); await flush(); },
    async key(key, extra = {}) {
      const event = { key, currentTarget: native, nativeEvent: {}, preventDefault() { this.prevented = true; }, ...extra };
      input().onKeyDown(event); await flush(); return event;
    },
    async composition(active) {
      if (active) input().onCompositionStart();
      else input().onCompositionEnd({ currentTarget: native });
      await flush();
    },
    dispose() { disposed = true; hooks.forEach((hook) => hook?.cleanup?.()); },
  };
}

test("short viewports keep movie choices and keyboard selection inside a scrolling list", async () => {
  const app = setup("@Before", { films: [
    ...films, film("midnight", "Before Midnight (2013)"), film("sunset", "Before Sunset (2004)"),
  ] }, { width: 200, viewportHeight: 300, menuHeight: 240, rect: { left: 100, top: 140, bottom: 174, width: 200 } });
  try {
    await app.focus();
    const menu = app.find((node) => node.props?.className === "movie-autocomplete");
    assert.equal(menu.props.style.maxHeight, 120);
    assert.equal(menu.props.style.top + 140, 12, "menu opens above when that side has more room");
    await app.key("ArrowUp");
    assert.equal(app.list.scrollTop, 120, "the last keyboard choice is scrolled fully into view");
    assert.ok(app.find((node) => node.props?.className === "movie-autocomplete-footer"));
    await app.key("Enter");
    assert.equal(app.accepted.length, 1);
  } finally { app.dispose(); }
});

test("movie completion uses the visible viewport when a mobile keyboard reduces available space", async () => {
  const app = setup("@Before", {}, {
    width: 200, viewportHeight: 600, visualHeight: 280, menuHeight: 240,
    rect: { left: 30, top: 80, bottom: 114, width: 200 },
  });
  try {
    await app.focus();
    const menu = app.find((node) => node.props?.className === "movie-autocomplete");
    assert.equal(menu.props.style.top + 80, 122);
    assert.equal(menu.props.style.maxHeight, 146, "height ends 12px before the keyboard instead of the layout viewport");
    await app.resize(180);
    assert.equal(app.find((node) => node.props?.className === "movie-autocomplete").props.style.maxHeight, 146,
      "measuring an already clipped list does not change its chosen placement");
  } finally { app.dispose(); }
});

test("ordinary complete titles offer options without stealing Enter or Tab", async () => {
  const app = setup("Before Sunrise couple looking away");
  try {
    await app.focus();
    assert.equal(app.options().length, 1);
    assert.equal(app.input()["aria-activedescendant"], undefined);
    assert.equal(app.input()["aria-describedby"], "search-help");
    assert.equal((await app.key("Enter")).prevented, undefined);
    assert.equal((await app.key("Tab")).prevented, undefined);
    assert.equal(app.accepted.length, 0);
    assert.equal(app.forwarded.length, 2);
    assert.equal(app.props.value, "Before Sunrise couple looking away");
  } finally { app.dispose(); }
});

test("explicit @ chooses a visible first option and Enter or forward Tab accepts it once", async () => {
  for (const key of ["Enter", "Tab"]) {
    const app = setup("red @Before");
    try {
      await app.focus();
      const selected = app.options().filter((option) => option.props["aria-selected"]);
      assert.equal(selected.length, 1);
      assert.equal(app.input()["aria-activedescendant"], selected[0].props.id);
      assert.equal(app.input()["aria-controls"], app.find((node) => node.props?.role === "listbox").props.id);
      assert.ok(app.options().every((option) => option.props.tabIndex === -1));
      assert.equal((await app.key(key)).prevented, true);
      assert.equal(app.accepted.length, 1);
      assert.equal(app.props.value, `red @${app.accepted[0].title} `);
      assert.equal(app.props.mentions.length, 1);
      assert.equal(app.forwarded.length, 0);
      assert.equal(app.input()["aria-expanded"], false);
    } finally { app.dispose(); }
  }
});

test("arrows choose stable film identities and ordinary prose can explicitly select an option", async () => {
  const app = setup("@Before");
  try {
    await app.focus();
    const first = app.input()["aria-activedescendant"];
    await app.key("ArrowDown");
    assert.notEqual(app.input()["aria-activedescendant"], first);
    await app.key("ArrowUp");
    assert.equal(app.input()["aria-activedescendant"], first);
    await app.type("Before Sunrise");
    assert.equal(app.input()["aria-activedescendant"], undefined);
    await app.key("ArrowDown");
    await app.key("Enter");
    assert.equal(app.accepted[0].film.film_id, "sunrise");
  } finally { app.dispose(); }
});

test("Escape exits completion and leaves continued typing, caret movement and refocus as plain text", async () => {
  const app = setup("@Before");
  try {
    await app.focus();
    assert.equal((await app.key("Tab", { shiftKey: true })).prevented, undefined);
    assert.equal(app.accepted.length, 0);
    assert.equal((await app.key("Escape")).prevented, true);
    assert.equal(app.input()["aria-expanded"], false);
    assert.equal(app.find((node) => node.props?.className === "movie-keyword"), undefined);
    assert.equal(app.props.value, "Before");
    assert.equal((await app.key("Enter")).prevented, undefined);
    await app.type("Before S");
    assert.equal(app.input()["aria-expanded"], false);
    assert.equal(app.find((node) => node.props?.className === "movie-keyword"), undefined);
    await app.select(4);
    app.input().onBlur(); await app.flush();
    await app.focus();
    assert.equal(app.input()["aria-expanded"], false);
    assert.equal(app.find((node) => node.props?.className === "movie-keyword"), undefined);
    await app.type("Before S @Before");
    assert.equal(app.input()["aria-expanded"], true);
    assert.ok(app.find((node) => node.props?.className === "movie-keyword"));
  } finally { app.dispose(); }
});

test("clearing an escaped query resets completion, while inserting an email does not", async () => {
  const app = setup("@Before");
  try {
    await app.focus();
    await app.key("Escape");
    await app.type("Before mail@Before");
    assert.equal(app.input()["aria-expanded"], false);
    await app.type("");
    await app.type("Before Sunrise");
    assert.equal(app.input()["aria-expanded"], true);
    assert.equal(app.input()["aria-activedescendant"], undefined);
  } finally { app.dispose(); }
});

test("Escape cancels only the active @ and keeps the caret and surrounding words", async () => {
  const app = setup("red @Before, two people");
  try {
    await app.focus();
    await app.select("red @Before".length);
    await app.key("Escape");
    assert.equal(app.props.value, "red Before, two people");
    assert.equal(app.props.inputRef.current.selectionStart, "red Before".length);
    assert.equal(app.props.inputRef.current.selectionEnd, "red Before".length);
    assert.equal(app.accepted.length, 0);
    assert.equal(app.input()["aria-expanded"], false);
  } finally { app.dispose(); }
});

test("range selection and IME composition leave native editing alone", async () => {
  const app = setup("@Before");
  try {
    await app.focus();
    await app.select(0, 3);
    assert.equal(app.input()["aria-expanded"], false);
    assert.equal((await app.key("Enter")).prevented, undefined);
    await app.select(7);
    await app.composition(true);
    assert.equal(app.input()["aria-expanded"], false);
    const forwarded = app.forwarded.length;
    for (const key of ["Enter", "Tab", "ArrowDown", "Backspace"]) assert.equal((await app.key(key)).prevented, undefined);
    assert.equal(app.forwarded.length, forwarded);
    await app.composition(false);
    assert.equal(app.input()["aria-expanded"], true);
    await app.key("Enter", { nativeEvent: { isComposing: true } });
    assert.equal(app.accepted.length, 0);
  } finally { app.dispose(); }
});

test("Backspace at the input start is native and does not delete a later mention", async () => {
  const text = "description @Before Sunrise (1995)";
  const app = setup(text, { mentions: [{ filmId: "sunrise", text: "@Before Sunrise (1995)", start: 12, end: text.length }] });
  try {
    await app.focus();
    await app.select(0);
    const event = await app.key("Backspace");
    assert.equal(app.forwarded[0], event);
    assert.equal(event.currentTarget.selectionStart, 0);
    assert.equal(event.prevented, undefined);
    assert.equal(app.props.value, text);
    assert.equal(app.props.mentions.length, 1);
  } finally { app.dispose(); }
});

test("click acceptance at a mid-query caret preserves the following description and insertion point", async () => {
  const app = setup("red @Before, two people", {});
  try {
    await app.focus();
    await app.select("red @Before".length);
    const option = app.options().find((node) => node.props["aria-label"] === "Use Before Sunrise (1995) as a movie filter");
    assert.ok(option);
    const event = { preventDefault() { this.prevented = true; } };
    app.find((node) => node.props?.className === "movie-autocomplete").props.onPointerDown(event);
    assert.equal(event.prevented, true);
    option.props.onClick(); await app.flush();
    assert.equal(app.accepted[0].film.film_id, "sunrise");
    assert.equal(app.props.value, "red @Before Sunrise (1995), two people");
    assert.equal(app.props.inputRef.current.selectionStart, "red @Before Sunrise (1995)".length);
    assert.equal(app.props.inputRef.current.selectionEnd, "red @Before Sunrise (1995)".length);
    assert.equal(app.props.value.slice(app.props.inputRef.current.selectionStart), ", two people");
  } finally { app.dispose(); }
});

test("Backspace after and Delete before a confirmed mention remove the whole tag, with native range editing elsewhere", async () => {
  for (const key of ["Backspace", "Delete"]) {
    const text = "red @Before Sunrise (1995), two people";
    const mention = { filmId: "sunrise", text: "@Before Sunrise (1995)", start: 4, end: "red @Before Sunrise (1995)".length };
    const app = setup(text, { mentions: [mention] });
    try {
      await app.focus();
      assert.ok(app.find((node) => node.props?.className === "movie-keyword movie-keyword-confirmed"));
      assert.equal(app.input()["aria-expanded"], false);
      await app.select(key === "Backspace" ? mention.end : mention.start);
      assert.equal((await app.key(key, { ctrlKey: true })).prevented, undefined);
      await app.select(mention.start, mention.start + 3);
      assert.equal((await app.key(key)).prevented, undefined);
      await app.select(key === "Backspace" ? mention.end : mention.start);
      assert.equal((await app.key(key)).prevented, true);
      assert.equal(app.props.value, "red, two people");
      assert.equal(app.props.mentions.length, 0);
      assert.equal(app.props.inputRef.current.selectionStart, mention.start - 1);
    } finally { app.dispose(); }
  }
});

test("confirming an already spelled title changes scope and moves the caret even when visible text is identical", async () => {
  const text = "the scene in @Before Sunrise (1995) where they listen";
  const app = setup(text);
  try {
    await app.focus();
    await app.select("the scene in @Before".length);
    const option = app.options().find((node) => node.props["aria-label"] === "Use Before Sunrise (1995) as a movie filter");
    assert.ok(option);
    option.props.onClick(); await app.flush();
    assert.equal(app.props.value, text);
    assert.equal(app.props.mentions.length, 1);
    assert.equal(app.props.value.slice(app.props.inputRef.current.selectionStart), "where they listen");
  } finally { app.dispose(); }
});

test("long mention completion reveals its caret and resize keeps it visible without fighting manual scroll or range selection", async () => {
  const prefix = "the scene where two people quietly exchange glances in ";
  const app = setup(prefix + "@Before", {}, { width: 24 });
  try {
    await app.focus();
    await app.scroll(app.props.value.length - 24);
    const before = app.props.inputRef.current.scrollLeft;
    await app.key("ArrowDown");
    await app.key("Tab");
    const input = app.props.inputRef.current;
    assert.equal(app.props.value, prefix + "@Before Sunrise (1995) ");
    assert.ok(input.scrollLeft > before, "controlled replacement must reveal the longer confirmed title");
    assert.ok(input.selectionStart - input.scrollLeft <= input.clientWidth);
    assert.equal(app.find((node) => node.props?.className === "movie-input-text").props.style.transform, `translateX(${-input.scrollLeft}px)`);
    await app.resize(12);
    assert.ok(input.selectionStart - input.scrollLeft <= input.clientWidth);
    await app.scroll(0);
    assert.equal(input.scrollLeft, 0, "manual horizontal scrolling remains under native control");
    await app.select(0, 10);
    await app.resize(8);
    assert.equal(input.scrollLeft, 0, "resizing must not collapse or scroll a native range selection");
    assert.equal(input.selectionEnd, 10);
  } finally { app.dispose(); }
});

test("continuing after acceptance keeps the mention and duplicate explicit mentions can be confirmed", async () => {
  const app = setup("the scene in @Before");
  try {
    await app.focus();
    await app.key("ArrowDown"); // Before Sunrise follows Before Rain.
    await app.key("Enter");
    assert.equal(app.props.value, "the scene in @Before Sunrise (1995) ");
    await app.type(app.props.value + "where they listen");
    assert.equal(app.props.mentions.length, 1);
    assert.equal(app.input()["aria-expanded"], false);
    await app.type(app.props.value + " @Before Sunrise");
    assert.equal(app.options().length, 1);
    await app.key("Enter");
    assert.equal(app.props.mentions.length, 2);
    assert.deepEqual(Array.from(mentionHelpers.compileMovieDraft({ text: app.props.value, mentions: app.props.mentions }).filmIds), ["sunrise"]);
  } finally { app.dispose(); }
});
