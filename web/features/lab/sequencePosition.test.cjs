const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const helpers = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "sequencePosition.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: helpers });
const cut = 16.939999999999998;
const slots = [{ id: "outgoing", start: 12, end: cut }, { id: "incoming", start: cut, end: 20 }];

test("an exact editorial cut and its microsecond-rounded media clock choose the same incoming slot", () => {
  assert.equal(helpers.slotAt(cut, slots, 20), slots[1]);
  assert.equal(helpers.slotAt(16.939999, slots, 20), slots[1]);
  assert.equal(helpers.slotAt(16.94, slots, 20), slots[1]);
});

test("actual before-cut seeks and frame steps stay in the outgoing clip", () => {
  assert.equal(helpers.slotAt(cut - 0.0001, slots, 20), slots[0]);
  assert.equal(helpers.slotAt(cut - 1 / 24, slots, 20), slots[0]);
  assert.equal(helpers.slotAt(cut + 1 / 24, slots, 20), slots[1]);
});

test("passage end shows the final slot while empty slots retain their own identity", () => {
  assert.equal(helpers.slotAt(20, slots, 20), slots[1]);
  const gaps = [{ ...slots[0], clip_id: null }, { ...slots[1], clip_id: "scene" }];
  assert.equal(helpers.slotAt(15, gaps, 20), gaps[0]);
  assert.equal(helpers.slotAt(16.939999, gaps, 20), gaps[1]);
});

test("absent slots, genuine gaps and nonfinite times do not invent a selection", () => {
  assert.equal(helpers.slotAt(13, [], 20), undefined);
  assert.equal(helpers.slotAt(15, [{ start: 12, end: 14 }, { start: 16, end: 20 }], 20), undefined);
  assert.equal(helpers.slotAt(NaN, slots, 20), undefined);
  assert.equal(helpers.slotAt(Infinity, slots, 20), undefined);
});
