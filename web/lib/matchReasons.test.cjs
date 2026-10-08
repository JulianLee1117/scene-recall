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
const table = (shot) => plain(lib.matchBreakdown(shot).rows).map((row) => [row.label, row.value, row.detail]);

test("a typed search always reports Picture, Text and Rerank, with ranks and what matched", () => {
  const shot = {
    caption: "Rows of beer bottles in a fridge.",
    matches: [{ clause_id: "main", facet: "all", rank: 1, evidence: { type: "text", view: "scene", text: "Seth finds a fridge stocked with beer." } }],
    debug: { final_score: 0.03, relevance: 0.97, channels: {
      img: { rank: 1, score: 0.3, distance: 0.7, source: "frame", matched_frame: { timestamp: 3843 } },
      txt: { rank: 12, score: 0.6, distance: 0.4, source: "scene", matched_text: { view: "scene", text: "Seth finds a fridge stocked with beer." } },
      rerank: { verdict: 0.781 },
    } },
  };
  assert.deepEqual(table(shot), [
    ["Picture", "#1", "frame 3843s"],
    ["Text", "#12", "Scene · Seth finds a fridge stocked with beer."],
    ["Rerank", "0.78", ""],
  ]);
  assert.equal(lib.matchBreakdown(shot).score, 0.97);
  assert.deepEqual(plain(lib.foundBy(shot)), ["Picture", "Scene"]);
});

test("a finder that did not return the scene still shows its row, marked unmatched", () => {
  const shot = {
    caption: "A couple kisses on a lawn.",
    matches: [{ clause_id: "main", facet: "all", rank: 4, evidence: { type: "text", view: "caption", text: "A couple kisses on a lawn." } }],
    debug: { final_score: 0.01, relevance: 0.5, channels: {
      txt: { rank: 38, score: 0.6, distance: 0.4, source: "caption", matched_text: { view: "caption", text: "A couple kisses on a lawn." } },
    } },
  };
  const rows = plain(lib.matchBreakdown(shot).rows);
  assert.deepEqual(rows.map((row) => [row.label, row.value, row.matched]), [["Picture", "–", false], ["Text", "#38", true], ["Rerank", "–", false]]);
  assert.equal(rows[1].detail, "Description (above)", "the caption shown above is not repeated");
  assert.equal(rows[2].detail, "not reranked (only the top 40 are)");
});

test("a recipe shows the description's finders and each category's own rank", () => {
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
  assert.deepEqual(table(shot), [
    ["Picture", "#140", ""],
    ["Text", "#39", "Story · Caleb clinks his beer bottle."],
    ["Rerank", "–", "not reranked (only the top 40 are)"],
    ["Words", "#25", "Dialogue · Cheers."],
    ["Look", "#2", "frame 90s"],
  ]);
  assert.equal(lib.matchBreakdown(shot).score, undefined);
});

test("without ranking detail, each clause still reports its rank; shot details and mood read as words", () => {
  const shot = { matches: [{ clause_id: "main", facet: "all", rank: 7, evidence: { type: "text", view: "facets", text: "framing: close_up; time of day: dawn_dusk" } }] };
  assert.deepEqual(table(shot), [["Your description", "#7", "Shot details · Shot: close-up · Time: dawn or dusk"]]);
  assert.equal(lib.readableEvidence("mood", "emotion: mild; scene tone: tense, social"), "Emotion: mild · Scene tone: tense, social");
  assert.equal(lib.readableEvidence("story", "Caleb clinks his beer bottle."), "Caleb clinks his beer bottle.");
});
