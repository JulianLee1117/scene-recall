const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const compiled = (file) => ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const progress = {};
vm.runInNewContext(compiled("jobProgress.ts"), { exports: progress });
const timing = {};
vm.runInNewContext(compiled("timingPlan.ts"), { exports: timing, require: () => ({ MAX_MUSIC_TIMELINE_SLOTS: 300 }) });
const job = (overrides = {}) => ({ id: "job-1", project_id: "project-1", kind: "generate", status: "running", base_revision: 3, created_at: 100, started_at: 110, ...overrides });

test("queued editor reports its own lane rather than waiting on ingestion", () => {
  const queued = job({ status: "queued", worker_role: "editor" });
  const editor = { role: "editor", online: true, state: "idle", mode: "separate", current_job: null };
  const ingestion = { role: "ingest", online: true, state: "busy", current_job: { id: "ingest-1", kind: "ingest" } };
  assert.equal(progress.queuedWorkerSummary(queued, { workers: [editor, ingestion] }), "Queued for the editor worker.");
  assert.match(progress.queuedWorkerSummary(queued, { workers: [{ ...editor, online: false }] }), /offline/);
  assert.match(progress.queuedWorkerSummary(queued, { workers: [{ ...editor, state: "stopping" }] }), /stopping/);
  assert.match(progress.queuedWorkerSummary({ ...queued, kind: "match", worker_role: "ingest" }, { workers: [editor, ingestion] }), /release the GPU/);
  assert.equal(progress.queuedWorkerSummary({ ...queued, cancel_requested: true }, { workers: [editor] }), null);
  assert.equal(progress.queuedWorkerSummary(job(), { workers: [editor] }), null);
  assert.equal(progress.queuedWorkerSummary(queued, null), null);
});

test("elapsed time uses persisted worker start across reloads and freezes at completion", () => {
  assert.equal(progress.jobElapsed(job(), 145900), 35);
  assert.equal(progress.jobElapsed(job({ status: "completed", finished_at: 170 }), 200000), 60);
  assert.equal(progress.jobElapsed(job({ status: "failed", finished_at: 170 }), 500000), 60);
  assert.equal(progress.elapsedLabel(65), "1:05");
});

test("queue time is distinct and absent or malformed timestamps stay unknown", () => {
  assert.equal(progress.jobElapsed(job({ status: "queued", started_at: null }), 145900), 45);
  assert.equal(progress.jobElapsed(job({ created_at: undefined, started_at: undefined }), 145900), null);
  assert.equal(progress.jobElapsed(job({ status: "completed", finished_at: null }), 145900), null);
  assert.equal(progress.jobElapsed(job({ started_at: "bad date", created_at: "bad date" }), 145900), null);
  assert.equal(progress.jobElapsed(job({ started_at: "2026-09-13T00:00:00Z" }), Date.parse("2026-09-13T00:00:15Z")), 15);
});

test("only actual recorded messages appear, retaining model identity and bounded counts", () => {
  const current = job({ progress_steps: ["Listening", "Listening", 23, "", "Using OpenAI model-a for planning"], progress: "Searching 3 of 9" });
  const result = progress.jobSteps(current);
  assert.equal(progress.jobSummary(current), "Searching 3 of 9");
  assert.deepEqual(Array.from(result), ["Listening", "Using OpenAI model-a for planning", "Searching 3 of 9"]);
  assert.equal(progress.jobSteps(job({ progress_steps: Array.from({ length: 90 }, (_, i) => `Step ${i}`) })).length, 80);
  assert.equal(progress.jobSteps(job()).length, 0);
});

test("failure and unapplied outcomes take priority over an unrelated success notice", () => {
  const failed = job({ status: "failed", error: "The provider is busy." });
  assert.equal(progress.jobSummary(failed, "Saved"), "The provider is busy.");
  const unapplied = job({ status: "completed", result: { applied: false, message: "Edit ready" } });
  assert.match(progress.jobSummary(unapplied, "Saved"), /without replacing your edit/);
  assert.equal(progress.jobStateLabel(unapplied), "Not applied");
  assert.match(progress.jobSummary(job({ status: "interrupted" })), /worker stopped/);
});

test("cancel requests stay active and visible until the worker acknowledges them", () => {
  const stopping = job({ cancel_requested: true, progress: "Old stage" });
  assert.equal(progress.isJobActive(stopping), true);
  assert.equal(progress.jobStateLabel(stopping), "Stopping");
  assert.match(progress.jobSummary(stopping), /Stopping/);
  assert.equal(progress.isJobActive({ ...stopping, status: "cancelled" }), false);
});

test("candidate counts report supplied facts including zero, never inferred totals or malformed data", () => {
  assert.deepEqual(JSON.parse(JSON.stringify(progress.jobCounts(job({ result: { candidate_count: 12, selected_count: 0, remaining_gaps: 3 } })))), [
    { label: "Offered candidates", count: 12 }, { label: "Selected scenes", count: 0 }, { label: "Unfilled shots", count: 3 },
  ]);
  assert.equal(progress.jobCounts(job({ result: { selected_count: -1, remaining_gaps: "4" } })).length, 0);
});

const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
function render(props) {
  const exported = {};
  vm.runInNewContext(compiled("JobStatus.tsx"), { exports: exported,
    require(name) {
      if (name === "react") return { useState: () => [null, () => {}], useEffect() {} };
      if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }), Fragment: "fragment" };
      if (name === "./jobProgress") return progress;
      if (name === "./timingPlan") return timing;
      return { default: name };
    },
  });
  return exported.default(props);
}

test("details use the shared anchored panel, cancellation is only active, and caller actions survive", () => {
  let cancelled = 0;
  const tree = render({ job: job({ progress_steps: ["Listening"] }), onCancel: () => cancelled++, actions: "Latest export" });
  const button = nodes(tree).find((node) => node.type === "button");
  button.props.onClick(); assert.equal(cancelled, 1);
  assert.ok(nodes(tree).some((node) => node.type === "./EditorPopover" && node.props.title === "Task details"));
  assert.match(text(tree), /Latest export/);
  const finished = render({ job: job({ status: "failed", error: "Model unavailable", result: { private_blob: "HIDDEN RESULT" } }), onCancel() {} });
  assert.equal(nodes(finished).filter((node) => node.type === "button").length, 0);
  assert.match(text(finished), /Model unavailable/);
  assert.doesNotMatch(text(finished), /HIDDEN RESULT/);
});

test("compact progress retains complete failures and an explicitly labeled error details control", () => {
  const error = "The provider could not select a scene. The saved project is unchanged. Try generating again.";
  const tree = render({ compact: true, job: job({ status: "failed", error }), onCancel() {} });
  const panel = nodes(tree).find((node) => node.type === "./EditorPopover");
  assert.equal(panel.props.label, "Error details");
  assert.match(text(panel), /The saved project is unchanged/);
  assert.equal(nodes(tree).find((node) => node.props?.role === "status").props.title, error);
});

test("compact count is the final fitted shot count and timing notes stay inside Details", () => {
  const receipt = { contract: "timing-v1", artifact_id: "plan-1", track_id: "song-1", fps: 24,
    passage: { start: 0, end: 10 }, end_frames: [24, 120, 240], final_end_frames: [48, 240], notes: [] };
  const tree = render({ compact: true, job: job({ status: "completed", result: { timing_plan: receipt } }), onCancel() {} });
  assert.match(text(tree), /2 shots/);
  assert.doesNotMatch(text(tree), /3 shots/);
  const panel = nodes(tree).find((node) => node.type === "./EditorPopover");
  assert.ok(nodes(panel).some((node) => node.type === "./TimingPlanDetails"));
  const nominal = render({ job: job({ status: "completed", result: { timing_plan: { ...receipt, final_end_frames: null } } }), onCancel() {} });
  assert.doesNotMatch(text(nominal), /\d+ shots/);
});

test("optional footage inspection stays inside Task Details and is never inherited from the document", () => {
  const receipt = { contract: "targeted-footage-inspection-v1", status: "partial", targets: [] };
  const tree = render({ compact: true, job: job({ status: "completed", result: { footage_inspection: receipt } }), onCancel() {} });
  const panel = nodes(tree).find(node => node.type === "./EditorPopover");
  const details = nodes(panel).find(node => node.type === "./FootageInspectionDetails");
  assert.equal(details.props.value, receipt);
  const old = render({ job: job(), savedProject: { document: { footage_inspection: receipt } }, onCancel() {} });
  assert.equal(nodes(old).filter(node => node.type === "./FootageInspectionDetails").length, 0);
});
