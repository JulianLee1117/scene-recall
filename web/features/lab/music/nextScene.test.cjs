const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const helpers = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "nextScene.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: helpers });

const anchor = { id: "a", film_id: "film-a", source_start: 10, source_end: 14, locked: false };
const slots = [
  { id: "a-slot", start: 20, end: 24, clip_id: "a" },
  { id: "b-slot", start: 24, end: 28, clip_id: null },
  { id: "c-slot", start: 28, end: 32, clip_id: null },
];

test("a filled selection anchors its immediate next slot; an empty selection uses only its immediate predecessor", () => {
  for (const id of ["a-slot", "b-slot"]) {
    const pair = helpers.nextScenePair(slots, [anchor], id);
    assert.equal(pair.anchorSlot.id, "a-slot");
    assert.equal(pair.nextSlot.id, "b-slot");
    assert.equal(pair.problem, null);
    assert.equal(pair.canFlex, true);
  }
  const separated = helpers.nextScenePair(slots, [anchor], "c-slot");
  assert.ok(separated.problem, "never substitute a distant available anchor across an empty slot");
  assert.equal(separated.canFlex, false);
  assert.ok(helpers.nextScenePair(slots, [anchor], "missing").problem);
});

test("locked and filled neighboring scenes keep timing authority", () => {
  const lockedAnchor = helpers.nextScenePair(slots, [{ ...anchor, locked: true }], "a-slot");
  assert.equal(lockedAnchor.problem, null);
  assert.equal(lockedAnchor.canFlex, false);
  const b = { ...anchor, id: "b", locked: false };
  const filled = [{ ...slots[0] }, { ...slots[1], clip_id: "b" }];
  assert.equal(helpers.nextScenePair(filled, [anchor, b], "a-slot").canFlex, false);
  assert.match(helpers.nextScenePair(filled, [anchor, { ...b, locked: true }], "a-slot").problem, /Unlock/);
  assert.ok(helpers.nextScenePair([slots[0]], [anchor], "a-slot").problem);
  assert.ok(helpers.nextScenePair([slots[0], { ...slots[1], start: 25 }], [anchor], "a-slot").problem);
});

test("adjustment bounds leave enough source for the complete incoming interval", () => {
  const scope = { t0: 20, t2: 30, current_cut: 25, cut_min: 23, cut_max: 27, passage_start: 0 };
  const candidate = { incoming_authority: { t_start: 100, t_end: 106 } };
  const bounds = helpers.nextSceneBounds(scope, candidate, 23);
  assert.equal(bounds.cutMin, 24);
  assert.equal(bounds.cut, 24);
  assert.equal(bounds.sourceMin, 100);
  assert.equal(bounds.sourceMax, 100);
  const later = helpers.nextSceneBounds(scope, candidate, 27);
  assert.equal(later.sourceMax, 103);
  assert.equal(later.sourceMax + (scope.t2 - later.cut), 106);
});

test("cut-slider endpoints and values stay on the passage-relative preview frame grid", () => {
  const scope = { t0: 20, t2: 30, current_cut: 25.013333333333332, cut_min: 23.03, cut_max: 27.08, passage_start: 14.43 };
  const candidate = { incoming_authority: { t_start: 100, t_end: 106.27 } };
  const bounds = helpers.nextSceneBounds(scope, candidate, 23.75);
  for (const value of [bounds.cutMin, bounds.cutMax, bounds.cut]) {
    assert.ok(Math.abs((value - scope.passage_start) * 24 - Math.round((value - scope.passage_start) * 24)) < 1e-6);
  }
  assert.ok(bounds.cutMin >= 23.73);
  assert.ok(bounds.cutMax <= scope.cut_max);
});

test("a non-grid saved boundary remains available when it is the only source-feasible cut", () => {
  const scope = { t0: 28, t2: 32, current_cut: 30.06, cut_min: 30.05, cut_max: 30.07, passage_start: 0 };
  const candidate = { incoming_authority: { t_start: 100, t_end: 101.95 } };
  const bounds = helpers.nextSceneBounds(scope, candidate, 30.05);
  assert.equal(bounds.cutMin, 30.06);
  assert.equal(bounds.cutMax, 30.06);
  assert.equal(bounds.cut, 30.06);
  assert.ok(bounds.sourceMax >= bounds.sourceMin);
  assert.ok(bounds.sourceMax + (scope.t2 - bounds.cut) <= 101.95 + 1e-6);
});

test("an existing non-grid endpoint is preserved without permitting other non-grid moves", () => {
  const scope = { t0: 20, t2: 30, current_cut: 23.04, cut_min: 23.03, cut_max: 27.08, passage_start: 14.43 };
  const candidate = { incoming_authority: { t_start: 100, t_end: 108 } };
  const kept = helpers.nextSceneBounds(scope, candidate, scope.current_cut);
  assert.equal(kept.cutMin, scope.current_cut);
  assert.equal(kept.cut, scope.current_cut);
  const moved = helpers.nextSceneBounds(scope, candidate, 25.2);
  assert.ok(Math.abs((moved.cut - scope.passage_start) * 24 - Math.round((moved.cut - scope.passage_start) * 24)) < 1e-6);
});

test("an adjusted preview is ready only for its own parent and exact timing", () => {
  const candidate = { id: "option", incoming: { source_start: 101 }, cut: 25 };
  assert.equal(helpers.sameNextSceneTiming(candidate, 101, 25), true);
  assert.equal(helpers.sameNextSceneTiming(candidate, 101 + 1 / 24, 25), false);
  assert.equal(helpers.sameNextSceneTiming(candidate, 101, 25 + 1 / 24), false);
  const job = { kind: "next-scene-preview", status: "completed", result: { next_scene_job_id: "parent", candidate } };
  assert.equal(helpers.nextScenePreview(job, "parent").candidate, candidate);
  assert.equal(helpers.nextScenePreview(job, "different"), null);
  assert.equal(helpers.nextScenePreview({ ...job, status: "failed" }, "parent"), null);
  assert.equal(helpers.nextSceneResult(job), null);
});

test("zoom smoothly removes bars before filling output without stretching wide or tall sources", () => {
  for (const [source, output] of [[2.4, 16 / 9], [9 / 16, 16 / 9], [16 / 9, 9 / 16]]) {
    assert.equal(helpers.nextSceneZoomCrop(1, source, output, null), null);
    const fillZoom = Math.max(source / output, output / source);
    let lastArea = 1;
    for (const zoom of [1.01, fillZoom, fillZoom * 1.5]) {
      const crop = helpers.nextSceneZoomCrop(zoom, source, output, null);
      const geometry = helpers.nextSceneCropGeometry(crop, source, output);
      assert.ok(crop.width * crop.height < lastArea);
      assert.ok(crop.x >= 0 && crop.x + crop.width <= 1 && crop.y >= 0 && crop.y + crop.height <= 1);
      assert.ok(Math.abs(geometry.width / geometry.height * output - source * crop.width / crop.height) < 1e-6, "display keeps crop proportions");
      assert.ok(Math.abs(helpers.nextSceneCropZoom(crop, source, output) - zoom) < 1e-6);
      if (zoom >= fillZoom) { assert.ok(Math.abs(geometry.width - 1) < 1e-6); assert.ok(Math.abs(geometry.height - 1) < 1e-6); }
      lastArea = crop.width * crop.height;
    }
  }
});

test("picture panning stays inside source and zoom preserves its center where possible", () => {
  const crop = { x: 0.2, y: 0.15, width: 0.5, height: 0.5 };
  const panned = helpers.nextScenePanCrop(crop, 100, -100);
  assert.equal(panned.x, 0.5);
  assert.equal(panned.y, 0);
  const zoomed = helpers.nextSceneZoomCrop(3, 16 / 9, 16 / 9, crop);
  assert.ok(Math.abs(zoomed.x + zoomed.width / 2 - 0.45) < 1e-6);
  assert.ok(Math.abs(zoomed.y + zoomed.height / 2 - 0.4) < 1e-6);
});

test("prepared proof includes exact crop representation, including explicit full frame versus null", () => {
  const candidate = { incoming: { source_start: 10 }, cut: 25 };
  const draft = { source_start: 10, cut_time: 25, crop: null };
  assert.equal(helpers.sameNextSceneDraft(candidate, draft), true);
  assert.equal(helpers.sameNextSceneDraft(candidate, { ...draft, crop: { x: 0, y: 0, width: 1, height: 1 } }), false);
  const identity = { ...candidate, incoming: { ...candidate.incoming, crop: { x: 0, y: 0, width: 1, height: 1 } } };
  assert.equal(helpers.sameNextSceneDraft(identity, draft), false, "resetting explicit identity crop to null must prepare new proof");
  assert.equal(helpers.sameSceneCrop(identity.incoming.crop, null), true, "visual equivalence remains appropriate for evidence comparison");
  const crop = { x: 0.2, y: 0, width: 0.5, height: 1 };
  assert.equal(helpers.sameNextSceneDraft(candidate, { ...draft, crop }), false);
  const cropped = { ...candidate, incoming: { ...candidate.incoming, crop } };
  assert.equal(helpers.sameNextSceneDraft(cropped, { ...draft, crop }), true);
  assert.equal(helpers.sameNextSceneDraft(cropped, { ...draft, crop: { ...crop, x: 0.21 } }), false);
  assert.equal(helpers.sameNextSceneDraft(cropped, { ...draft, crop: { ...crop, x: crop.x + 1e-8 } }), false, "manifest crop coordinates are exact, not visually equivalent");
  assert.equal(helpers.sameNextSceneDraft(cropped, draft), false, "full-frame reset also needs a new preview");
});
