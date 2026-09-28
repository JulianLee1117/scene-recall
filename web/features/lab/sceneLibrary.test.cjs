const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const helpers = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "sceneLibrary.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: helpers });
const row = { unit_id: "shot", film_id: "film", t_start: 90, t_end: 110,
  caption: "A figure turns", keyframe_index: 2, matched_frame_index: 2, matched_frame_timestamp: 100 };

test("library preview uses a legal window centered on the retrieved frame", () => {
  const result = helpers.libraryAlternative(row, 4);
  assert.equal(result.clip.source_start, 98);
  assert.equal(result.clip.source_end, 102);
  assert.equal(result.search_evidence.matched_frame_index, 2);
  const edge = helpers.libraryAlternative({ ...row, matched_frame_timestamp: 109.8 }, 4);
  assert.equal(edge.clip.source_start, 106);
  assert.equal(edge.clip.source_end, 110);
});

test("short results retain their real duration rather than inventing footage", () => {
  const result = helpers.libraryAlternative({ ...row, t_start: 99, t_end: 101 }, 4);
  assert.equal(result.clip.source_start, 99);
  assert.equal(result.clip.source_end, 101);
});

test("a shorter timeline drop retains the retrieved moment and original source bounds", () => {
  const result = helpers.libraryAlternative(row, 4);
  const fitted = helpers.fitDraggedScene(result.clip, 1, result.search_evidence);
  assert.equal(fitted.source_start, 99.5);
  assert.equal(fitted.source_end, 100.5);
  assert.equal(result.clip.source_start, 98, "fitting must not mutate the library preview");
});

test("missing or out-of-range visual timestamps do not invent a matched moment", () => {
  const result = helpers.libraryAlternative({ ...row, matched_frame_timestamp: undefined }, 4);
  assert.equal(result.clip.source_start, 98);
  assert.equal(result.search_evidence.matched_frame_timestamp, null);
  assert.equal(helpers.fitDraggedScene(result.clip, 1, null), result.clip);
});
