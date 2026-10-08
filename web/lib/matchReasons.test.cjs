const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const lib = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "matchReasons.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: lib, require: () => ({ formatTime: (value) => `${value}s` }) });

const plain = (value) => JSON.parse(JSON.stringify(value));

test("a typed search explains each finder: the text view it matched, the picture and the overall fit", () => {
  const shot = {
    caption: "Rows of beer bottles in a fridge.",
    matched_line: null,
    matches: [{ clause_id: "main", facet: "all", rank: 1, evidence: { type: "text", view: "scene", text: "Seth finds a fridge stocked with beer." } }],
    debug: { final_score: 0.03, relevance: 0.97, channels: {
      img: { rank: 1, score: 0.3, distance: 0.7, source: "frame", matched_frame: { timestamp: 3843 } },
      txt: { rank: 12, score: 0.6, distance: 0.4, source: "scene", matched_text: { view: "scene", text: "Seth finds a fridge stocked with beer." } },
      rerank: { verdict: 0.78 },
    } },
  };
  const breakdown = plain(lib.matchBreakdown(shot));
  assert.deepEqual(breakdown.fit, { strength: 5, word: "Strong fit" });
  assert.deepEqual(breakdown.rows.map((row) => [row.label, row.strength, row.detail]), [
    ["Picture", 5, "Closest frame at 3843s"],
    ["Scene", 3, "Seth finds a fridge stocked with beer."],
  ]);
  assert.equal(breakdown.rows[1].note, "Ranked 12th by Scene");
  assert.deepEqual(plain(lib.foundBy(shot)), ["Picture", "Scene"]);
});

test("a recipe explains the description by finder and each reference clause by its own rank", () => {
  const shot = {
    matches: [
      { clause_id: "main", facet: "all", rank: 11, evidence: { type: "text", view: "story", text: "Caleb clinks his beer bottle." } },
      { clause_id: "words", facet: "words", rank: 25, evidence: { type: "text", view: "dialogue", text: "Cheers." } },
      { clause_id: "look", facet: "look", rank: 2, evidence: { type: "frame", frame_index: 1, timestamp: 90 } },
    ],
    debug: { final_score: 0.02, clauses: { main: { channels: {
      txt: { rank: 39, score: 0.6, distance: 0.4, source: "story", matched_text: { view: "story", text: "Caleb clinks his beer bottle." } },
      img: { rank: 140, score: 0.2, distance: 0.8, source: "frame" },
    } } } },
  };
  const { fit, rows } = plain(lib.matchBreakdown(shot));
  assert.equal(fit, undefined, "no fit verdict was reported for this recipe");
  assert.deepEqual(rows.map((row) => [row.label, row.strength]), [["Look", 5], ["Words", 3], ["Story", 2], ["Picture", 1]]);
  assert.equal(rows.find((row) => row.label === "Words").detail, "Cheers.");
  assert.equal(rows.find((row) => row.label === "Look").detail, "Closest frame at 90s");
});

test("without ranking detail, each clause still explains itself; shot details read as words", () => {
  const shot = { matches: [{ clause_id: "main", facet: "all", rank: 7, evidence: { type: "text", view: "facets", text: "framing: close_up; time of day: dawn_dusk" } }] };
  assert.deepEqual(plain(lib.matchBreakdown(shot).rows), [{
    label: "Your description", strength: 4, note: "Ranked 7th by Your description", detail: "Shot: close-up · Time: dawn or dusk",
  }]);
});

test("ordinals and fit words read naturally", () => {
  assert.deepEqual([1, 2, 3, 4, 11, 12, 13, 21, 22, 101].map(lib.ordinal), ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "101st"]);
  const fit = (verdict) => lib.matchBreakdown({ matches: [{ clause_id: "main", facet: "all", rank: 1 }], debug: { final_score: 0, channels: { rerank: { verdict } } } }).fit.word;
  assert.deepEqual([0.8, 0.6, 0.45, 0.25, 0.05].map(fit), ["Strong fit", "Good fit", "Fair fit", "Loose fit", "Weak fit"]);
});

test("mood evidence reads as words like shot details", () => {
  assert.equal(lib.readableEvidence("mood", "emotion: mild; scene tone: tense, social"), "Emotion: mild · Scene tone: tense, social");
  assert.equal(lib.readableEvidence("story", "Caleb clinks his beer bottle."), "Caleb clinks his beer bottle.");
});
