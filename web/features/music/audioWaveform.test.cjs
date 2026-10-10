const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

function load(file) {
  const exported = {};
  const compiled = ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(compiled, { exports: exported });
  return exported;
}
const { audioPeaks, waveformPath } = load("audioWaveform.ts");
const { playbackScrollLeft } = load("timelineViewport.ts");

test("decoded waveform preserves brief stereo attacks and the final sample", () => {
  const left = new Float32Array(16000), right = new Float32Array(16000);
  // A single-sample attack that the old stride-subsampling could miss.
  left[157] = .75;
  right[8017] = .9;
  right[15999] = .6;
  const values = audioPeaks({ duration: 1, length: 16000, numberOfChannels: 2,
    getChannelData: (index) => [left, right][index] });
  assert.equal(values.length, 200);
  assert.equal(values[1], .75);
  assert.ok(Math.abs(values[100] - .9) < 1e-6);
  assert.ok(Math.abs(values[199] - .6) < 1e-6);
  assert.equal(values.filter((value) => value > 0).length, 3);
});

test("zoom reveals separate measured attacks instead of stretching the overview", () => {
  const values = new Array(2000).fill(0);
  values[500] = .4; values[510] = .9;
  const overview = waveformPath(values, 10, 0, 10, 100);
  const detail = waveformPath(values, 10, 2.4, 2.9, 100);
  const bars = (value) => value.split(" ").filter((bar) => !bar.endsWith("v1.00"));
  assert.equal(bars(overview).length, 1);
  assert.equal(bars(detail).length, 2);
  assert.match(detail, /v24\.80/);
  assert.match(detail, /v55\.80/);
  assert.equal(bars(waveformPath(values, 10, 4, 4.5, 100)).length, 0);
});

test("waveform work is bounded by viewport pixels and preserves peaks at passage offsets", () => {
  const values = new Array(120000).fill(0);
  values[60000] = 1;
  assert.equal(waveformPath(values, 600, 300, 337.5, 900).split(" ").length, 900);
  assert.match(waveformPath(values, 600, 300, 337.5, 900), /v62\.00/);
  assert.equal(waveformPath(values, 600, 0, 600, 10000).split(" ").length, 4096);
  assert.equal(waveformPath([], 600, 0, 600, 900), "");
});

test("playback follow waits until the playhead leaves view and clamps at both ends", () => {
  assert.equal(playbackScrollLeft(899, 0, 900, 3600), 0);
  assert.equal(playbackScrollLeft(900, 0, 900, 3600), 765);
  assert.equal(playbackScrollLeft(1400, 765, 900, 3600), 765);
  assert.equal(playbackScrollLeft(3700, 765, 900, 3600), 2700);
  assert.equal(playbackScrollLeft(700, 765, 900, 3600), 0);
  assert.equal(playbackScrollLeft(900, 0, 900, 900), 0);
});
