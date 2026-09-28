const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const compile = (file) => ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const transitions = {}; vm.runInNewContext(compile("transitions.ts"), { exports: transitions });
const helpers = {}; vm.runInNewContext(compile("fast-edit-timing.ts"), { exports: helpers, require: () => transitions });
const plain = (value) => JSON.parse(JSON.stringify(value));
const a = { film_id: "a", source_start: 10, source_end: 11 };
const b = { film_id: "b", source_start: 20, source_end: 23, framing: { fit: "fill", anchor_x: .25, anchor_y: .7, zoom: 1.2 } };
const recipe = { id: "whip-pan", duration: .4, direction: "down", easing: "linear", intensity: .8, softness: .03, rebound: .35, overscan: .1, cut_phase: .67 };

test("fast edit timing changes only recipe duration and supplies an explicit legal speed preset", () => {
  const before = JSON.stringify({ a, b, recipe });
  const result = helpers.fastEditTiming(a, b, recipe);
  assert.equal(result.available, true);
  assert.equal(result.reason, null);
  assert.deepEqual(plain(result.recipe), { ...recipe, duration: 8 / 30 });
  assert.deepEqual(plain(result.retime), { mode: "rush", speed: 2, span: 1, curve: "smooth", interpolation: "nearest" });
  assert.equal(transitions.validatePair(a, b, result.recipe, 12, result.retime), null);
  assert.equal(JSON.stringify({ a, b, recipe }), before, "Applying timing must not alter source windows, framing or the input recipe");
});

test("fast edit timing is available only for camera whip with both source selections", () => {
  for (const id of ["hard-cut", "crash-zoom", "luma-reveal"]) {
    const result = helpers.fastEditTiming(a, b, { ...recipe, id });
    assert.equal(result.available, false);
    assert.match(result.reason, /Camera whip/);
    assert.equal(result.retime, undefined);
  }
  for (const sources of [[null, b], [a, null], [null, null]]) {
    const result = helpers.fastEditTiming(...sources, recipe);
    assert.equal(result.available, false);
    assert.match(result.reason, /both clips/);
  }
  assert.equal(helpers.fastEditTiming(a, b, null).available, false);
});

test("short clips disable the preset without silently shrinking its one-second ramp", () => {
  for (const side of ["A", "B"]) {
    const sources = side === "A" ? [{ ...a, source_end: 10.99 }, b] : [a, { ...b, source_end: 20.99 }];
    const result = helpers.fastEditTiming(...sources, recipe);
    assert.equal(result.available, false);
    assert.match(result.reason, new RegExp(`Clip ${side} needs at least 1 second`));
    assert.equal(result.recipe, undefined);
  }
  assert.equal(helpers.fastEditTiming({ ...a, source_start: .1, source_end: 1.1 }, b, recipe).available, true);
});

test("invalid and overlong source windows and invalid framing stay unavailable", () => {
  for (const patch of [{ source_start: NaN }, { source_end: Infinity }, { source_start: -1 }, { source_end: 9 }, { source_end: 23 }, { framing: { ...b.framing, zoom: 3 } }]) {
    const result = helpers.fastEditTiming({ ...a, ...patch }, b, recipe);
    assert.equal(result.available, false);
    assert.ok(result.reason.length);
  }
});

test("preset results have independent state and the frontend names the revised camera whip", () => {
  const first = helpers.fastEditTiming(a, b, recipe);
  first.recipe.direction = "left";
  first.retime.speed = 4;
  const next = helpers.fastEditTiming(a, b, recipe);
  assert.equal(next.recipe.direction, "down");
  assert.equal(next.retime.speed, 2);
  assert.equal(transitions.recipeTitle("whip-pan"), "Camera whip");
});
