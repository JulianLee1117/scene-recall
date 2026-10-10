const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const compile = (file) => ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const rows = {};
vm.runInNewContext(compile("justifiedRows.ts"), { exports: rows });
const board = {};
vm.runInNewContext(compile("boardLayout.ts"), { exports: board, Map, require(name) { if (name === "./justifiedRows") return rows; throw new Error(name); } });

const tile = (id, aspect = 2) => ({ kind: "tile", id, aspect });
const place = (layout, id) => JSON.parse(JSON.stringify(layout.tiles.get(id)));

test("tiles fill justified rows, a short last row keeps the target height, and headings take their room", () => {
  // Width 402 with a 2px gap: two 2:1 tiles make a row 100 tall.
  const layout = board.layoutBoard([tile("a"), tile("b"), tile("c")], 402, 100);
  assert.deepEqual(place(layout, "a"), { x: 0, y: 0, width: 200, height: 100 });
  assert.deepEqual(place(layout, "b"), { x: 202, y: 0, width: 200, height: 100 });
  assert.deepEqual(place(layout, "c"), { x: 0, y: 102, width: 200, height: 100 }, "alone on the last row, at the target height");
  assert.equal(layout.height, 202);

  const grouped = board.layoutBoard([
    { kind: "heading", key: "heat" }, tile("a"), tile("b"),
    { kind: "heading", key: "moon" }, tile("c"),
  ], 402, 100);
  assert.equal(grouped.headings.get("heat"), 0, "the first heading sits at the top");
  assert.equal(place(grouped, "a").y, board.HEADING_HEIGHT);
  const second = board.HEADING_HEIGHT + 100 + board.GROUP_GAP;
  assert.equal(grouped.headings.get("moon"), second, "a later group leaves room above its heading");
  assert.equal(place(grouped, "c").y, second + board.HEADING_HEIGHT);
  assert.equal(grouped.height, second + board.HEADING_HEIGHT + 100);
  assert.equal(board.layoutBoard([], 402, 100).height, 0);
});

test("placeAt reads the drop place from the pointer: a side of a tile, the ends, or nothing", () => {
  const order = ["a", "b", "c", "d"];
  const layout = board.layoutBoard(order.map((id) => tile(id)), 402, 100);
  // Rows: a b / c d, each tile 200 wide and 100 tall.
  assert.equal(board.placeAt(layout, order, "a", 50, 50), null, "over its own place: nothing changes");
  assert.equal(board.placeAt(layout, order, "a", 250, 50), 1, "left half of b: before b");
  assert.equal(board.placeAt(layout, order, "a", 350, 50), 2, "right half of b: after b");
  assert.equal(board.placeAt(layout, order, "a", 50, 150), 2, "left half of c: before c");
  assert.equal(board.placeAt(layout, order, "a", 390, 150), 4, "right half of d: the end");
  assert.equal(board.placeAt(layout, order, "a", 300, -20), 0, "above the board: the start");
  assert.equal(board.placeAt(layout, order, "a", 100, 300), 4, "below the board: the end");
  assert.equal(board.placeAt(layout, order, "a", 201, 150), 3, "in a gap: the nearest side");
  assert.equal(board.placeAt(layout, order, "a", 100, 101), null, "between rows: nothing");
  assert.equal(board.placeAt(layout, ["a"], "a", 500, 500), null, "nothing else on the board");
});
