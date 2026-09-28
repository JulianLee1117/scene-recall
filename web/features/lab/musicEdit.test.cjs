const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const limits = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "../../lib/labLimits.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText, { exports: limits });
const helpers = {};
const dialogue = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "dialogueAudio.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: dialogue });
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "musicEdit.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: helpers, crypto: require("node:crypto").webcrypto,
  require(name) { if (name === "@/lib/labLimits") return limits; if (name === "./dialogueAudio") return dialogue; throw new Error(name); } });
const plain = (value) => JSON.parse(JSON.stringify(value));

function fixture() {
  const evidence = { rank: 2, matched_text: "A figure waits by a window.", matched_text_view: "scene",
    matched_frame_index: 1, matched_frame_timestamp: 12, matches: [], channels: {} };
  const reference = { reference_id: "ref-a", clip_id: "a", film_id: "film-a", unit_id: "unit-a",
    frame_index: 1, timestamp: 12, source_start: 10, source_end: 14 };
  const searchPlan = { clauses: [
    { kind: "text", facet: "scene", text: "A figure waits", reference_id: null },
    { kind: "source", facet: "look", text: null, reference_id: "ref-a" },
  ], references: [reference], unverified_requirements: ["Let the gesture finish"] };
  const clip = { id: "a", film_id: "film-a", unit_id: "unit-a", title: "Scene A", source_start: 10, source_end: 14, locked: false };
  const slot = { id: "shot-a", start: 20, end: 24, section_index: 0, clip_id: "a",
    direction: { query: "A figure waits, warm tones", search_facet: "all", purpose: "Introduce the character", music_cue: "A soft entrance", timing_note: "Hold", search_plan: searchPlan },
    direction_source: "ai", needs_direction: false, feedback: null,
    alternatives: [{ clip: { ...clip, id: "alternative", film_id: "film-b", unit_id: "unit-b" }, film_title: "B", search_evidence: evidence }],
    reason: "A quiet opening", search_error: null, search_evidence: evidence,
    resolved_search: { ...searchPlan, capability_version: "test", min_duration: 4 } };
  return { schema_version: 1, track: { id: "song", name: "Song", duration: 60 }, passage: { start: 20, end: 28 },
    brief: "Quiet, then restless", film_ids: [], analysis: { segments: [{ start: 20, end: 28, query: "quiet", feeling: "calm", imagery: "window", energy: .2 }] },
    rhythm: null, clips: [clip], aspect_ratio: "16:9", fps: 24,
    music_timeline: { track_id: "song", passage: { start: 20, end: 28 }, slots: [slot, { ...slot, id: "shot-b", start: 24, end: 28, clip_id: null }] } };
}

function starter() {
  const document = fixture();
  document.analysis = null;
  document.clips = [];
  document.music_timeline = { ...document.music_timeline,
    provisional_timing: { contract: "local-rhythm-starter-v1", fingerprint: "opaque-server-proof" },
    slots: document.music_timeline.slots.map((slot) => ({ ...slot, clip_id: null, direction: null,
      direction_source: null, alternatives: [], resolved_search: null, search_evidence: null, feedback: null, needs_direction: false })),
  };
  return document;
}

test("manual timing permits 300 slots while retained footage has a separate 600-scene budget", () => {
  const document = starter();
  document.passage = { start: 0, end: 600 };
  document.track.duration = 600;
  document.music_timeline.passage = document.passage;
  document.music_timeline.slots = Array.from({ length: 299 }, (_, index) => ({
    ...document.music_timeline.slots[0], id: `slot-${index}`, start: index * 2, end: index === 298 ? 600 : index * 2 + 2,
  }));
  const split = helpers.splitSlot(document, "slot-0", 1);
  assert.equal(split.music_timeline.slots.length, 300);
  assert.equal(helpers.splitSlot(split, "slot-1", 3), split);
  assert.equal(document.music_timeline.slots.length, 299, "Undo source stays untouched");
  const source = fixture().clips[0];
  split.clips = Array.from({ length: 599 }, (_, index) => ({ ...source, id: `saved-${index}` }));
  const placed = helpers.placeClip(split, "slot-0", source);
  assert.equal(placed.clips.length, 600);
  assert.throws(() => helpers.placeClip(placed, "slot-1", source), /600 saved scenes/);
  assert.equal(helpers.placeClip(placed, "slot-0", source).clips.length, 600, "reselection does not consume another saved slot");
});

test("manual starter-cut movement, splitting and joining fix the resulting timing while preserving the original for Undo", () => {
  for (const update of [
    (doc) => helpers.moveCut(doc, 1, 25, {}),
    (doc) => helpers.splitSlot(doc, "shot-a", 22),
    (doc) => helpers.joinWithNext(doc, "shot-a", {}),
  ]) {
    const original = starter();
    const fixed = update(original);
    assert.equal(fixed.music_timeline.provisional_timing, null);
    assert.equal(original.music_timeline.provisional_timing.fingerprint, "opaque-server-proof");
    assert.deepEqual(original.music_timeline.slots.map(({ start, end }) => [start, end]), [[20, 24], [24, 28]]);
  }
});

test("manual scene placement, written direction and feedback claim starter timing", () => {
  const source = fixture().clips[0];
  for (const update of [
    (doc) => helpers.placeClip(doc, "shot-a", source),
    (doc) => helpers.editSlotDirection(doc, "shot-a", { query: "The lights go out" }),
    (doc) => helpers.setSlotFeedback(doc, "shot-a", "wrong_energy"),
    (doc) => helpers.resetSlotDirection(doc, "shot-a"),
    (doc) => helpers.keepCutPlan(doc),
  ]) {
    const original = starter();
    const fixed = update(original);
    assert.equal(fixed.music_timeline.provisional_timing, null);
    assert.equal(original.music_timeline.provisional_timing.fingerprint, "opaque-server-proof");
    assert.deepEqual(plain(fixed.music_timeline.slots.map(({ start, end }) => [start, end])), [[20, 24], [24, 28]]);
  }
});

test("no-op edits preserve starter eligibility and old timelines never acquire it", () => {
  const document = starter();
  for (const same of [
    helpers.moveCut(document, 1, 24, {}),
    helpers.editSlotDirection(document, "shot-a", { query: "" }),
    helpers.setSlotFeedback(document, "shot-a", null),
    helpers.clearSlot(document, "shot-a"),
  ]) assert.equal(same, document);
  const old = fixture();
  assert.equal(helpers.keepCutPlan(old), old);
  assert.equal(helpers.planOf(old).provisional_timing, undefined);
  const fixed = helpers.moveCut(old, 1, 25, { "film-a": 90 });
  assert.equal(fixed.music_timeline.provisional_timing, null);
});

test("audio fade keeps untouched starter timing eligible", () => {
  const document = starter();
  const fade = helpers.changeMusicPassage(document, document.passage, 1);
  assert.equal(fade.music_timeline.provisional_timing, document.music_timeline.provisional_timing);
});

test("query edits execute visible text and clear the old recipe and returned evidence without changing the edit", () => {
  const original = fixture();
  const next = helpers.editSlotDirection(original, "shot-a", { query: "Running through rain" });
  const slot = next.music_timeline.slots[0];
  assert.equal(slot.direction.query, "Running through rain");
  assert.equal(slot.direction.search_plan, null);
  assert.equal(slot.resolved_search, null);
  assert.equal(slot.search_evidence, null);
  assert.equal(slot.alternatives.length, 0);
  assert.equal(slot.direction_source, "user");
  assert.equal(slot.start, 20);
  assert.equal(slot.end, 24);
  assert.equal(slot.clip_id, "a");
  assert.equal(next.clips, original.clips);
  assert.equal(next.music_timeline.slots[1], original.music_timeline.slots[1]);
  assert.ok(original.music_timeline.slots[0].direction.search_plan);
});

test("scene search explains empty clues, stale references and explicitly unavailable signals before submitting", () => {
  const doc = fixture();
  const direction = doc.music_timeline.slots[0].direction;
  assert.equal(helpers.searchInputProblem(direction, doc.clips, null), null,
    "unknown availability must not be confused with unavailable");
  const empty = structuredClone(direction);
  empty.search_plan.clauses[0].text = "  ";
  assert.match(helpers.searchInputProblem(empty, doc.clips, null), /Write clue 1/);
  assert.match(helpers.searchInputProblem(direction, [{ ...doc.clips[0], source_start: 11 }], null), /reference changed/);
  assert.match(helpers.searchInputProblem(direction, doc.clips, {
    facets: [{ facet: "scene", label: "Scene", text_available: false }],
  }), /Scene is unavailable/);
  assert.match(helpers.searchInputProblem({ ...direction, query: "" }, doc.clips, null), /Describe a scene/);
  assert.equal(helpers.searchInputProblem(direction, doc.clips, null), null, "validation does not mutate the saved search");
});

test("changing the simple search signal also removes a typed recipe", () => {
  const next = helpers.editSlotDirection(fixture(), "shot-a", { search_facet: "mood" });
  assert.equal(next.music_timeline.slots[0].direction.search_facet, "mood");
  assert.equal(next.music_timeline.slots[0].direction.search_plan, null);
  assert.equal(next.music_timeline.slots[0].search_evidence, null);
});

test("intent-only edits keep actual search evidence and unchanged edits do not consume Undo", () => {
  const original = fixture();
  const next = helpers.editSlotDirection(original, "shot-a", { purpose: "Introduce tension" });
  assert.equal(next.music_timeline.slots[0].search_evidence, original.music_timeline.slots[0].search_evidence);
  assert.equal(next.music_timeline.slots[0].direction.search_plan, original.music_timeline.slots[0].direction.search_plan);
  assert.equal(helpers.editSlotDirection(original, "shot-a", { query: original.music_timeline.slots[0].direction.query }), original);
});

test("removing a reference clue drops its frozen reference and stale results", () => {
  const original = fixture();
  const plan = original.music_timeline.slots[0].direction.search_plan;
  const next = helpers.editSlotSearchPlan(original, "shot-a", { ...plan, clauses: [plan.clauses[0]] });
  assert.deepEqual(plain(next.music_timeline.slots[0].direction.search_plan.references), []);
  assert.equal(next.music_timeline.slots[0].direction.search_plan.clauses.length, 1);
  assert.equal(next.music_timeline.slots[0].resolved_search, null);
  assert.equal(next.music_timeline.slots[0].search_evidence, null);
  const cleared = helpers.editSlotSearchPlan(next, "shot-a", { ...plan, clauses: [] });
  assert.equal(cleared.music_timeline.slots[0].direction.search_plan, null);
});

test("choosing an alternative carries its evidence; manual replacement clears it and preserves timing", () => {
  const original = fixture();
  const alternative = original.music_timeline.slots[0].alternatives[0];
  const chosen = helpers.placeClip(original, "shot-a", alternative.clip, alternative.search_evidence);
  assert.equal(chosen.music_timeline.slots[0].search_evidence, alternative.search_evidence);
  assert.equal(chosen.music_timeline.slots[0].start, 20);
  assert.equal(chosen.music_timeline.slots[0].end, 24);
  const manual = helpers.placeClip(chosen, "shot-a", { ...alternative.clip, source_start: 30, source_end: 34 });
  assert.equal(manual.music_timeline.slots[0].search_evidence, null);
  assert.equal(manual.clips.find((item) => item.id === manual.music_timeline.slots[0].clip_id).source_start, 30);
});

test("adjusting a placed source invalidates its resolved search and marks direction for review without rewriting it", () => {
  const original = fixture();
  const clip = { ...original.clips[0], crop: { x: 0.1, y: 0, width: 0.8, height: 1 } };
  const adjusted = helpers.placeClip(original, "shot-a", clip, null, true);
  const slot = adjusted.music_timeline.slots[0];
  assert.equal(slot.search_evidence, null);
  assert.equal(slot.resolved_search, null);
  assert.equal(slot.needs_direction, true);
  assert.equal(slot.direction, original.music_timeline.slots[0].direction);
  assert.equal(slot.start, 20);
  assert.equal(slot.end, 24);
  assert.deepEqual(plain(adjusted.clips.find((item) => item.id === slot.clip_id).crop), clip.crop);
});

test("locked scenes reject replacement and clear; moving cuts invalidates evidence for both affected shots", () => {
  const original = fixture();
  original.clips[0].locked = true;
  assert.equal(helpers.placeClip(original, "shot-a", { ...original.clips[0] }), original);
  assert.equal(helpers.clearSlot(original, "shot-a"), original);
  original.clips[0].locked = false;
  const moved = helpers.moveCut(original, 1, 25, { "film-a": 90 });
  assert.equal(moved.music_timeline.slots[0].end, 25);
  assert.equal(moved.music_timeline.slots[1].start, 25);
  for (const slot of moved.music_timeline.slots) {
    assert.equal(slot.search_evidence, null);
    assert.equal(slot.resolved_search, null);
  }
});

function catalog() {
  return { recipe: { max_clauses: 3 }, facets: ["all", "scene", "mood", "look", "words", "composition"].map((facet) =>
    ({ facet, text_available: facet !== "composition" })) };
}

test("adding a text clue builds from the visible direction and prefers an available focused signal", () => {
  const direction = { ...fixture().music_timeline.slots[0].direction, search_plan: null };
  assert.deepEqual(plain(helpers.newTextClueFacets(direction, catalog())), ["scene", "mood", "look", "words"]);
  const plan = helpers.addTextSearchClue(direction, "mood", "  Uneasy anticipation  ", catalog());
  assert.equal(plan.clauses.length, 2);
  assert.equal(plan.clauses[0].text, direction.query);
  assert.equal(plan.clauses[0].facet, direction.search_facet);
  assert.equal(plan.clauses[1].text, "Uneasy anticipation");
  assert.equal(plan.clauses[1].facet, "mood");
  assert.equal(direction.search_plan, null);
});

test("new clues reject blank text, duplicates, unverified signals and the fourth clause", () => {
  const direction = fixture().music_timeline.slots[0].direction;
  const capabilities = catalog();
  assert.deepEqual(plain(helpers.newTextClueFacets(direction, capabilities)), ["mood", "words"]);
  assert.equal(helpers.addTextSearchClue(direction, "mood", " ", capabilities), null);
  assert.equal(helpers.addTextSearchClue(direction, "scene", "Another subject", capabilities), null);
  capabilities.facets.find((facet) => facet.facet === "mood").text_available = null;
  assert.equal(helpers.addTextSearchClue(direction, "mood", "Anticipation", capabilities), null);
  const plan = helpers.addTextSearchClue(direction, "words", "Coming home", capabilities);
  assert.equal(plan.references, direction.search_plan.references);
  assert.equal(plan.clauses[1], direction.search_plan.clauses[1]);
  assert.equal(plan.clauses.length, 3);
  assert.equal(helpers.addTextSearchClue({ ...direction, search_plan: plan }, "mood", "Anticipation", catalog()), null);
});

test("an empty or invalid existing direction cannot seed a saved recipe", () => {
  const direction = fixture().music_timeline.slots[0].direction;
  assert.deepEqual(plain(helpers.newTextClueFacets({ ...direction, query: " ", search_plan: null }, catalog())), []);
  const invalid = { ...direction.search_plan, clauses: [{ kind: "text", facet: "scene", text: "", reference_id: null }] };
  assert.equal(helpers.addTextSearchClue({ ...direction, search_plan: invalid }, "mood", "Calm", catalog()), null);
});
