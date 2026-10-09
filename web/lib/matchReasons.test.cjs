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
const table = (shot, columns) => plain(lib.matchBreakdown(shot, columns).rows)
  .map((row) => (row.terms ? [row.label, row.value, row.terms, row.detail] : [row.label, row.value, row.detail]));

const typed = (channels, extra = {}) => ({
  caption: "A caption.",
  matches: [{ clause_id: "main", facet: "all", rank: 1 }],
  debug: { final_score: 0.03, relevance: 0.9, depth: 600, channels },
  ...extra,
});

test("every card in a search lists the same finders in the same fixed order", () => {
  const picture = typed({ img: { rank: 2, score: 0.3, distance: 0.7, matched_frame: { timestamp: 90 } }, rerank: { verdict: 0.52 } });
  const words = typed(
    { txt: { rank: 18, score: 0.6, distance: 0.4, source: "ocr", matched_text: { view: "ocr", text: "LOVE" } }, lex: { rank: 3, score: 1, distance: null, terms: ["love", "neon"], fields: ["caption", "dialogue"] }, quote: { rank: 1, score: 1, distance: null } },
    { matched_line: { t_start: 4, t_end: 5, text: "love." } },
  );
  const columns = plain(lib.matchColumns([picture, words]));
  assert.deepEqual(columns, ["img", "txt", "lex", "quote", "rerank"]);
  assert.deepEqual(table(picture, columns), [
    ["Visual", "#2", "frame 90s"],
    ["Semantic", "–", "not in its top 600"],
    ["Lexical", "–", "fewer than two of your words in its description or dialogue"],
    ["Quote", "–", "no matching spoken line"],
    ["Rerank", "0.52", ""],
  ]);
  assert.deepEqual(table(words, columns), [
    ["Visual", "–", "not in its top 600"],
    ["Semantic", "#18", "On-screen text · LOVE"],
    ["Lexical", "#3", ["love", "neon"], "in description and dialogue"],
    ["Quote", "#1", "“love.”"],
    ["Rerank", "–", "not in the top 40 it scores"],
  ]);
  assert.equal(lib.matchBreakdown(picture, columns).score, 0.9);
});

test("a recipe adds a row per category after the description's finders", () => {
  const shot = {
    matches: [
      { clause_id: "main", facet: "all", rank: 11 },
      { clause_id: "words", facet: "words", rank: 25, evidence: { type: "text", view: "dialogue", text: "Cheers." } },
      { clause_id: "look", facet: "look", rank: 2, evidence: { type: "frame", frame_index: 1, timestamp: 90 } },
    ],
    debug: { final_score: 0.02, depth: 600, clauses: { main: { depth: 600, channels: {
      txt: { rank: 39, score: 0.6, distance: 0.4, source: "story", matched_text: { view: "story", text: "Caleb clinks his beer bottle." } },
    } } } },
  };
  assert.deepEqual(table(shot, plain(lib.matchColumns([shot]))), [
    ["Visual", "–", "not in its top 600"],
    ["Semantic", "#39", "Story · Caleb clinks his beer bottle."],
    ["Words", "#25", "Dialogue · Cheers."],
    ["Look", "#2", "frame 90s"],
  ]);
});

test("the caption shown above is not repeated, and shot details and mood read as words", () => {
  const shot = typed({ txt: { rank: 38, score: 0.6, distance: 0.4, source: "caption", matched_text: { view: "caption", text: "A caption." } } });
  assert.equal(table(shot)[1][2], "Description (above)");
  assert.equal(lib.readableEvidence("facets", "framing: close_up; time of day: dawn_dusk"), "Shot: close-up · Time: dawn or dusk");
  assert.equal(lib.readableEvidence("mood", "emotion: mild; scene tone: tense, social"), "Emotion: mild · Scene tone: tense, social");
});

test("the hover label names the finders that ranked the scene in their top 30", () => {
  const shot = typed({ img: { rank: 1, score: 0.3, distance: 0.7 }, txt: { rank: 12, score: 0.6, distance: 0.4, source: "scene", matched_text: { view: "scene", text: "x" } } });
  assert.deepEqual(plain(lib.foundBy(shot)), ["Visual", "Scene"]);
});
