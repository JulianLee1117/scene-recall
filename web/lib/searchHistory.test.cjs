const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const lib = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "searchHistory.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: lib });

const plain = (value) => JSON.parse(JSON.stringify(value));

/** A browser history whose Back and Forward fire popstate, and a page that shows screens. */
function browser(initialState = null) {
  const entries = [{ state: initialState }];
  let index = 0;
  const screens = [];
  const page = { tab: "search", shot: null, scrollY: 0, search: "home" };
  const history = {
    get state() { return entries[index].state; },
    pushState(data) { entries.splice(index + 1); entries.push({ state: data }); index += 1; },
    replaceState(data) { entries[index] = { state: data }; },
    back() { if (index > 0) { index -= 1; api.pop(); } },
    forward() { if (index < entries.length - 1) { index += 1; api.pop(); } },
  };
  const view = {
    show(screen) {
      screens.push(plain(screen));
      if (screen.search) page.search = screen.search.snapshot?.draft.text ?? "home";
      page.tab = screen.tab;
      page.shot = screen.shot?.unit_id ?? null;
      if (screen.scrollY !== undefined) page.scrollY = screen.scrollY;
    },
    tab: () => page.tab,
    scrollY: () => page.scrollY,
  };
  let count = 0;
  const api = lib.createSearchHistory(history, view, () => `s${++count}`);
  return { api, history, page, screens, last: () => screens.at(-1), length: () => entries.length };
}

const snapshot = (text, extra = {}) => ({ draft: { text, mentions: [] }, drafts: {}, filmFilters: {}, shotFilters: {}, image: null, ...extra });
const outcome = (unit) => ({ results: [{ unit_id: unit }], window: { hasMore: false, nextLimit: null }, evidence: {}, image: null });
/** The page searches: it leaves where it was, shows the results, and history records them. */
const search = (app, text, unit = text) => {
  app.api.leave();
  app.page.search = text;
  app.page.scrollY = 0;
  app.api.record(snapshot(text), outcome(unit));
};

test("each search is a page: Back shows the one before from memory, where it was left", () => {
  const app = browser();
  app.api.start("search");
  search(app, "rain");
  app.page.scrollY = 640;
  search(app, "snow");
  assert.equal(app.length(), 3, "home, rain, snow");
  app.page.scrollY = 90;
  app.history.back();
  assert.deepEqual(app.last(), { search: { snapshot: snapshot("rain"), outcome: outcome("rain") }, tab: "search", shot: null, scrollY: 640 });
  app.history.back();
  assert.equal(app.page.search, "home");
  app.history.forward();
  app.history.forward();
  assert.deepEqual(app.page, { tab: "search", shot: null, scrollY: 90, search: "snow" }, "Forward returns to where Back left it");
});

test("the same search again (a new order, a re-run) keeps its page, whatever order its fields were set in", () => {
  const app = browser();
  app.api.start("search");
  const clue = { kind: "text", facet: "look", text: "neon" };
  app.api.record(snapshot("rain", { drafts: { look: clue, scene: { ...clue, facet: "scene" } } }), outcome("a"));
  app.api.record(snapshot("rain", { drafts: { scene: { ...clue, facet: "scene" }, look: clue } }), outcome("b"));
  assert.equal(app.length(), 2);
  app.api.record(snapshot("rain", { shotFilters: { size: ["close"] } }), outcome("c"));
  assert.equal(app.length(), 3, "a new filter is a new page");
});

test("Back closes the player first, and a search started from the player returns to it", () => {
  const app = browser();
  app.api.start("search");
  search(app, "rain");
  app.page.scrollY = 300;
  app.api.openShot({ unit_id: "x" });
  assert.equal(app.page.shot, "x");
  // The browser steps back a moment after the player closes.
  const back = app.history.back;
  let steps = 0;
  app.history.back = () => { steps += 1; };
  app.api.closeShot();
  assert.equal(app.page.shot, null, "the player closes at once");
  app.api.closeShot();
  assert.equal(steps, 1, "a second close on the way does not step back again");
  app.history.back = back;
  back();
  assert.deepEqual(app.page, { tab: "search", shot: null, scrollY: 300, search: "rain" });

  app.api.openShot({ unit_id: "x" });
  app.api.openShot({ unit_id: "y" });
  assert.equal(app.length(), 3, "another scene while the player is open replaces it");
  app.page.shot = null; // Related closes the player as its search starts.
  search(app, "related to y");
  app.history.back();
  assert.deepEqual(app.page, { tab: "search", shot: "y", scrollY: 300, search: "rain" },
    "Back from a Related search: the search it came from, where it was, the scene open");
  app.history.back();
  assert.deepEqual(app.page, { tab: "search", shot: null, scrollY: 300, search: "rain" });
});

test("tabs are pages, and each search remembers where each tab was left", () => {
  const app = browser();
  app.api.start("search");
  search(app, "rain");
  app.page.scrollY = 500;
  app.api.switchTab("saved");
  assert.deepEqual(app.page, { tab: "saved", shot: null, scrollY: 0, search: "rain" }, "a tab opens at its top the first time");
  app.api.switchTab("saved");
  assert.equal(app.length(), 3, "the tab on screen is not a new page");
  app.page.scrollY = 220;
  app.api.switchTab("search");
  assert.equal(app.page.scrollY, 500, "Search returns to the results where they were left");
  app.api.switchTab("saved");
  assert.equal(app.page.scrollY, 220, "and Saved to where it was left");
  app.api.openShot({ unit_id: "s" });
  search(app, "related to s");
  app.history.back();
  assert.deepEqual(app.page, { tab: "saved", shot: "s", scrollY: 220, search: "rain" }, "the scene opens again on Saved");
  app.history.back();
  app.history.back();
  assert.deepEqual(app.page, { tab: "search", shot: null, scrollY: 500, search: "rain" }, "Back from Saved returns to the search just left");
});

test("a failed search is a page that is searched again when shown", () => {
  const app = browser();
  app.api.start("search");
  search(app, "rain");
  app.api.leave();
  app.api.record(snapshot("snow"));
  app.history.back();
  assert.equal(app.last().search.outcome.results[0].unit_id, "rain");
  app.history.forward();
  assert.deepEqual(app.last().search, { snapshot: snapshot("snow") }, "no results held: search it again");
});

test("after a reload a screen is shown again and searched again; Look deeper results are kept for the way back", () => {
  const app = browser();
  app.api.start("search");
  search(app, "rain");
  app.api.update({ results: [{ unit_id: "a" }, { unit_id: "b" }] });
  search(app, "snow");
  app.history.back();
  assert.equal(app.last().search.outcome.results.length, 2);

  const screens = [];
  const reloaded = lib.createSearchHistory(app.history, { show: (screen) => screens.push(plain(screen)), tab: () => "search", scrollY: () => 0 });
  reloaded.start("search");
  assert.deepEqual(screens, [{ search: { snapshot: snapshot("rain") }, tab: "search", shot: null, scrollY: 0 }]);
});

test("Home is a fresh page; Home again starts it afresh in place; a landing names its tab", () => {
  const app = browser();
  app.api.start("saved");
  assert.equal(app.history.state.sceneRecallV1.tab, "saved");
  app.api.home("search");
  assert.equal(app.length(), 2);
  app.page.scrollY = 400;
  app.api.home("search");
  assert.equal(app.length(), 2);
  assert.deepEqual(app.last(), { search: { snapshot: null }, tab: "search", shot: null, scrollY: 0 }, "the home page is reset, at its top");
  search(app, "rain");
  app.api.home("search");
  assert.equal(app.length(), 4);
  app.history.back();
  assert.equal(app.page.search, "rain");
});

test("without an entry of its own the player and tabs still show, and history is left alone", () => {
  const app = browser();
  app.api.openShot({ unit_id: "x" });
  assert.equal(app.page.shot, "x");
  app.api.closeShot();
  assert.equal(app.page.shot, null);
  app.api.switchTab("saved");
  assert.equal(app.page.tab, "saved");
  assert.equal(app.length(), 1);
});

test("every entry keeps the fields Next.js keeps beside it", () => {
  const next = { __NA: true, __PRIVATE_NEXTJS_INTERNALS_TREE: ["", {}] };
  const app = browser(next);
  app.api.start("search");
  search(app, "rain");
  app.api.openShot({ unit_id: "x" });
  assert.deepEqual(Object.keys(app.history.state).sort(), ["__NA", "__PRIVATE_NEXTJS_INTERNALS_TREE", "sceneRecallV1"]);
  app.history.back();
  app.history.back();
  assert.equal(app.history.state.__NA, true, "the first entry too");
});

test("stableKey ignores the order of fields", () => {
  assert.equal(lib.stableKey({ b: 1, a: { d: [2, { y: 1, x: 2 }], c: 3 } }), lib.stableKey({ a: { c: 3, d: [2, { x: 2, y: 1 }] }, b: 1 }));
});
