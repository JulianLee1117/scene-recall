const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const lib = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "filmFilters.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: lib });

const plain = (value) => JSON.parse(JSON.stringify(value));
const film = (id, year, genres, directors = []) => ({ film_id: id, title: id, filename: `${id}.mkv`, status: "indexed", year, genres, directors });

// The library's real decade spread: 15 films before 1960, then 10+ per decade.
const spread = { 1920: 1, 1930: 1, 1940: 3, 1950: 10, 1960: 12, 1990: 28, 2010: 47 };
const spreadYears = Object.entries(spread).flatMap(([decade, count]) => Array.from({ length: count }, () => Number(decade) + 5));

test("sparse early decades share one era; a lone decade or a tiny library stays as is", () => {
  assert.equal(lib.earlyEraBoundary(spreadYears), 1960);
  assert.equal(lib.eraLabel(1948, 1960), "Before 1960");
  assert.equal(lib.eraLabel(1960, 1960), "1960s");
  assert.equal(lib.earlyEraBoundary([...Array(12).fill(1925), ...Array(12).fill(1995)]), null);
  assert.equal(lib.earlyEraBoundary([1995, 2003]), null);
});

const films = lib.filterableFilms([
  film("heat", 1995, ["Crime", "Drama"], ["Michael Mann"]),
  film("collateral", 2004, ["Crime", "Thriller"], ["Michael Mann"]),
  film("elf", 2003, ["Comedy", "Family"], ["Jon Favreau"]),
  film("chungking", 1994, ["Comedy", "Romance"], ["Wong Kar-wai"]),
  { ...film("unindexed", 2001, ["Crime"]), status: "not_indexed", film_id: null },
]);

test("values within a facet are alternatives; facets combine", () => {
  const ids = (filters) => lib.filterFilms(films, filters).map((item) => item.id);
  assert.deepEqual(ids({ genre: ["Crime"] }), ["heat", "collateral"]);
  assert.deepEqual(ids({ genre: ["Crime", "Comedy"] }), ["heat", "collateral", "elf", "chungking"]);
  assert.deepEqual(ids({ genre: ["Comedy"], era: ["1990s"] }), ["chungking"]);
});

test("each facet counts films passing the other facets, keeps every value, and orders sensibly", () => {
  const filters = { era: ["2000s"] };
  assert.deepEqual(plain(lib.facetOptions(films, filters, "era")), [
    { value: "1990s", count: 2, selected: false },
    { value: "2000s", count: 2, selected: true },
  ]);
  const genres = plain(lib.facetOptions(films, filters, "genre"));
  assert.deepEqual(genres.map((option) => option.value), ["Comedy", "Crime", "Drama", "Family", "Romance", "Thriller"]);
  assert.deepEqual(genres.find((option) => option.value === "Romance"), { value: "Romance", count: 0, selected: false });
  // Directors with more films come first.
  assert.deepEqual(plain(lib.facetOptions(films, {}, "director")).map((option) => option.value), ["Michael Mann", "Jon Favreau", "Wong Kar-wai"]);
});

test("toggling adds and removes values, dropping empty facets", () => {
  const once = lib.toggleFilter({}, "genre", "Crime");
  assert.deepEqual(plain(once), { genre: ["Crime"] });
  assert.deepEqual(plain(lib.toggleFilter(once, "genre", "Crime")), {});
  // One summary per section, in panel order; clearing drops a whole section.
  const both = { director: ["Wong Kar-wai"], era: ["1990s", "2000s"] };
  assert.deepEqual(plain(lib.activeFacets(both)).map(({ key, values }) => [key, values]), [
    ["era", ["1990s", "2000s"]],
    ["director", ["Wong Kar-wai"]],
  ]);
  assert.deepEqual(plain(lib.clearFacet(both, "era")), { director: ["Wong Kar-wai"] });
});

test("the search scope is the named movies or the library, narrowed by the filters", () => {
  assert.deepEqual(plain(lib.narrowScope([], films, {})), { filmIds: [], excludesAll: false });
  assert.deepEqual(plain(lib.narrowScope([], films, { genre: ["Crime"] })), { filmIds: ["heat", "collateral"], excludesAll: false });
  assert.deepEqual(plain(lib.narrowScope(["elf", "heat"], films, { genre: ["Crime"] })), { filmIds: ["heat"], excludesAll: false });
  assert.deepEqual(plain(lib.narrowScope(["elf"], films, { genre: ["Crime"] })), { filmIds: [], excludesAll: true });
  // A filter every movie passes needs no film list.
  assert.deepEqual(plain(lib.narrowScope([], films, { era: ["1990s", "2000s"] })), { filmIds: [], excludesAll: false });
});

test("selections compare by value, whatever the order of their choices", () => {
  assert.equal(lib.sameFilters({ genre: ["Crime", "Drama"] }, { genre: ["Drama", "Crime"] }), true);
  assert.equal(lib.sameFilters({ genre: ["Crime"] }, { genre: ["Crime"], era: [] }), true);
  assert.equal(lib.sameFilters({ genre: ["Crime"] }, { era: ["1990s"] }), false);
  assert.equal(lib.sameSelection({ filters: {}, filmIds: ["a", "b"] }, { filters: {}, filmIds: ["b", "a"] }), true);
  assert.equal(lib.sameSelection({ filters: {}, filmIds: ["a"] }, { filters: {}, filmIds: [] }), false);
});
