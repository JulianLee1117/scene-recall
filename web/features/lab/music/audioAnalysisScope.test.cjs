const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

function load(name, dependencies = {}) {
  const exports = {};
  const source = ts.transpileModule(fs.readFileSync(path.join(__dirname, name), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(source, { exports, require: (name) => dependencies[name] });
  return exports;
}
const scope = load("audioAnalysisScope.ts");
const { songMeaning } = load("songMeaning.ts", { "./audioAnalysisScope": scope });
const { musicCues } = load("musicCues.ts", { "./audioAnalysisScope": scope });

function aggregate() {
  const passage = { start: 0, end: 600 };
  const parts = Array.from({ length: 7 }, (_, index) => {
    const passage = { start: index * 600 / 7, end: (index + 1) * 600 / 7 };
    const profile = { track: "song", passage, interpretation_id: `part-${index}` };
    return { passage, analysis: { provenance: { track: "song", passage },
      events_provenance: { ...profile, source: "ai-observed" },
      song_meaning_provenance: { ...profile, source: "ai-heard-paraphrase" } } };
  });
  const manifest = parts.map((part, index) => ({ passage: part.passage, interpretation_id: `part-${index}` }));
  const profile = { track: "song", passage, parts: manifest, aggregation_contract: "bounded-audio-parts-v1" };
  return { track: { id: "song", duration: 600 }, passage,
    analysis: { parts, provenance: { track: "song", passage, contract: "bounded-audio-parts-v1", parts: manifest },
      events_provenance: { ...profile, source: "ai-observed" },
      song_meaning_provenance: { ...profile, source: "ai-heard-paraphrase" },
      events: Array.from({ length: 224 }, (_, index) => ({ id: `event-${index}`, start: index * 2.5, end: index * 2.5 + 1,
        label: `Observation ${index}`, confidence: "medium" })),
      song_meaning: { vocal_status: "partly_understood", summary: "s".repeat(8500), uncertainty: "u".repeat(4300),
        themes: Array.from({ length: 42 }, (_, index) => `Theme ${index}`),
        cues: Array.from({ length: 56 }, (_, index) => ({ start: index * 10, end: index * 10 + 5,
          paraphrase: `Heard meaning ${index}`, confidence: "medium" })) } } };
}

test("full-song observations and meaning retain their later source times", () => {
  const document = aggregate();
  assert.equal(scope.audioAnalysisPartCount(document, document.analysis.events_provenance), 7);
  assert.equal(songMeaning(document), document.analysis.song_meaning);
  const cues = musicCues(document);
  assert.equal(cues.length, 224);
  assert.equal(cues.at(-1).start, 557.5);
  assert.equal(songMeaning(document).cues.at(-1).start, 550);
});

test("malformed or stale aggregate scopes cannot unlock larger evidence limits", () => {
  for (const mutate of [
    (d) => { delete d.analysis.parts; },
    (d) => { d.analysis.provenance.contract = "unknown"; },
    (d) => { d.analysis.parts = []; },
    (d) => { d.analysis.parts.push(d.analysis.parts[0]); },
    (d) => { d.analysis.parts[1].passage = { start: 1, end: 80 }; },
    (d) => { d.analysis.parts[0].analysis.provenance.track = "stale"; },
    (d) => { d.analysis.parts[0].analysis.parts = []; },
    (d) => { d.analysis.parts[0].analysis.events_provenance.interpretation_id = "stale";
                d.analysis.parts[0].analysis.song_meaning_provenance.interpretation_id = "stale"; },
    (d) => { d.analysis.events_provenance.parts = [];
                d.analysis.song_meaning_provenance.parts = []; },
    (d) => { d.analysis.events_provenance.aggregation_contract = "guessed";
                d.analysis.song_meaning_provenance.aggregation_contract = "guessed"; },
  ]) {
    const document = aggregate(); mutate(document);
    assert.equal(songMeaning(document), null);
    assert.equal(musicCues(document).length, 0);
  }
});

test("ordinary single-passage packets retain the short schema caps", () => {
  const document = aggregate();
  delete document.analysis.parts;
  delete document.analysis.provenance.contract;
  delete document.analysis.events_provenance.aggregation_contract;
  delete document.analysis.song_meaning_provenance.aggregation_contract;
  assert.equal(scope.audioAnalysisPartCount(document, document.analysis.events_provenance), 1);
  assert.equal(musicCues(document).length, 32);
  assert.equal(songMeaning(document), null);
  Object.assign(document.analysis.song_meaning, { summary: "A supported meaning.", uncertainty: "",
    cues: document.analysis.song_meaning.cues.slice(0, 8), themes: document.analysis.song_meaning.themes.slice(0, 6) });
  assert.equal(songMeaning(document), document.analysis.song_meaning);
});

test("aggregate unclear vocals still cannot establish invented themes", () => {
  const document = aggregate();
  document.analysis.song_meaning.vocal_status = "unclear";
  assert.equal(songMeaning(document), null);
  Object.assign(document.analysis.song_meaning, { themes: [], cues: [] });
  assert.equal(songMeaning(document), document.analysis.song_meaning);
});
