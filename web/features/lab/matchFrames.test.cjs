const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const exports_ = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "matchFrames.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: exports_ });
const { frameAt, frameContains, stepFrame, referenceAt } = exports_;
const frames = [{ time: 100, end: 100.041708 }, { time: 100.041708, end: 100.103 }, { time: 100.103, end: 100.123 }];

test("source stepping follows variable frame intervals and preserves exact PTS", () => {
  assert.equal(stepFrame(frames, 100.001, 1).time, 100.041708);
  assert.equal(stepFrame(frames, 100.045, 1).time, 100.103);
  assert.equal(stepFrame(frames, 100.104, -1).time, 100.041708);
  assert.equal(frameAt(frames, 100.103).time, 100.103);
  assert.equal(stepFrame(frames, 100, -1), undefined);
  assert.equal(stepFrame(frames, 100.103, 1), undefined);
});
test("a prompt belongs to its native frame interval, not a nominal frame-rate grid", () => {
  assert.equal(frameContains(frames[1], 100.045), true);
  assert.equal(frameContains(frames[1], 100.102), true);
  assert.equal(frameContains(frames[1], 100.103), false);
  assert.equal(frameContains(frames[1], 100), false);
});
test("a new matching moment can reach the whole shot beyond the initial three-second clip", () => {
  const clip = { source_start: 102, source_end: 105, reference_time: 104 };
  const result = referenceAt(clip, 112.375, { t_start: 100, t_end: 120 });
  assert.equal(result.reference_time, 112.375);
  assert.ok(result.source_start <= result.reference_time && result.source_end > result.reference_time);
  assert.equal(result.source_end - result.source_start, 3);
  assert.equal(result.window_start, null);
});
test("reference timing cannot escape the source shot, including short shots", () => {
  const clip = { source_start: 100, source_end: 100.4 };
  const bounds = { t_start: 100, t_end: 100.4 };
  for (const time of [90, 100, 100.3, 101]) {
    const result = referenceAt(clip, time, bounds);
    assert.ok(result.source_start >= bounds.t_start);
    assert.ok(result.source_end <= bounds.t_end);
    assert.ok(result.reference_time >= result.source_start && result.reference_time < result.source_end);
  }
});
