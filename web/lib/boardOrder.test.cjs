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
const board = {};
vm.runInNewContext(compile("boardOrder.ts"), { exports: board, require(name) { if (name === "./format") return format; throw new Error(name); } });

const plain = (value) => JSON.parse(JSON.stringify(value));
const record = (id, { film = "moonlight", title = "Moonlight", savedDay = 1, start = 0, position = null } = {}) => ({
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

test("Your order shows the board as it is; Newest and By film are views that reorder nothing", () => {
  const bookmarks = [
    record("late", { savedDay: 3, start: 40 }),
    record("early", { savedDay: 1, start: 10, film: "heat", title: "Heat" }),
    record("mid", { savedDay: 2, start: 5 }),
  ];
  const yours = board.arrangeBoard(bookmarks, "yours");
  assert.equal(yours.length, 1); assert.equal(yours[0].title, null);
  assert.equal(yours[0].items, bookmarks, "the board's own order is the list, untouched");

  const newest = board.arrangeBoard(bookmarks, "newest");
  assert.deepEqual(ids(newest[0].items), ["late", "mid", "early"]);

  const byFilm = board.arrangeBoard(bookmarks, "film");
  assert.deepEqual(plain(byFilm).map((group) => [group.title, ids(group.items)]), [
    [format.displayTitle("Heat"), ["early"]],
    [format.displayTitle("Moonlight"), ["mid", "late"]],
  ], "films by name, each film's scenes in story order");
  assert.deepEqual(ids(bookmarks), ["late", "early", "mid"], "views never change the board");
});

test("hasUserOrder reads whether anything has been placed", () => {
  assert.equal(board.hasUserOrder([record("a"), record("b")]), false);
  assert.equal(board.hasUserOrder([record("a"), record("b", { position: 0 })]), true);
});
