const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const compile = (file) => ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const lib = {};
vm.runInNewContext(compile("../../../lib/matchCuts.ts"), { exports: lib, process: { env: {} }, URLSearchParams });
const vision = {};
vm.runInNewContext(compile("../../../lib/matchVision.ts"), { exports: vision });
const close = (actual, expected, message) => assert.ok(Math.abs(actual - expected) < 1e-6, `${message}: ${actual} != ${expected}`);

test("a whole picture of the output's shape fills the box", () => {
  const place = lib.placement([0, 0, 1, 1], 16 / 9, null, 16 / 9);
  for (const [key, value] of Object.entries({ left: 0, top: 0, width: 100, height: 100 })) close(place[key], value, key);
});

test("a wider picture is letterboxed and centred", () => {
  const place = lib.placement(null, 2.39, null, 16 / 9);
  close(place.width, 100, "width");
  close(place.height, (16 / 9) / 2.39 * 100, "height");
  close(place.top, (100 - place.height) / 2, "top");
});

test("a crop of the content fills the box and letterbox bars in the video frame are skipped", () => {
  // Video frame 16:9 with 2.39 content between bars; zoomed 2x on the content's right half.
  const box = [0, 0.128, 1, 0.872];
  const content = 16 / 9 * 1 / (0.872 - 0.128);
  const place = lib.placement([0.5, 0.25, 0.5, 0.5], lib.frameAspect(content, box), box, content);
  close(place.width, 200, "width");                        // the region is half the frame wide
  close(place.left, -100, "left");                         // its left edge sits at the box's left edge
  close(place.top, -(0.128 + 0.25 * 0.744) / (0.5 * 0.744) * 100, "top");
});

test("a crop fits a format only in that format's shape", () => {
  const crop = [0.38, 0, (9 / 16) / 2.39, 1];                // full height of a widescreen picture
  assert.equal(lib.cropFits(crop, 2.39, "vertical"), true);
  assert.equal(lib.cropFits(crop, 2.39, "landscape"), false);
  assert.equal(lib.cropFits([0.1, 0.1, 0.6, 0.6], 2.39, "landscape"), true);    // a uniform zoom keeps the picture's shape
});

test("the cut point snaps to the nearest usable instant", () => {
  const shot = { moments: [{ time: 10, ok: true }, { time: 10.25, ok: false }, { time: 10.5, ok: true }] };
  assert.equal(lib.nearestMoment(shot, 10.3), 10.5);
  assert.equal(lib.nearestMoment({ moments: [] }, 3), 3);
});

test("settings survive the address", () => {
  const href = lib.matchHref("film_0001", 12.3456, { focus: "motion", output: "vertical", reframe: true, includeSameFilm: false });
  const params = new URLSearchParams(href.split("?")[1]);
  assert.equal(params.get("time"), "12.346");
  assert.deepEqual(JSON.parse(JSON.stringify(lib.settingsFrom(params))), { focus: "motion", output: "vertical", reframe: true, includeSameFilm: false });
  assert.deepEqual(JSON.parse(JSON.stringify(lib.settingsFrom(new URLSearchParams("focus=vibes&output=wide")))), JSON.parse(JSON.stringify(lib.DEFAULT_SETTINGS)));
});

test("a search result opens at its matched frame, inside the shot", () => {
  assert.equal(lib.resultTime({ t_start: 5, t_end: 9, matched_frame_timestamp: 6.5 }), 6.5);
  assert.equal(lib.resultTime({ t_start: 5, t_end: 9 }), 8.7);
  assert.equal(lib.resultTime({ t_start: 5, t_end: 9, matched_frame_timestamp: 20 }), 8.95);
});

const moment = (layers) => ({ unit_id: "u", film_id: "f", time: 1, usable: true, aspect: 2, layers,
  source: { index: "i", profile: "match-v1-abc", models: { objects: "seg-model", pose: "pose-model" } } });

test("vision draws any described layer in the picture's own shape, and only the layers asked for", () => {
  const layers = [
    { key: "objects", label: "Objects", kind: "regions",
      items: [{ label: "person", score: 0.95, box: [0.25, 0.1, 0.75, 0.9], mask: ["0110", "1111", "0000"] }] },
    { key: "pose", label: "Pose", kind: "skeletons", joints: ["a", "b", "c"], edges: [[0, 1], [1, 2]],
      items: [{ points: [[0.5, 0.2, 0.9], [0.5, 0.4, 0.9], [0.5, 0.6, 0.1]] }] },
    { key: "eyes", label: "Eye point", kind: "points", items: [{ label: "eyes", at: [0.5, 0.2] }] },
    { key: "lines", label: "Lines", kind: "field", columns: 2, rows: 1, cells: [[0, 0, Math.PI / 2, 1]] },
    { key: "light", label: "Light", kind: "grid", columns: 2, rows: 1, values: [0, 1] },
    { key: "motion", label: "Motion", kind: "vectors", seconds: 2, zoom: null, items: [{ label: "camera", from: [0.5, 0.5], to: [0.6, 0.5] }] },
  ];
  const all = vision.visionShapes(moment(layers), new Set(layers.map((layer) => layer.key)));
  assert.equal(all.width, 200);
  assert.equal(all.height, 100);
  const by = (key) => all.shapes.filter((shape) => shape.layer === key);
  // A silhouette is one cell per run of filled squares, inside its box.
  const cells = by("objects").filter((shape) => shape.kind === "cell");
  assert.equal(cells.length, 2);
  close(cells[0].x, 0.25 * 200 + 25, "first run starts one column in");
  close(cells[0].w, 50, "two columns wide");
  assert.ok(by("objects").some((shape) => shape.kind === "label" && shape.text === "person 0.95"));
  // A bone needs both its ends shown.
  assert.equal(by("pose").filter((shape) => shape.kind === "line").length, 1);
  assert.equal(by("eyes")[0].ring, true);
  const [stroke] = by("lines");
  close(stroke.x1, stroke.x2, "an edge at a right angle is upright");
  assert.deepEqual(Array.from(by("light"), (shape) => shape.fill), ["rgb(0,0,0)", "rgb(255,255,255)"]);
  assert.ok(by("light").every((shape) => shape.area) && cells.every((shape) => !shape.area), "grid cells cover an area; a silhouette does not");

  // A grid finer than 16 columns is drawn in blocks: mean grey, mean colour.
  const fine = vision.visionShapes(moment([
    { key: "light", label: "Light", kind: "grid", columns: 32, rows: 2, values: Array.from({ length: 64 }, (_, index) => (index % 2 ? 1 : 0)) },
    { key: "colour", label: "Colour", kind: "grid", columns: 1, rows: 1, colors: ["#ff8000"] },
  ]), new Set(["light", "colour"]));
  const blocks = fine.shapes.filter((shape) => shape.layer === "light");
  assert.equal(blocks.length, 16, "32 x 2 cells draw as 16 x 1 blocks");
  close(blocks[0].w, 200 / 16, "a block spans two columns");
  assert.equal(blocks[0].fill, "rgb(128,128,128)");
  assert.equal(fine.shapes.find((shape) => shape.layer === "colour").fill, "rgb(255,128,0)");
  assert.equal(by("motion")[0].arrow, true);

  const some = vision.visionShapes(moment(layers), new Set(["eyes"]));
  assert.deepEqual([...new Set(some.shapes.map((shape) => shape.layer))], ["eyes"]);
  assert.equal(vision.visionSource(moment([])), "seg-model · pose-model · match-v1-abc");
});

test("vision shows one reason of a cut: the one pointed at, else the strongest it can draw", () => {
  const reasons = [
    { code: "reframe", label: "Reframed 1.2x", strength: 0, layers: [] },
    { code: "eyes", label: "Eye line carries over", strength: 0.8, layers: ["eyes"] },
    { code: "light", label: "Light falls alike", strength: 0.6, layers: ["light"] },
  ];
  assert.equal(vision.shownReason(reasons, null).code, "eyes");
  assert.equal(vision.shownReason(reasons, "light").code, "light");
  assert.equal(vision.shownReason(reasons, "reframe").code, "eyes", "a reason with nothing to draw falls back");
  assert.equal(vision.shownReason([{ code: "push", label: "Push", strength: 1 }], null), null, "an older index names no layers");
});
