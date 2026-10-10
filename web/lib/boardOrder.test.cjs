const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const compile = (file) => ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const format = {};
vm.runInNewContext(compile("format.ts"), { exports: format });
const look = {};
vm.runInNewContext(compile("sceneLook.ts"), { exports: look });
const board = {};
vm.runInNewContext(compile("boardOrder.ts"), {
  exports: board,
  require(name) {
    if (name === "./format") return format;
    if (name === "./sceneLook") return look;
    throw new Error(name);
  },
});

const plain = (value) => JSON.parse(JSON.stringify(value));
const record = (id, { film = "moonlight", title = "Moonlight (2016)", savedDay = 1, start = 0, position = null } = {}) => ({
  bookmark_id: id, film_id: film, film_title: title, source_unit_id: `u-${id}`, evidence_timestamp: start,
  created_at: `2026-10-${String(savedDay).padStart(2, "0")}T12:00:00Z`, position, availability: "indexed",
  scene: { unit_id: `u-${id}`, film_id: film, t_start: start, t_end: start + 2 },
});
const ids = (items) => plain(items).map((item) => item.bookmark_id);

test("moveItem places an item at an insertion index and leaves a no-op list untouched", () => {
  const list = ["a", "b", "c", "d"];
  assert.deepEqual(plain(board.moveItem(list, 0, 3)), ["b", "c", "a", "d"], "forward: the index counts the item's own old place");
  assert.deepEqual(plain(board.moveItem(list, 3, 0)), ["d", "a", "b", "c"]);
  assert.deepEqual(plain(board.moveItem(list, 1, 4)), ["a", "c", "d", "b"], "to the end");
  assert.equal(board.moveItem(list, 2, 2), list, "dropping on itself changes nothing");
  assert.equal(board.moveItem(list, 2, 3), list, "nor dropping just after itself");
  assert.equal(board.moveItem(list, 9, 0), list, "an unknown item is ignored");
  assert.deepEqual(list, ["a", "b", "c", "d"], "the list itself is never changed");
});

test("Your order shows the board as it is; Newest is a sequence that moves nothing", () => {
  const bookmarks = [
    record("late", { savedDay: 3, start: 40 }),
    record("early", { savedDay: 1, start: 10, film: "heat", title: "Heat (1995)" }),
    record("mid", { savedDay: 2, start: 5 }),
  ];
  assert.equal(board.arrangeBoard(bookmarks, "yours"), bookmarks, "the board's own order is the list, untouched");
  assert.deepEqual(ids(board.arrangeBoard(bookmarks, "newest")), ["late", "mid", "early"]);
  assert.deepEqual(ids(bookmarks), ["late", "early", "mid"], "views never change the board");
});

test("By colour is a gradient: around the wheel, light before dark, then the greys; unmeasured scenes follow", () => {
  const looks = {
    "u-blue": { hue: 230, chroma: 0.3, lightness: 0.3 },
    "u-red": { hue: 5, chroma: 0.5, lightness: 0.4 },
    "u-red-light": { hue: 8, chroma: 0.4, lightness: 0.7 },
    "u-amber": { hue: 40, chroma: 0.4, lightness: 0.5 },
    "u-dark-grey": { hue: 0, chroma: 0.01, lightness: 0.2 },
    "u-light-grey": { hue: 0, chroma: 0.02, lightness: 0.8 },
  };
  const bookmarks = ["blue", "new-1", "dark-grey", "red", "light-grey", "amber", "new-2", "red-light"].map((id) => record(id));
  const arranged = board.arrangeBoard(bookmarks, "colour", { lookOf: (unitId) => looks[unitId] });
  assert.deepEqual(ids(arranged), ["red-light", "red", "amber", "blue", "light-grey", "dark-grey", "new-1", "new-2"]);
  assert.deepEqual(ids(board.arrangeBoard(bookmarks, "colour")), ids(bookmarks), "nothing measured yet: the board as it is");
});

test("By era runs through the years, films by name within a year and their scenes in story order", () => {
  const bookmarks = [
    record("rublev-late", { film: "andrei-rublev", title: "Andrei Rublev (1966)", start: 90 }),
    record("heat", { film: "heat", title: "Heat (1995)" }),
    record("unknown", { film: "mystery", title: "Mystery" }),
    record("rublev-early", { film: "andrei-rublev", title: "Andrei Rublev (1966)", start: 10 }),
    record("persona", { film: "persona-1966", title: "Persona [Criterion]", start: 5 }),
    record("drive", { film: "drive-my-car", title: "Drive My Car (2021)" }),
  ];
  assert.deepEqual(ids(board.arrangeBoard(bookmarks, "era")), ["rublev-early", "rublev-late", "persona", "heat", "drive", "unknown"]);
  assert.equal(board.filmYearOf(record("x", { title: "Heat (1995)" })), 1995);
  assert.equal(board.filmYearOf(record("x", { film: "persona-1966", title: "Persona" })), 1966, "the id's year when the title has none");
  assert.equal(board.filmYearOf(record("x", { film: "mystery", title: "Mystery" })), null);
});

test("Shuffle deals the same board for the same seed and a different one for a new seed", () => {
  const bookmarks = Array.from({ length: 12 }, (_, i) => record(`s${i}`));
  const first = ids(board.arrangeBoard(bookmarks, "shuffle", { seed: 7 }));
  assert.deepEqual(ids(board.arrangeBoard(bookmarks, "shuffle", { seed: 7 })), first, "a deal is stable");
  assert.deepEqual([...first].sort(), ids(bookmarks).sort(), "every scene is dealt once");
  assert.notDeepEqual(ids(board.arrangeBoard(bookmarks, "shuffle", { seed: 8 })), first, "a new seed is a new deal");
  assert.notDeepEqual(first, ids(bookmarks), "and it is not the board's own order");
});

test("hasUserOrder reads whether anything has been placed", () => {
  assert.equal(board.hasUserOrder([record("a"), record("b")]), false);
  assert.equal(board.hasUserOrder([record("a"), record("b", { position: 0 })]), true);
});
