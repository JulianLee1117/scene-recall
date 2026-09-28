const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const helpers = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "directions.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: helpers });

test("direction customization retains source observations without inventing motion", () => {
  const result = helpers.buildBridgePrompt("whip", "  A moves right past a column; B continues right.  ",
    { energy: "restrained", direction: "right", anchor: "  Face at upper center  " });
  assert.match(result, /Camera travel is rightward/);
  assert.match(result, /minimal distortion/);
  assert.match(result, /Visual anchor to preserve across the transition: Face at upper center/);
  assert.ok(result.endsWith("A moves right past a column; B continues right."));
  assert.doesNotMatch(helpers.buildBridgePrompt("whip", "", { direction: "auto" }), /Camera travel is (left|right)ward/);
});

test("inapplicable direction and invalid or excessive customization are rejected", () => {
  for (const [id, options] of [["focus", { direction: "right" }], ["whip", { direction: "forward" }],
    ["flight", { direction: "right" }], ["scan", { energy: "neon" }], ["light", { anchor: "x".repeat(241) }]]) {
    assert.throws(() => helpers.buildBridgePrompt(id, "", options));
  }
  assert.throws(() => helpers.buildBridgePrompt("whip", "x".repeat(2001)));
  assert.throws(() => helpers.buildBridgePrompt("missing"));
});

test("every available direction fits the strictest hosted model prompt limit", () => {
  for (const starter of helpers.BRIDGE_PROMPTS) {
    for (const direction of starter.directions) {
      const prompt = helpers.buildBridgePrompt(starter.id, "x".repeat(2000), { energy: "bold", direction, anchor: "a".repeat(240) });
      assert.ok(prompt.length < 6000, starter.id);
      assert.equal(helpers.buildBridgePrompt(starter.id), starter.text);
    }
  }
});

test("spatial and material experiments remain labeled with specific review criteria", () => {
  for (const id of ["morph", "portal", "material", "scan"]) {
    const starter = helpers.BRIDGE_PROMPTS.find(item => item.id === id);
    assert.equal(starter.group, "experimental");
    assert.ok(starter.fit && starter.check);
  }
  assert.doesNotMatch(helpers.buildBridgePrompt("flight", "", { direction: "backward" }), /forward camera/);
});

test("camera flight describes a motivated height change without inventing a route for the source pair", () => {
  const notes = "A looks across the rooftops. Descend through the open gap between the buildings, reveal the street, and arrive beside the incoming car.";
  const prompt = helpers.buildBridgePrompt("flight", notes, { energy: "restrained", direction: "forward", anchor: "Street vanishing point" });
  assert.match(prompt, /change in camera height/);
  assert.match(prompt, /nearby surfaces passing faster than distant ones/);
  assert.match(prompt, /ease into its camera height, angle and final framing/);
  assert.ok(prompt.endsWith(notes), "the chosen route stays an explicit source observation");
  assert.doesNotMatch(helpers.buildBridgePrompt("flight"), /rooftops|buildings|street|car/,
    "the reusable starter does not invent a city route for an unrelated pair");
});

test("reverse camera flight preserves the requested reveal and does not impose a forward dive", () => {
  const notes = "Pull away from the person through the open doorway and rise into the elevated courtyard view.";
  const prompt = helpers.buildBridgePrompt("flight", notes, { energy: "balanced", direction: "backward" });
  assert.match(prompt, /Pull the camera backward/);
  assert.ok(prompt.endsWith(notes));
  assert.doesNotMatch(prompt, /camera forward|destination ahead|descend|dive downward|level out/i);
  assert.equal(helpers.BRIDGE_PROMPTS.filter((item) => item.id === "flight").length, 1,
    "both height changes use the existing flight family");
});
