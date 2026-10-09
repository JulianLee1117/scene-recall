const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const lib = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "justifiedRows.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: lib });
const plain = (value) => JSON.parse(JSON.stringify(value));

test("each row takes the run of tiles whose full-width height is closest to the target", () => {
  // Width 1000, target 200, no gap. Two 2.39 frames make a 209 px row; a third
  // would make 139 px, so the row stops at two.
  const layout = lib.layoutRows([2.39, 2.39, 2.39, 1.33, 1.33, 1.33, 1.33], 1000, 200, 0);
  assert.deepEqual(plain(layout.sizes), [2, 3, 2]);
  assert.equal(Math.round(layout.heights[0]), 209);
  assert.equal(Math.round(layout.heights[1]), Math.round(1000 / (2.39 + 1.33 * 2)), "a 2.39 and two 4:3 frames land at 198 px");
  assert.equal(layout.lastIsShort, true, "two 4:3 tiles cannot fill the width");
  assert.equal(layout.heights[2], 200, "the short last row keeps the target height");
});

test("rows stay near the target instead of leaving one tall pair", () => {
  const aspects = [1.85, 2.39, 2.39, 1.78, 2.39, 1.33, 1.85, 2.39, 1.78, 1.33];
  const { heights } = lib.layoutRows(aspects, 1500, 200, 2);
  for (const height of heights) assert.ok(height > 150 && height < 260, `row height ${Math.round(height)} stays near 200`);
});

test("only whole rows show while more results can arrive", () => {
  const layout = { sizes: [4, 3, 4, 2], heights: [200, 210, 190, 200], lastIsShort: true };
  assert.deepEqual(plain(lib.visibleTileCount(layout, { floor: 0, minRows: 3, exhausted: false })), { count: 11, rows: 3, atEnd: false });
  assert.deepEqual(plain(lib.visibleTileCount(layout, { floor: 12, minRows: 1, exhausted: false })), { count: 11, rows: 3, atEnd: false },
    "never the tentative last row before the results are exhausted");
  assert.deepEqual(plain(lib.visibleTileCount(layout, { floor: 12, minRows: 1, exhausted: true })), { count: 13, rows: 4, atEnd: true });
  assert.deepEqual(plain(lib.visibleTileCount({ sizes: [2], heights: [200], lastIsShort: true }, { floor: 0, minRows: 3, exhausted: false })),
    { count: 2, rows: 1, atEnd: false }, "a single row still shows rather than nothing");
});

test("row height follows the viewport, smaller for the reference lookup", () => {
  assert.deepEqual([lib.rowHeightFor(1360), lib.rowHeightFor(2400), lib.rowHeightFor(800), lib.rowHeightFor(390)], [190, 240, 140, 92]);
  assert.deepEqual([lib.rowHeightFor(1360, "small"), lib.rowHeightFor(390, "small")], [96, 76]);
  assert.deepEqual([lib.rowHeightFor(1360, "large"), lib.rowHeightFor(2400, "large"), lib.rowHeightFor(390, "large")], [286, 340, 140]);
  assert.equal(lib.cssAspect(16 / 9), 1.7778);
});
