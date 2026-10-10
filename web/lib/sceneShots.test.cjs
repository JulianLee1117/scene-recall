const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

function load(name, require = () => { throw new Error("Unexpected import"); }) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, name), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, { exports, require });
  return exports;
}
const moments = load("resultMoment.ts");
const { matchingShots } = load("sceneShots.ts", (name) => {
  assert.equal(name, "@/lib/resultMoment");
  return moments;
});
const plain = (value) => JSON.parse(JSON.stringify(value));
const shot = (id, start, heroTime = start) => ({
  unit_id: id, film_id: "film", film_title: "Film", t_start: start, t_end: start + 30,
  caption: "A shot.", keyframe_index: 1, keyframe_url: `/media/keyframe/${id}/1`,
  preview_url: `/media/preview/${id}`, thumbnail_url: `/media/hero/${id}`,
  hero_url: `/media/hero/${id}`, hero_time: heroTime,
});

test("matching-shot navigation is chronological by its displayed moments, without replacing the best match", () => {
  const original = shot("best", 3110, 3116);
  original.scene_alternatives = [shot("later", 3130, 3139), shot("earlier", 3090, 3105), shot("middle", 3100, 3115)];
  const before = JSON.stringify(original);
  const matching = matchingShots(original);
  assert.deepEqual(plain(matching.map((item) => item.unit_id)), ["earlier", "middle", "best", "later"]);
  assert.equal(matching[2], original);
  assert.equal(JSON.stringify(original), before);
});

test("a folded lookalike keeps its own scene and evidence instead of borrowing the representative's details", () => {
  const original = { ...shot("best", 3110), scene: { id: "scene-a", title: "At home", summary: "A conversation." } };
  const anotherScene = { id: "scene-b", title: "At work", summary: "A later conversation." };
  original.scene_alternatives = [{ ...shot("other-scene", 3599), scene: anotherScene,
    caption: "An office.", action: "She picks up the phone.", focus_start: 3600, focus_end: 3610,
    matched_line: { text: "Hello?", t_start: 3601, t_end: 3602, score: 1 } }, shot("unknown-scene", 3700)];
  const [, other, unknown] = matchingShots(original);
  assert.equal(other.scene, anotherScene);
  assert.equal(other.action, "She picks up the phone.");
  assert.equal(other.matched_line.text, "Hello?");
  assert.equal(other.focus_start, 3600);
  assert.equal(unknown.scene, undefined);
});

test("legacy compact alternatives remain usable and ties have a deterministic order", () => {
  const original = shot("z", 10, 15);
  original.scene_alternatives = [
    { unit_id: "a", t_start: 10, t_end: 20, keyframe_url: "/media/keyframe/a/1", keyframe_index: 1,
      thumbnail_url: "/media/hero/a", hero_time: 15 },
    { unit_id: "b", t_start: 5, t_end: 20, keyframe_url: "/media/keyframe/b/1", keyframe_index: 1,
      thumbnail_url: "/media/hero/b", hero_time: 15 },
  ];
  const matching = matchingShots(original);
  assert.deepEqual(plain(matching.map((item) => item.unit_id)), ["b", "a", "z"]);
  assert.equal(matching[0].film_id, "film");
  assert.equal(matching[0].caption, "");
  assert.equal(matching[0].preview_url, "");
});
