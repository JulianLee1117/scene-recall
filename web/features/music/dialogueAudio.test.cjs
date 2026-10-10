const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const compile = (name) => ts.transpileModule(fs.readFileSync(path.join(__dirname, name), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const helpers = {};
vm.runInNewContext(compile("dialogueAudio.ts"), { exports: helpers });
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-8, `${a} ≈ ${b}`);
const voice = (patch = {}) => ({ id: "voice", film_id: "film", source_start: 100, source_end: 102,
  start: 12, gain_db: 0, fade_in_seconds: .08, fade_out_seconds: .12, music_duck_db: -8, title: "Voice", text: "", ...patch });
const document = (clips = []) => ({ passage: { start: 10, end: 20 }, dialogue_clips: clips, clips: [{ id: "picture" }], music_timeline: { slots: [{ clip_id: "picture" }] } });
const media = () => ({ currentTime: 0, duration: 1000, readyState: 4, seeking: false, paused: true, volume: 1, muted: false, error: null,
  pause() { this.paused = true; }, async play() { this.paused = false; }, load() { this.error = null; } });

function transportHarness({ activate = async () => {}, AudioContext } = {}) {
  const gain = {}, exported = {};
  vm.runInNewContext(compile("dialogueGain.ts"), { exports: gain, ...(AudioContext ? { AudioContext } : {}) });
  vm.runInNewContext(compile("dialogueTransport.ts"), { exports: exported, AbortController, URLSearchParams,
    require(name) {
      if (name === "./dialogueAudio") return helpers;
      if (name === "./dialogueGain") return AudioContext ? gain : { ...gain, activateDialogueAudio: activate };
      if (name === "@/lib/lab") return { mediaUrl: (url) => url };
      throw new Error(name);
    },
  });
  return { ...exported, gain };
}

test("voice fades and positive gain stay independent of music gain, duck tails combine by minimum amplitude", () => {
  const a = voice({ gain_db: 12 }), b = voice({ id: "next", start: 14.2, source_end: 101, music_duck_db: -12 });
  const doc = { ...document([a, b]), music_gain_db: -6, audio_fade_in_seconds: 4, audio_fade_out_seconds: 3 };
  const level = 10 ** (-8 / 20), musicGain = 10 ** (-6 / 20);
  close(helpers.musicVolume(doc, 11.875), musicGain * (1 - (1 - level) * .5) * (1.875 / 4));
  close(helpers.musicVolume(doc, 13), musicGain * level * .75);
  const aRelease = level + (1 - level) * .1 / .5, bAttack = 1 - (1 - 10 ** (-12 / 20)) * .15 / .25;
  close(helpers.musicVolume(doc, 14.1), musicGain * Math.min(aRelease, bAttack));
  close(helpers.dialogueVolume(a, 12.04), 10 ** (12 / 20) * .5);
  close(helpers.dialogueVolume(a, 13.94), 10 ** (12 / 20) * .5);
  close(helpers.musicVolume(doc, 18.5), musicGain * .5);
  assert.equal(helpers.dialogueAt([a], 14), null);
  assert.equal(helpers.dialogueVolume(a, 14), 0);
  const slow = voice({ duck_attack_seconds: .6, duck_release_seconds: 1.2 });
  close(helpers.musicVolume(document([slow]), 11.7), (1 + level) / 2);
  close(helpers.musicVolume(document([slow]), 14.6), (1 + level) / 2);
  const instant = voice({ duck_attack_seconds: 0, duck_release_seconds: 0 });
  close(helpers.musicVolume(document([instant]), 11.999), 1);
  close(helpers.musicVolume(document([instant]), 14), 1);
  close(helpers.musicVolume(document([instant]), 14.001), 1);
});

test("prepared voice URL identifies the exact source window and focus mode used by export", async () => {
  const { resolveDialogueSource } = transportHarness();
  const url = await resolveDialogueSource(voice({ film_id: "film with spaces", source_audio_mode: "voice_focus" }), new AbortController().signal);
  const parsed = new URL(url, "http://localhost");
  assert.equal(parsed.pathname, "/lab/dialogue-audio");
  assert.equal(parsed.searchParams.get("film_id"), "film with spaces");
  assert.equal(parsed.searchParams.get("source_start"), "100");
  assert.equal(parsed.searchParams.get("source_end"), "102");
  assert.equal(parsed.searchParams.get("source_audio_mode"), "voice_focus");
});

test("dialogue placement preserves all B-roll, permits overlaps, and passage trim preserves surviving source offsets", () => {
  const original = document([voice()]);
  const next = helpers.addDialogue(original, { film_id: "other", title: "Line", source_start: 30, source_end: 33 }, "new", 12.5, "Hello");
  assert.equal(next.clips, original.clips); assert.equal(next.music_timeline, original.music_timeline);
  assert.equal(next.dialogue_clips[1].start, 12.5);
  assert.equal(helpers.updateDialogue(next, "new", { start: 13 }).dialogue_clips[1].start, 13);
  const moved = helpers.updateDialogue(next, "voice", { start: 18 });
  assert.equal(moved.dialogue_clips[1].start, 18, "a voice can move past another voice into a free gap");
  const trimmed = helpers.trimDialogueToPassage(next.dialogue_clips, { start: 13, end: 15 });
  assert.deepEqual(JSON.parse(JSON.stringify(trimmed.map(({ start, source_start, source_end }) => ({ start, source_start, source_end })))), [
    { start: 13, source_start: 101, source_end: 102 }, { start: 13, source_start: 30.5, source_end: 32.5 },
  ]);
});

test("source lookup cannot publish after scrubbing to another voice, and source offsets follow the song clock", async () => {
  const requests = [], audio = media(), { createDialogueTransport } = transportHarness();
  const transport = createDialogueTransport(audio, [voice(), voice({ id: "second", film_id: "second", start: 15 })], (clip, signal) => {
    const gate = deferred(); requests.push({ film: clip.film_id, signal, gate }); return gate.promise;
  });
  assert.equal(transport.prepare(12.5), false);
  transport.prepare(15.5);
  assert.equal(requests[0].signal.aborted, true);
  requests[1].gate.resolve("/video/second"); await flush(); transport.prepare(15.5);
  assert.equal(audio.src, "/video/second"); assert.equal(audio.currentTime, .5);
  requests[0].gate.resolve("/video/film"); await flush();
  assert.equal(audio.src, "/video/second");
  transport.prepare(14.5); assert.equal(audio.paused, true);
  transport.dispose();
});

test("decode failure at HAVE_NOTHING is visible and retry can recover; a pending play cannot restart a stopped voice", async () => {
  const audio = media(), { createDialogueTransport } = transportHarness();
  const transport = createDialogueTransport(audio, [voice()], async () => "/video/film");
  transport.prepare(12); await flush();
  audio.readyState = 0; audio.error = { code: 4 };
  assert.equal(transport.prepare(12), false); assert.match(transport.error, /could not be played/);
  transport.retry(); await flush(); audio.readyState = 4;
  assert.equal(transport.prepare(12), true); assert.equal(transport.error, "");
  const pending = deferred(); audio.play = () => pending.promise.then(() => { audio.paused = false; });
  const playing = transport.play(); transport.pause(); pending.resolve(); await playing;
  assert.equal(audio.paused, true);
  transport.dispose();
});

test("sample-rounded EOF just before the song clock holds silence instead of restarting the prepared voice", async () => {
  for (const ended of [true, false]) {
    const audio = media(), { createDialogueTransport } = transportHarness();
    const clip = voice({ source_start: 5351.9, source_end: 5359.2 });
    audio.duration = 7.3;
    assert.ok(clip.source_end - clip.source_start > audio.duration, "fixture reproduces the real source-range subtraction error");
    const transport = createDialogueTransport(audio, [clip], async () => "/voice.wav");
    transport.prepare(19.28); await flush(); transport.prepare(19.28);
    audio.currentTime = 7.3; audio.ended = ended; audio.paused = true;
    let plays = 0; audio.play = async () => { plays += 1; audio.currentTime = 0; audio.paused = false; };
    assert.equal(transport.prepare(19.28), true); await transport.play();
    assert.equal(plays, 0); assert.equal(audio.volume, 0); assert.equal(transport.playing, true);
    assert.equal(audio.currentTime, 7.3);
    transport.prepare(12.5, true); assert.equal(audio.currentTime, .5, "an explicit backward scrub leaves the end hold");
    transport.dispose();
  }
});

test("Web Audio resumes inside the gesture, gates ready media until audible, and reuses its graph after effect cleanup", async () => {
  const resumes = [], gains = [], sources = [];
  class AudioContext {
    destination = {};
    createMediaElementSource(audio) { assert.equal(sources.includes(audio), false); sources.push(audio); return { connect() {}, disconnect() {} }; }
    createGain() { const node = { gain: { value: 1 }, connect() {}, disconnect() {} }; gains.push(node); return node; }
    resume() { const gate = deferred(); resumes.push(gate); return gate.promise; }
    async suspend() {}
  }
  const { createDialogueTransport, gain } = transportHarness({ AudioContext }), audio = media();
  const transport = createDialogueTransport(audio, [voice({ gain_db: 18 })], async () => "/video/film");
  transport.prepare(12.5); await flush(); transport.activate();
  assert.equal(resumes.length, 1, "resume is called synchronously in the Play gesture");
  assert.equal(transport.prepare(12.5), false, "ready HTML media must wait for a running audio graph");
  await transport.play(); assert.equal(audio.paused, true);
  resumes[0].resolve(); await flush(); assert.equal(transport.prepare(12.5), true);
  close(gains[0].gain.value, 10 ** (18 / 20)); assert.equal(audio.volume, 1);
  audio.muted = true; transport.prepare(12.5); assert.equal(gains[0].gain.value, 0);
  transport.dispose(); gain.releaseDialogueAudio(audio);
  const second = createDialogueTransport(audio, [voice()], async () => "/video/film");
  second.activate(); assert.equal(sources.length, 1, "Fast Refresh/Strict Mode must not create a duplicate media source");
  resumes[1].resolve(); await flush(); second.dispose(); gain.releaseDialogueAudio(audio);
});
