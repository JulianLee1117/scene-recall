const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const lib = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "shotFilters.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: lib });

const plain = (value) => JSON.parse(JSON.stringify(value));
const facets = [
  { key: "size", label: "Size", values: [{ value: "close", label: "Close", count: 1 }, { value: "wide", label: "Wide", count: 1 }] },
  { key: "color", label: "Color", values: [{ value: "color", label: "Color", count: 1 }, { value: "bw", label: "B&W", count: 1 }] },
];

test("choosing a value replaces the facet's choice, and Any drops the facet", () => {
  const close = lib.chooseShotFilter({ color: ["bw"] }, "size", "close");
  assert.deepEqual(plain(close), { color: ["bw"], size: ["close"] });
  assert.deepEqual(plain(lib.chooseShotFilter(close, "size", "wide")), { color: ["bw"], size: ["wide"] });
  assert.deepEqual(plain(lib.chooseShotFilter(close, "size", "")), { color: ["bw"] });
  assert.deepEqual(plain(lib.clearShotFacet(close, "color")), { size: ["close"] });
});

test("filters compare by value and send only chosen facets", () => {
  assert.equal(lib.sameShotFilters({ size: ["close", "wide"] }, { size: ["wide", "close"], color: [] }), true);
  assert.equal(lib.sameShotFilters({ size: ["close"] }, {}), false);
  assert.equal(lib.hasShotFilters({ size: [] }), false);
  assert.equal(lib.shotFiltersPayload({ size: [] }), undefined);
  assert.deepEqual(plain(lib.shotFiltersPayload({ size: ["wide"], color: [] })), { size: ["wide"] });
});

test("chips name each active facet with its value labels, in menu order", () => {
  assert.deepEqual(plain(lib.activeShotFacets({ color: ["bw"], size: ["wide", "close"] }, facets)), [
    { key: "size", label: "Size", values: ["Close", "Wide"] },
    { key: "color", label: "Color", values: ["B&W"] },
  ]);
});
