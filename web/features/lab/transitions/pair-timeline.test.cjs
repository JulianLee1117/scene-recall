const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const compile = (file) => ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const transitions = {}; vm.runInNewContext(compile("transitions.ts"), { exports: transitions });
const helpers = {}; vm.runInNewContext(compile("pair-timeline.ts"), { exports: helpers, require: () => transitions });
const a = { film_id: "film-a", title: "First", source_start: 10, source_end: 13 };
const b = { film_id: "film-b", title: "Second", source_start: 20, source_end: 24 };
const recipe = { id: "whip-pan", duration: .4 };
const off = transitions.DEFAULT_RETIME;
const near = (actual, expected, tolerance = 1e-8) => assert.ok(Math.abs(actual - expected) < tolerance, `${actual} != ${expected}`);

test("pair geometry has a true output-time overlap and a zero-width hard cut", () => {
  const layout = helpers.pairLayout(a, b, recipe, off);
  near(layout.a.duration, 3); near(layout.b.duration, 4); near(layout.b.start, 2.6);
  near(layout.duration, 6.6); near(layout.overlapStart, 2.6); near(layout.overlapEnd, 3);
  const cut = helpers.pairLayout(a, b, { ...recipe, id: "hard-cut" }, off);
  near(cut.overlap, 0); near(cut.duration, 7); near(cut.b.start, 3);
});

test("source inspection resolves overlap by selected side and maps both directions", () => {
  const layout = helpers.pairLayout(a, b, recipe, off);
  assert.deepEqual(JSON.parse(JSON.stringify(helpers.sourceAtSequenceTime(layout, 2.8, "a"))), { side: "a", sourceTime: 12.8 });
  near(helpers.sourceAtSequenceTime(layout, 2.8, "b").sourceTime, 20.2);
  near(helpers.sequenceAtSourceTime(layout, { side: "b", sourceTime: 22 }), 4.6);
  assert.equal(helpers.sourceAtSequenceTime(layout, 1, "b").side, "a", "preferred side only resolves the overlap");
  assert.equal(helpers.sourceAtSequenceTime(layout, 4, "a").side, "b");
  assert.ok(helpers.sourceAtSequenceTime(layout, 100).sourceTime < b.source_end, "out is exclusive");
});

test("all speed shapes map source inspection through the same nominal integral as output timing", () => {
  for (const mode of ["rush", "slow-hit", "pulse"]) for (const curve of ["smooth", "snappy"]) {
    const speed = { ...off, mode, speed: mode === "slow-hit" ? .25 : 4, span: 1, curve };
    const layout = helpers.pairLayout(a, b, recipe, speed);
    assert.equal(layout.approximate, true);
    near(layout.a.duration, transitions.retimedFrames(3, speed) / 30);
    near(layout.b.duration, transitions.retimedFrames(4, speed) / 30);
    near(layout.a.outputKnots.at(-1), transitions.retimedDuration(3, speed));
    near(layout.b.outputKnots.at(-1), transitions.retimedDuration(4, speed));
    for (const side of ["a", "b"]) for (const phase of [.1, .4, .7, .9]) {
      const clip = layout[side], sourceTime = clip.source.source_start + (clip.source.source_end - clip.source.source_start) * phase;
      const sequence = helpers.sequenceAtSourceTime(layout, { side, sourceTime });
      near(helpers.sourceAtClipTime(layout, side, sequence).sourceTime, sourceTime);
    }
  }
});

test("trim limits preserve the opposite edge, source length, ramp, film end, and retimed overlap", () => {
  for (const speed of [off, { ...off, mode: "rush", speed: 4, span: .5 }, { ...off, mode: "slow-hit", speed: .25, span: .5 }]) {
    for (const edge of ["source_start", "source_end"]) {
      const result = helpers.timelineTrim(a, edge, edge === "source_start" ? 100 : -100, recipe, speed, 15);
      const next = { ...a, [edge]: result };
      assert.equal(transitions.validatePair(next, b, recipe, 12, speed), null);
      assert.equal(next[edge === "source_start" ? "source_end" : "source_start"], a[edge === "source_start" ? "source_end" : "source_start"]);
    }
  }
  assert.equal(helpers.timelineTrim(a, "source_start", -100, recipe, off), 1, "a clip retains at most 12 seconds");
  assert.equal(helpers.timelineTrim(a, "source_end", 100, recipe, off, 15), 15);
  assert.equal(helpers.timelineTrim(a, "source_end", NaN, recipe, off), null);
});

test("overlap resizing uses output frames and cannot consume either retimed clip", () => {
  const speed = { ...off, mode: "rush", speed: 4, span: .5 };
  const layout = helpers.pairLayout({ ...a, source_end: 10.5 }, b, recipe, speed);
  const duration = helpers.timelineDuration(layout, 100);
  assert.ok(duration * 30 < transitions.retimedFrames(.5, speed));
  assert.equal(helpers.timelineDuration(layout, 0), .1);
  assert.equal(helpers.timelineDuration(layout, NaN), null);
});

test("missing and invalid sources are safe and a single B clip begins at zero", () => {
  const empty = helpers.pairLayout(null, null, recipe, off);
  assert.equal(empty.duration, 0); assert.equal(helpers.sourceAtSequenceTime(empty, 0), null);
  const onlyB = helpers.pairLayout(null, b, recipe, off);
  assert.equal(onlyB.b.start, 0); near(helpers.sourceAtSequenceTime(onlyB, 1).sourceTime, 21);
  assert.equal(helpers.pairLayout({ ...a, source_start: NaN }, b, recipe, off).a, null);
  assert.equal(helpers.sequenceAtSourceTime(onlyB, { side: "a", sourceTime: 11 }), null);
});
