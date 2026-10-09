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
vm.runInNewContext(compile("../../lib/matchCuts.ts"), { exports: lib, process: { env: {} }, URLSearchParams });
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
