const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const exported = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "editorDirection.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: exported });
const { effectiveEditorDirection, changeEditorDirection, appendDirectionExample, availableDirectionRange, directionRangeBounds, fitDirectionRange, updateDirectionRange } = exported;
const plain = (value) => JSON.parse(JSON.stringify(value));

test("legacy direction migrates only user-authored material and explicit empty stays empty", () => {
  const legacy = { brief: "Red dreams", visual_plan: { source: "user", arc: "Open into daylight", motifs: "Hands" } };
  assert.equal(effectiveEditorDirection(legacy).instruction, "Red dreams\n\nOpen into daylight\n\nHands");
  assert.equal(effectiveEditorDirection({ ...legacy, visual_plan: { ...legacy.visual_plan, source: "ai" } }).instruction, "Red dreams");
  assert.equal(effectiveEditorDirection({ ...legacy, editor_direction: null }).instruction, effectiveEditorDirection(legacy).instruction);
  assert.deepEqual(plain(effectiveEditorDirection({ ...legacy, editor_direction: { instruction: "", ranges: [] } })), { instruction: "", ranges: [] });
});

test("direction edits retain the exact timeline, clips, generated evidence, and lyrics", () => {
  const document = { brief: "Old", clips: [{ id: "clip" }], music_timeline: { slots: [{ id: "slot", needs_direction: false, reason: "Old reason" }] },
    song_context: { lyrics: [{ text: "words" }] }, visual_plan: { source: "ai", arc: "Generated" } };
  const next = changeEditorDirection(document, { instruction: "New" });
  for (const field of ["clips", "music_timeline", "song_context", "visual_plan"]) assert.equal(next[field], document[field]);
  assert.equal(next.brief, "Old");
  assert.equal(changeEditorDirection(next, { instruction: "New" }), next);
});

test("examples append without deleting existing instructions or timed directions", () => {
  const ranges = [{ id: "intro", start: 14, end: 18, instruction: "Fast" }];
  const document = { editor_direction: { instruction: "Existing", ranges } };
  const next = appendDirectionExample(document, "Surreal colors");
  assert.equal(next.editor_direction.instruction, "Existing\n\nSurreal colors");
  assert.equal(next.editor_direction.ranges, ranges);
  assert.equal(appendDirectionExample({ editor_direction: { instruction: "x".repeat(24000), ranges: [] } }, "more").editor_direction.instruction.length, 24000);
});

test("free ranges respect non-overlap, passage offset, and occupied playheads", () => {
  const ranges = [{ id: "old", start: 0, end: 5 }, { id: "intro", start: 10, end: 15 }, { id: "chorus", start: 20, end: 24 }];
  assert.deepEqual(plain(availableDirectionRange(ranges, { start: 10, end: 30 }, 12)), { start: 15, end: 19 });
  assert.deepEqual(plain(availableDirectionRange(ranges, { start: 10, end: 30 }, 18)), { start: 18, end: 20 });
  assert.equal(availableDirectionRange([{ start: 0, end: 30 }], { start: 10, end: 30 }, 20), null);
});

test("dragging and trimming cannot cross neighboring ranges or source track limits", () => {
  const range = { id: "middle", start: 15, end: 18, instruction: "hold" };
  const rows = [{ id: "first", start: 2, end: 12 }, range, { id: "last", start: 24, end: 30 }];
  const bounds = directionRangeBounds(rows, "middle", 90);
  assert.deepEqual(plain(bounds), { start: 12, end: 24 });
  assert.deepEqual(plain(fitDirectionRange(range, "move", -500, bounds)), { start: 12, end: 15 });
  assert.deepEqual(plain(fitDirectionRange(range, "move", 500, bounds)), { start: 21, end: 24 });
  assert.deepEqual(plain(fitDirectionRange(range, "start", 500, bounds)), { start: 17.9, end: 18 });
  assert.deepEqual(plain(fitDirectionRange(range, "end", 500, bounds)), { start: 15, end: 24 });
});

test("editing an in-passage direction preserves outside-passage directions and stable identity", () => {
  const first = { id: "earlier", start: 1, end: 8, instruction: "Slow" };
  const document = { editor_direction: { instruction: "Global", ranges: [first, { id: "current", start: 20, end: 30, instruction: "Fast" }] } };
  const next = updateDirectionRange(document, "current", { id: "accidental", instruction: "Hold" });
  assert.equal(next.editor_direction.ranges[0], first);
  assert.equal(next.editor_direction.ranges[1].id, "current");
  assert.equal(updateDirectionRange(next, "missing", { instruction: "ignored" }), next);
});

test("rounding a dragged boundary never crosses sub-millisecond neighboring bounds", () => {
  const range = { start: 10, end: 12 };
  const bounds = { start: 9.9996, end: 15.3336 };
  assert.ok(fitDirectionRange(range, "start", -20, bounds).start >= bounds.start);
  assert.ok(fitDirectionRange(range, "end", 20, bounds).end <= bounds.end);
  assert.ok(fitDirectionRange(range, "move", 20, bounds).end <= bounds.end);
});
