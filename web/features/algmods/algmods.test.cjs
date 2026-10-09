const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const helpers = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "algmods.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: helpers });

const dots = { kind: "dots", style: "vivid", density: 3000, radius_frac: .0075, size_jitter: .25, bright_boost: .4, paint_floor: .3, spacing: 0, grow_in: 5, shrink_out: 4, grace: 4, fill: true, protect_subject: false };
const catalog = {
  version: "t", limits: { min_window_seconds: .2, max_window_seconds: 12, fps: 30, width: 720, height: 1280 },
  treatments: [
    { id: "dots", name: "Painted dots", description: "", defaults: dots,
      styles: [{ id: "vivid", name: "Vivid", description: "", defaults: dots }, { id: "pastel", name: "Pastel", description: "", defaults: { ...dots, style: "pastel", size_jitter: 0, bright_boost: 0 } }],
      controls: [{ key: "density", label: "Density", type: "range", advanced: false }, { key: "protect_subject", label: "Keep the subject real", type: "toggle", advanced: false }] },
    { id: "stripes", name: "Time stripes", description: "", defaults: { kind: "stripes", count: 48, direction: "horizontal", whole_frame: false },
      controls: [{ key: "count", label: "Bands", type: "range", advanced: false }, { key: "direction", label: "Bands", type: "select", advanced: false, options: [{ value: "horizontal", label: "Across" }, { value: "fan", label: "Fan from centre" }] }] },
  ],
};

test("a search result becomes a three-second window around its matched frame, inside the shot", () => {
  const window = helpers.sourceFromResult({ film_id: "f", unit_id: "u", film_title: "Film", t_start: 10, t_end: 20, matched_frame_timestamp: 19.5 });
  assert.equal(window.source_end, 20);
  assert.equal(window.source_start, 17);
  const short = helpers.sourceFromResult({ film_id: "f", unit_id: "u", t_start: 10, t_end: 11, matched_frame_timestamp: 10.5 });
  assert.deepEqual([short.source_start, short.source_end], [10, 11]);
});

test("window validation follows the catalog limits", () => {
  assert.match(helpers.validateWindow(null, catalog.limits), /Choose/);
  assert.equal(helpers.validateWindow({ film_id: "f", title: "", source_start: 1, source_end: 4 }, catalog.limits), "");
  assert.match(helpers.validateWindow({ film_id: "f", title: "", source_start: 1, source_end: 14 }, catalog.limits), /at most 12/);
  assert.match(helpers.validateWindow({ film_id: "f", title: "", source_start: 1, source_end: 1.1 }, catalog.limits), /at least/);
});

test("the variant summary names the treatment and only what differs from its defaults", () => {
  assert.equal(helpers.treatmentSummary(dots, catalog), "painted dots · vivid");
  assert.equal(helpers.treatmentSummary({ ...dots, style: "pastel", size_jitter: 0, bright_boost: 0, density: 4500, protect_subject: true }, catalog), "painted dots · pastel · density 4500 · keep the subject real on");
  assert.equal(helpers.treatmentSummary({ kind: "stripes", count: 48, direction: "fan", whole_frame: false }, catalog), "time stripes · bands fan from centre");
});

test("a treatment's baseline is its style's defaults for dots and its own defaults otherwise", () => {
  assert.equal(helpers.baseline(catalog, { ...dots, style: "pastel" }).size_jitter, 0);
  assert.equal(helpers.baseline(catalog, { kind: "stripes" }).count, 48);
  assert.equal(helpers.baseline(catalog, { kind: "smear" }), null);
});
