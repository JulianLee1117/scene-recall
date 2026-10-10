const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const compile = (file) => ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

function setup() {
  const stored = new Map();
  const window = { localStorage: { getItem: (key) => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value) } };
  const look = {};
  vm.runInNewContext(compile("sceneLook.ts"), { exports: look, window });
  return { look, stored };
}

const pixels = (...colours) => colours.flatMap(([r, g, b]) => [r, g, b, 255]);

test("a picture's look: its hue weighted by colour, how colourful it is, and how light", () => {
  const { look } = setup();
  const red = look.lookFromPixels(pixels([255, 0, 0], [255, 0, 0]));
  assert.ok(red.hue < 1 || red.hue > 359, `red sits at the top of the wheel, got ${red.hue}`);
  assert.equal(red.chroma, 1);
  assert.equal(red.lightness, 0.5);

  const grey = look.lookFromPixels(pixels([40, 40, 40], [200, 200, 200]));
  assert.equal(grey.chroma, 0, "black and white has no colour");
  assert.ok(Math.abs(grey.lightness - 120 / 255) < 1e-9);
  assert.ok(grey.chroma < look.GREY_CHROMA, "and sorts with the greys");

  const night = look.lookFromPixels(pixels([0, 0, 120], [60, 60, 60], [60, 60, 60]));
  assert.ok(Math.abs(night.hue - 240) < 1, "the grey pixels do not pull the hue off blue");
  assert.ok(night.chroma > look.GREY_CHROMA, "a dark blue scene still counts as colour");

  assert.deepEqual({ ...look.lookFromPixels([]) }, { hue: 0, chroma: 0, lightness: 0 }, "no pixels: no look");
});

test("looks are remembered by scene, rounded, and read back", () => {
  const { look, stored } = setup();
  assert.equal(look.loadLooks().size, 0);
  look.saveLooks(new Map([["unit-1", { hue: 33.6, chroma: 0.12345, lightness: 0.5 }]]));
  look.saveLooks(new Map([["unit-2", { hue: 240, chroma: 0, lightness: 0.9 }]]));
  const back = look.loadLooks();
  assert.deepEqual(JSON.parse(JSON.stringify([...back])), [
    ["unit-1", { hue: 34, chroma: 0.123, lightness: 0.5 }],
    ["unit-2", { hue: 240, chroma: 0, lightness: 0.9 }],
  ]);
  stored.set("scene-recall.looks", "{not json");
  assert.equal(look.loadLooks().size, 0, "a corrupt store starts over");
});
