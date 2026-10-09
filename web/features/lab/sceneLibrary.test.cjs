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
const clip = { id: "clip", film_id: "film", unit_id: "shot", title: "A figure turns", source_start: 98, source_end: 102, locked: false };

test("a shorter timeline drop keeps the retrieved moment inside the source bounds", () => {
  const fitted = helpers.fitDraggedScene(clip, 1, { matched_frame_timestamp: 100 });
  assert.equal(fitted.source_start, 99.5);
  assert.equal(fitted.source_end, 100.5);
  assert.equal(clip.source_start, 98, "fitting must not mutate the dropped clip");
});

test("a missing or out-of-range moment leaves the drop as it is", () => {
  assert.equal(helpers.fitDraggedScene(clip, 1, null), clip);
  assert.equal(helpers.fitDraggedScene(clip, 1, { matched_frame_timestamp: 120 }), clip);
});
