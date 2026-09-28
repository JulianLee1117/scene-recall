const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const compiled = (file) => ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const timing = {};
vm.runInNewContext(compiled("timingPlan.ts"), { exports: timing,
  require(name) {
    assert.equal(name, "@/lib/labLimits");
    return { MAX_MUSIC_TIMELINE_SLOTS: 300 };
  },
});

function receipt(overrides = {}) {
  return {
    contract: "music-timing-v1", artifact_id: "timing-1", track_id: "song-1",
    passage: { start: 60, end: 70 }, fps: 24,
    end_frames: [24, 120, 240], final_end_frames: [48, 240],
    nominal_timing: { selected_shots: 3, shortest_seconds: 1, longest_seconds: 5, average_seconds_selected: 10 / 3, uniform_timing: false },
    final_timing: { selected_shots: 2, shortest_seconds: 2, longest_seconds: 8, average_seconds_selected: 5, uniform_timing: false },
    notes: [{ start_frame: 0, end_frame: 120, reason: "Hold through the quiet phrase.", evidence_ids: ["event-17"] }],
    cache_reused: true,
    ...overrides,
  };
}

test("recorded nominal and final fit remain distinct, including original-song time", () => {
  const plan = timing.readTimingPlan(receipt());
  assert.equal(plan.end_frames.length, 3);
  assert.equal(plan.final_end_frames.length, 2);
  assert.equal(plan.nominal_timing.average_seconds_selected, 10 / 3);
  assert.equal(plan.final_timing.average_seconds_selected, 5);
  assert.equal(timing.timingFrameTime(plan, 120), 65);
  assert.equal(plan.notes[0].reason, "Hold through the quiet phrase.");
  assert.equal(plan.notes[0].evidence_ids[0], "event-17");
});

test("missing, legacy, malformed and out-of-scope receipts are safely absent", () => {
  for (const input of [null, undefined, [], {}, { pace_budget: { selected_shots: 20 } },
    receipt({ fps: NaN }), receipt({ fps: 0 }), receipt({ passage: { start: 20, end: 10 } }),
    receipt({ end_frames: [120, 24, 240] }), receipt({ end_frames: [24, 120, 241] }),
    receipt({ end_frames: [24, "120", 240] }), receipt({ end_frames: [] }),
    receipt({ artifact_id: null }), receipt({ end_frames: Array.from({ length: 301 }, (_, i) => i + 1) }),
  ]) assert.equal(timing.readTimingPlan(input), null);
});

test("a nominal plan without final fitting never claims final shots or stale diagnostics", () => {
  for (const final_end_frames of [undefined, [50, 250], [240, 120], []]) {
    const plan = timing.readTimingPlan(receipt({ final_end_frames }));
    assert.equal(plan.final_end_frames, null);
    assert.equal(plan.final_timing, null);
    assert.equal(plan.end_frames.length, 3);
  }
  const plan = timing.readTimingPlan(receipt({ nominal_timing: { selected_shots: "3" },
    final_timing: { selected_shots: 4, shortest_seconds: 2, longest_seconds: 8, average_seconds_selected: 5, uniform_timing: false } }));
  assert.equal(plan.nominal_timing, null);
  assert.equal(plan.final_timing, null);
});

test("malformed notes are omitted and recorded notes stay bounded without invented explanations", () => {
  const valid = receipt().notes[0];
  const plan = timing.readTimingPlan(receipt({ notes: [null, { ...valid, reason: {} }, { ...valid, end_frame: 241 },
    { ...valid, start_frame: -1 }, { ...valid, start_frame: 120 }, { ...valid, evidence_ids: [null, {}, "event-17"] }] }));
  assert.equal(plan.notes.length, 1);
  assert.deepEqual(Array.from(plan.notes[0].evidence_ids), ["event-17"]);
  assert.equal(timing.readTimingPlan(receipt({ notes: Array(40).fill(valid) })).notes.length, 32);
});

test("fractional last frame uses exact selected passage endpoint and accepts backend rounding ties", () => {
  const plan = timing.readTimingPlan(receipt({ passage: { start: 60, end: 70.01 } }));
  assert.equal(timing.timingFrameTime(plan, 240), 70.01);
  assert.ok(timing.readTimingPlan(receipt({ passage: { start: 60, end: 60 + 240.5 / 24 } })));
  assert.ok(timing.readTimingPlan(receipt({ passage: { start: 60, end: 60 + 239.5 / 24 } })));
});

test("document fallback belongs only to the matching completed applied job and saved scope", () => {
  const job = { project_id: "project-1", status: "completed", result: { applied: true, revision: 4 } };
  const saved = { id: "project-1", revision: 4, document: { track: { id: "song-1" }, passage: { start: 60, end: 70 }, direction_plan: { timing_plan: receipt() } } };
  assert.equal(timing.timingPlanForJob(job, saved).artifact_id, "timing-1");
  for (const wrong of [{ ...job, status: "failed" }, { ...job, status: "running" },
    { ...job, project_id: "other" }, { ...job, result: { applied: false, revision: 4 } },
    { ...job, result: { applied: true, revision: 3 } }, { ...job, result: { ...job.result, timing_plan: {} } },
  ]) assert.equal(timing.timingPlanForJob(wrong, saved), null);
  assert.equal(timing.timingPlanForJob(job, { ...saved, document: { ...saved.document, passage: { start: 0, end: 10 } } }), null);
  assert.equal(timing.timingPlanForJob(job, { ...saved, document: { ...saved.document, track: { id: "other" } } }), null);
  assert.equal(timing.timingPlanForJob({ ...job, result: { timing_plan: receipt({ artifact_id: "job-receipt" }) } }, saved).artifact_id, "job-receipt");
});

const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
test("timing details label initial versus final, retain recorded reasons and make provenance inspectable", () => {
  const exported = {};
  vm.runInNewContext(compiled("TimingPlanDetails.tsx"), { exports: exported, require(name) {
    if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
    if (name === "@/lib/lab") return { seconds: (value) => value.toFixed(2) };
    if (name === "./timingPlan") return timing;
    return { default: {} };
  } });
  const content = text(exported.default({ plan: timing.readTimingPlan(receipt()) }));
  assert.match(content, /Initial plan3 shots/);
  assert.match(content, /Final footage fit2 shots/);
  assert.match(content, /60.00 – 65.00Hold through the quiet phrase/);
  assert.match(content, /Evidence IDs: event-17/);
  assert.match(content, /Times refer to the initial plan/);
  assert.match(content, /Reused saved timing plan/);
  assert.match(content, /timing-1/);
  assert.match(content, /Initial end frames24, 120, 240/);
  assert.match(content, /Final end frames48, 240/);
  const partial = text(exported.default({ plan: timing.readTimingPlan(receipt({ notes: [], final_end_frames: null })) }));
  assert.doesNotMatch(partial, /Final footage fit|Timing notes/);
});
