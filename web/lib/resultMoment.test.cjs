const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const moments = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "resultMoment.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: moments });
const plain = (value) => JSON.parse(JSON.stringify(value));
const floating = {
  unit_id: "swim", t_start: 1102.393, t_end: 1119.201,
  hero_url: "/media/hero/swim", thumbnail_url: "/media/hero/swim", hero_time: 1105,
  focus_start: 1102.393, focus_end: 1106.595,
  keyframe_url: "/media/keyframe/swim/2", keyframe_index: 2,
  matched_frame_url: "/media/keyframe/swim/2", matched_frame_index: 2, matched_frame_timestamp: 1114.999,
};

test("a hero card opens and saves the displayed picture, not another retrieval channel's frame", () => {
  assert.equal(moments.displayMoment(floating), 1105);
  assert.deepEqual(plain(moments.bookmarkAnchor(floating)), { evidence_timestamp: 1105, frame_index: null });
  assert.equal(floating.keyframe_index, 2, "visual source identity is not mutated");
});

test("a displayed visual match keeps its exact indexed frame even when another hero exists", () => {
  const visual = { ...floating, thumbnail_url: floating.matched_frame_url };
  assert.equal(moments.displayMoment(visual), 1114.999);
  assert.deepEqual(plain(moments.bookmarkAnchor(visual)), { evidence_timestamp: 1114.999, frame_index: 2 });
});

test("Saved uses its durable time even if the nearest indexed frame or current hero changed", () => {
  const saved = { ...floating, evidence_timestamp: 1104.4, thumbnail_url: "/media/frame/film?t=1104.4" };
  assert.equal(moments.displayMoment(saved), 1104.4);
  assert.deepEqual(plain(moments.bookmarkAnchor(saved)), { evidence_timestamp: 1104.4, frame_index: null });
});

test("a frame hint is accepted only for the same timestamp, including clause-owned frames", () => {
  const source = { ...floating, matches: [{ evidence: { type: "frame", frame_index: 0, timestamp: 1105 } }] };
  assert.deepEqual(plain(moments.bookmarkAnchor(source)), { evidence_timestamp: 1105, frame_index: 0 });
  assert.equal(moments.bookmarkAnchor({ ...source, matches: [{ evidence: { type: "frame", frame_index: 0, timestamp: 1104 } }] }).frame_index, null);
});

test("legacy hero thumbnails and missing metadata have finite deterministic fallbacks", () => {
  assert.equal(moments.displayMoment({ ...floating, hero_url: undefined }), 1105);
  assert.equal(moments.displayMoment({ t_start: 20, t_end: 30, focus_start: 22, hero_time: NaN, matched_frame_timestamp: Infinity }), 22);
  assert.equal(moments.displayMoment({ t_start: 20, t_end: 30 }), 20);
});

test("Saved previews require a known focus span containing their durable moment", () => {
  const saved = { ...floating, preview_url: "/media/preview/swim?focus=1102.39", evidence_timestamp: 1105 };
  assert.equal(moments.hoverPreviewUrl(saved), saved.preview_url);
  for (const change of [
    { evidence_timestamp: saved.focus_start - 0.001 },
    { evidence_timestamp: saved.focus_end },
    { evidence_timestamp: 1115 },
    { focus_start: undefined }, { focus_end: undefined },
    { focus_end: saved.focus_start }, { focus_end: Infinity },
    { focus_start: 1104.8, focus_end: 1105.3 },
    { preview_url: "" }, { preview_url: "   " },
  ]) {
    assert.equal(moments.hoverPreviewUrl({ ...saved, ...change }), null);
  }
  assert.equal(moments.hoverPreviewUrl({ ...saved, evidence_timestamp: saved.focus_start }), saved.preview_url);
  assert.equal(moments.hoverPreviewUrl({ ...saved, evidence_timestamp: undefined, focus_start: undefined }), saved.preview_url,
    "ordinary results keep their existing preview behavior");
});
