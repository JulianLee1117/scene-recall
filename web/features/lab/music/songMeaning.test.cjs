const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const compile = (name) => ts.transpileModule(fs.readFileSync(path.join(__dirname, name), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const meaning = {};
const scope = {};
vm.runInNewContext(compile("audioAnalysisScope.ts"), { exports: scope });
vm.runInNewContext(compile("songMeaning.ts"), { exports: meaning, require: () => scope });
const component = {};
const jsx = (type, props) => ({ type, props });
vm.runInNewContext(compile("MusicAnalysisDetails.tsx"), {
  exports: component,
  require(name) {
    if (name === "react/jsx-runtime") return { jsx, jsxs: jsx, Fragment: "fragment" };
    if (name === "./songMeaning") return meaning;
    if (name === "@/lib/lab") return { seconds: (value) => `time:${value}` };
    if (name.endsWith(".css")) return { default: new Proxy({}, { get: (_, key) => key }) };
    return { default: name };
  },
});
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);

function document() {
  const passage = { start: 20, end: 50 };
  return {
    track: { id: "song", duration: 90 }, passage,
    analysis: {
      summary: "The instrumentation gradually becomes brighter.",
      provenance: { track: "song", passage: { ...passage } },
      song_meaning_provenance: { track: "song", passage: { ...passage }, source: "ai-heard-paraphrase" },
      song_meaning: {
        vocal_status: "partly_understood", summary: "The speaker anticipates a separation.",
        themes: ["fear of separation"], uncertainty: "The final phrase is obscured.",
        cues: [{ start: 22, end: 25, paraphrase: "The speaker asks the other person to stay.", confidence: "medium" }],
      },
    },
  };
}

test("heard meaning requires both provenance records to match the current song passage", () => {
  const current = document();
  assert.equal(meaning.songMeaning(current), current.analysis.song_meaning);
  for (const mutate of [
    (d) => { d.track = null; },
    (d) => { d.track.id = "another-song"; },
    (d) => { d.passage.end = 51; },
    (d) => { d.analysis.provenance.track = "another-song"; },
    (d) => { d.analysis.song_meaning_provenance.passage.start = 0; },
    (d) => { d.analysis.song_meaning_provenance.source = "imagined-from-mood"; },
    (d) => { d.analysis.provenance = null; },
    (d) => { d.analysis.song_meaning_provenance = null; },
  ]) {
    const value = document(); mutate(value);
    assert.equal(meaning.songMeaning(value), null);
  }
});

test("legacy analysis remains readable without synthesizing a vocal meaning from atmosphere", () => {
  const legacy = document();
  delete legacy.analysis.song_meaning;
  delete legacy.analysis.song_meaning_provenance;
  assert.equal(meaning.songMeaning(legacy), null);
  const tree = component.default({ document: legacy, working: false, onAnalyze() {} });
  assert.match(text(tree), /no separate vocal meaning/);
  assert.match(text(tree), /instrumentation gradually becomes brighter/);
  assert.doesNotMatch(text(tree), /Song meaning|What the model heard|fear of separation/);
  assert.equal(nodes(tree).find((node) => node.type === "details").props.open, true);
});

test("malformed or out-of-passage packets cannot appear as heard meaning", () => {
  for (const mutate of [
    (m) => { m.vocal_status = "certain"; },
    (m) => { m.summary = " "; },
    (m) => { m.summary = "x".repeat(1201); },
    (m) => { m.uncertainty = null; },
    (m) => { m.uncertainty = "x".repeat(601); },
    (m) => { m.themes = "separation"; },
    (m) => { m.themes = [""]; },
    (m) => { m.themes = Array(7).fill("theme"); },
    (m) => { m.themes = ["x".repeat(161)]; },
    (m) => { m.cues = null; },
    (m) => { m.cues = []; },
    (m) => { m.cues = [null]; },
    (m) => { m.cues = Array(9).fill(m.cues[0]); },
    (m) => { m.cues[0].start = 19; },
    (m) => { m.cues[0].end = 51; },
    (m) => { m.cues[0].start = "22"; },
    (m) => { m.cues[0].start = NaN; },
    (m) => { m.cues[0].end = Infinity; },
    (m) => { m.cues[0].end = 22; },
    (m) => { m.cues[0].paraphrase = " "; },
    (m) => { m.cues[0].paraphrase = "x".repeat(601); },
    (m) => { m.cues[0].confidence = "guaranteed"; },
  ]) {
    const value = document(); mutate(value.analysis.song_meaning);
    assert.equal(meaning.songMeaning(value), null);
  }
});

test("unclear or absent vocals cannot expose invented cue or theme fields", () => {
  for (const status of ["unclear", "no_vocals"]) {
    const value = document();
    value.analysis.song_meaning.vocal_status = status;
    assert.equal(meaning.songMeaning(value), null);
    const invalid = component.default({ document: value, working: false, onAnalyze() {} });
    assert.doesNotMatch(text(invalid), /fear of separation|speaker asks|What the model heard/);
    Object.assign(value.analysis.song_meaning, { themes: [], cues: [], summary: "The vocal words could not be established." });
    assert.equal(meaning.songMeaning(value), value.analysis.song_meaning);
    const tree = component.default({ document: value, working: false, onAnalyze() {} });
    assert.match(text(tree), status === "unclear" ? /Vocal meaning unclear/ : /No clear vocals/);
    assert.doesNotMatch(text(tree), /What the model heard|fear of separation/);
  }
});

test("model paraphrases expose original song times, confidence and limitations separately from atmosphere", () => {
  const value = document();
  const tree = component.default({ document: value, working: false, onAnalyze() {} });
  assert.match(text(tree), /Vocals partly understood/);
  assert.match(text(tree), /speaker anticipates a separation/);
  assert.match(text(tree), /final phrase is obscured/);
  const heard = nodes(tree).find((node) => node.type === "details" && text(node).startsWith("What the model heard"));
  assert.equal(heard.props.open, undefined, "Detailed observations start collapsed");
  assert.match(text(heard), /time:22 – time:25 · medium confidence/);
  assert.match(text(heard), /speaker asks the other person to stay/);
  assert.match(text(heard), /Paraphrases and approximate times, not verified lyrics/);
  const atmosphere = nodes(tree).find((node) => node.type === "details" && text(node).startsWith("Musical atmosphere"));
  assert.equal(atmosphere.props.open, false);
  assert.doesNotMatch(text(heard), /instrumentation gradually/);
});

test("listening is explicitly triggered and disabled while work is active", () => {
  let calls = 0;
  const value = document();
  const tree = component.default({ document: value, working: false, onAnalyze: () => calls++ });
  assert.equal(calls, 0);
  const button = nodes(tree).find((node) => node.type === "button");
  assert.equal(text(button).trim(), "Listen again");
  assert.equal(button.props.disabled, false);
  button.props.onClick(); assert.equal(calls, 1);
  const busy = component.default({ document: value, working: true, onAnalyze() {} });
  assert.equal(nodes(busy).find((node) => node.type === "button").props.disabled, true);
  value.analysis = null;
  const fresh = component.default({ document: value, working: false, onAnalyze() {} });
  assert.equal(text(nodes(fresh).find((node) => node.type === "button")).trim(), "Analyze music");
});
