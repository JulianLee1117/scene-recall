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
    ["Lexical", "#3", ["love", "neon"], "in picture and dialogue"],
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
    ["Semantic", "#39", "Shot · Caleb clinks his beer bottle."],
    ["Words", "#25", "Dialogue · Cheers."],
    ["Look", "#2", "frame 90s"],
  ]);
});

test("mixed-recipe Quote details use main-query provenance and never another clause's global line", () => {
  const shot = {
    matched_line: { text: "Wrong Words line." },
    matches: [
      { clause_id: "main", facet: "all", rank: 5, evidence: { type: "text", view: "dialogue", source: "quote", text: "Right main-query line.", score: 1 } },
      { clause_id: "words", facet: "words", rank: 1, evidence: { type: "text", view: "dialogue", source: "quote", text: "Wrong Words line." } },
    ],
    debug: { clauses: { main: { channels: { quote: { rank: 2 } } } } },
  };
  assert.deepEqual(table(shot, ["quote"]), [["Quote", "#2", "“Right main-query line.”"]]);
  shot.matches[0].evidence = { type: "text", view: "caption", text: "A doorway." };
  assert.deepEqual(table(shot, ["quote"]), [["Quote", "#2", ""]], "quote participation remains visible without inventing its words");
});

test("the caption shown above is not repeated, and shot details and mood read as words", () => {
  const shot = typed({ txt: { rank: 38, score: 0.6, distance: 0.4, source: "caption", matched_text: { view: "caption", text: "A caption." } } });
  assert.equal(table(shot)[1][2], "Picture (above)");
  assert.equal(lib.readableEvidence("facets", "framing: close_up; time of day: dawn_dusk"), "Size: close-up · Time: dawn or dusk");
  assert.equal(lib.readableEvidence("mood", "emotion: mild; scene tone: tense, social"), "Emotion: mild · Scene tone: tense, social");
  assert.equal(lib.readableEvidence("scene", "You Talkin' to Me?. Back home. Wait!."), "You Talkin' to Me? Back home. Wait!");
});

test("ordinary hover stays on shot context when ranks offer only mood or incidental quote overlap", () => {
  const shot = typed({
    txt: { rank: 1, source: "mood", matched_text: { view: "mood", text: "tense" } },
    quote: { rank: 1 },
  }, { action: "He raises an axe.", matched_text: "tense", matched_text_view: "mood", matched_line: { text: "Cheers.", score: 0.6 },
    matches: [{ clause_id: "main", facet: "all", rank: 1, evidence: { type: "text", view: "mood", text: "tense" } }],
  });
  assert.deepEqual(plain(lib.hoverEvidence(shot)), { text: "He raises an axe." });
  assert.deepEqual(plain(lib.hoverEvidence({ ...shot, action: "  " })), { text: "A caption." });
  assert.deepEqual(plain(lib.hoverEvidence({ caption: "A still lake." })), { text: "A still lake." }, "saved scenes need no search evidence");
  assert.equal(lib.hoverEvidence({ action: " ", caption: " " }), null);
});

test("ordinary search exposes its strong quote and keeps text, time and score together", () => {
  const evidence = { type: "text", view: "dialogue", text: " You talking to me? ", source: "quote", score: 0.8, t_start: 42.5, t_end: 46 };
  const shot = { action: "He looks in the mirror.", matches: [{ clause_id: "main", facet: "all", rank: 5, evidence }] };
  assert.deepEqual(plain(lib.hoverEvidence(shot)), { kind: "Spoken", text: "“You talking to me?”" });
  assert.deepEqual(plain(lib.matchedWordsEvidence(shot)), { kind: "Spoken", text: "You talking to me?", source: "quote", score: 0.8, t_start: 42.5, t_end: 46 });
});

test("ordinary search keeps a backend-selected partial quote when spoken words explain the result", () => {
  const evidence = {
    type: "text", view: "dialogue", source: "quote", score: 0.7143,
    text: "And I am not going to stand here and see that thing cut open",
    t_start: 2155.822, t_end: 2159.534,
  };
  const shot = {
    action: "Mayor Vaughn refuses an autopsy.",
    matches: [{ clause_id: "main", facet: "all", rank: 18, evidence }],
  };
  assert.deepEqual(plain(lib.hoverEvidence(shot)), { kind: "Spoken", text: `“${evidence.text}”` });
  assert.deepEqual(plain(lib.matchedWordsEvidence(shot)), {
    kind: "Spoken", text: evidence.text, source: "quote", score: 0.7143,
    t_start: 2155.822, t_end: 2159.534,
  }, "hover and playback retain the selected passage without consulting debug ranks");
});

test("ordinary search exposes selected semantic dialogue or on-screen text without a Words filter", () => {
  for (const [view, kind, text] of [["dialogue", "Spoken", "Please don't leave me."], ["ocr", "On screen", "NO VACANCY"]]) {
    const shot = {
      action: "He waits.",
      matched_line: { text: "Unrelated weak quote.", score: 0.55, t_start: 7 },
      matches: [{ clause_id: "main", facet: "all", rank: 25, evidence: { type: "text", source: "semantic", view, text } }],
    };
    assert.deepEqual(plain(lib.hoverEvidence(shot)), { kind, text: `“${text}”` });
    assert.deepEqual(plain(lib.matchedWordsEvidence(shot)), { kind, text, source: "semantic" }, "semantic text has no invented quote timestamp");
  }
});

test("Words hover uses its own spoken or on-screen match in a mixed recipe", () => {
  for (const [view, kind, text] of [["dialogue", "Spoken", "Not here."], ["ocr", "On screen", "EXIT"]]) {
    const shot = {
      action: "He runs.", matched_line: { text: "Unrelated main-query dialogue." },
      matched_text: "Unrelated main-query description.", matched_text_view: "story",
      matches: [
        { clause_id: "main", facet: "all", rank: 1, evidence: { type: "text", view: "dialogue", text: "Wrong line." } },
        { clause_id: "words", facet: "words", rank: 90, evidence: { type: "text", view, text } },
      ],
    };
    assert.deepEqual(plain(lib.hoverEvidence(shot)), { kind, text: `“${text}”` });
  }
});

test("Words without usable text keeps the shot description and never borrows another clause's words", () => {
  for (const evidence of [undefined, { type: "text", view: "dialogue", text: " " }, { type: "text", view: "story", text: "Unrelated story." }, { type: "frame", frame_index: 0 }]) {
    assert.deepEqual(plain(lib.hoverEvidence({
      action: "He runs.", matched_line: { text: "Unrelated quote." }, matched_text: "EXIT", matched_text_view: "ocr",
      matches: [{ clause_id: "words", facet: "words", rank: 1, evidence }],
    })), { text: "He runs." });
  }
});

test("a main query can explain a result when its Words refinement has no usable match", () => {
  const shot = {
    action: "He waits.",
    matches: [
      { clause_id: "words", facet: "words", rank: 1 },
      { clause_id: "main", facet: "all", rank: 12, evidence: { type: "text", view: "ocr", text: "EXIT", source: "semantic" } },
    ],
  };
  assert.deepEqual(plain(lib.hoverEvidence(shot)), { kind: "On screen", text: "“EXIT”" });
});

test("mixed recipes never borrow globally selected words or show another category's snippet", () => {
  const shot = {
    action: "She opens the door.",
    matched_line: { text: "Unrelated quote.", t_start: 300, t_end: 302, score: 1 },
    matched_text: "WRONG EXIT", matched_text_view: "ocr",
    matches: [
      { clause_id: "main", facet: "all", rank: 20, evidence: { type: "text", view: "caption", text: "The doorway." } },
      { clause_id: "scene", facet: "scene", rank: 1, evidence: { type: "text", view: "dialogue", text: "Don't show this." } },
    ],
  };
  assert.deepEqual(plain(lib.hoverEvidence(shot)), { text: "She opens the door." });
  assert.equal(lib.matchedWordsEvidence(shot), null);
});

test("standalone results retain supported words without relying on diagnostic ranks", () => {
  assert.deepEqual(plain(lib.hoverEvidence({ matched_line: { text: "Come with me.", score: 0.9, t_start: 0, t_end: 2 } })), { kind: "Spoken", text: "“Come with me.”" });
  assert.equal(lib.matchedWordsEvidence({ matched_line: { text: "Come with me.", score: 0.79 } }), null);
  assert.deepEqual(plain(lib.hoverEvidence({ matched_text_view: "ocr", matched_text: "EXIT" })), { kind: "On screen", text: "“EXIT”" });
});
