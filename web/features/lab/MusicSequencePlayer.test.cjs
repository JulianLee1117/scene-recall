const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const compile = (file) => ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const position = {};
vm.runInNewContext(compile("sequencePosition.ts"), { exports: position });
const dialogue = {};
vm.runInNewContext(compile("dialogueAudio.ts"), { exports: dialogue });
const dialogueGain = {};
vm.runInNewContext(compile("dialogueGain.ts"), { exports: dialogueGain });
const compiled = compile("MusicSequencePlayer.tsx");
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
const same = (a, b) => a && b && a.length === b.length && a.every((value, index) => Object.is(value, b[index]));

async function setup({ roundSourceTimes = false, sourceStart = 50, dialogueClips = [], resolveVoice, activateVoice } = {}) {
  const hooks = [], effects = [], playback = [];
  const playerRef = { current: null };
  const stats = { passiveRuns: 0, passiveStateUpdates: 0, transportStarts: 0 };
  let cursor = 0, scheduled = false, disposed = false, tree, clock, videoIndex = 0, passive = false;
  const media = (round = false) => {
    let time = 0;
    return { paused: true, readyState: 4, seeking: false, duration: 10000,
      seekCount: 0,
      get currentTime() { return time; }, set currentTime(value) { this.seekCount += 1; time = round ? Math.floor(value * 1e6) / 1e6 : value; },
      videoWidth: 1920, videoHeight: 1080, error: null, load() {}, pause() { this.paused = true; }, async play() { this.paused = false; } };
  };
  const audio = media(), voice = media(), voices = new Map(), videos = [media(roundSourceTimes), media(roundSourceTimes)];
  const dialogueTransport = {}, sourceRequests = [];
  const gain = activateVoice ? { ...dialogueGain, activateDialogueAudio: activateVoice } : dialogueGain;
  vm.runInNewContext(compile("dialogueTransport.ts"), { exports: dialogueTransport, AbortController, URLSearchParams,
    fetch: async (url, options) => { sourceRequests.push(url); return resolveVoice ? resolveVoice(url, options) : { ok: true, json: async () => ({ url: url.replace("/playback", "?representation=browser") }) }; },
    require(name) { if (name === "./dialogueAudio") return dialogue; if (name === "./dialogueGain") return gain; if (name === "@/lib/lab") return { mediaUrl: (value) => value }; throw new Error(name); },
  });
  const schedule = () => { if (!scheduled && !disposed) { scheduled = true; queueMicrotask(render); } };
  const react = {
    useState(initial) {
      const index = cursor++; hooks[index] ??= { value: typeof initial === "function" ? initial() : initial };
      return [hooks[index].value, (update) => { const next = typeof update === "function" ? update(hooks[index].value) : update;
        if (passive) stats.passiveStateUpdates += 1;
        if (!Object.is(next, hooks[index].value)) { hooks[index].value = next; schedule(); } }];
    },
    useRef(initial) { return hooks[cursor++] ??= { current: initial }; },
    useMemo(compute, deps) { const index = cursor++; if (!same(hooks[index]?.deps, deps)) hooks[index] = { value: compute(), deps }; return hooks[index].value; },
    useEffect(effect, deps) { const index = cursor++; if (!same(hooks[index]?.deps, deps)) {
      hooks[index] = { deps, cleanup: hooks[index]?.cleanup };
      effects.push(() => { passive = true; stats.passiveRuns += 1;
        try { hooks[index].cleanup?.(); hooks[index].cleanup = effect(); } finally { passive = false; } });
    } },
    useImperativeHandle(ref, create, deps) { const index = cursor++, dependencies = [...deps, ref];
      if (!same(hooks[index]?.deps, dependencies)) {
        hooks[index] = { deps: dependencies, cleanup: hooks[index]?.cleanup };
        effects.push(() => { hooks[index].cleanup?.(); if (!ref) return;
          const value = create();
          if (typeof ref === "function") { const cleanup = ref(value); hooks[index].cleanup = typeof cleanup === "function" ? cleanup : () => ref(null); }
          else { ref.current = value; hooks[index].cleanup = () => { ref.current = null; }; }
        });
      }
    },
  };
  const slots = [{ id: "first", start: 12, end: 16.94, clip_id: "a" }, { id: "second", start: 16.94, end: 18, clip_id: "b" }, { id: "gap", start: 18, end: 20, clip_id: null }];
  const props = {
    document: { passage: { start: 12, end: 20 }, track: { id: "song" }, aspect_ratio: "16:9", fps: 24,
      dialogue_clips: dialogueClips,
      clips: [{ id: "a", film_id: "film-a", source_start: sourceStart, source_end: sourceStart + 4.94, title: "A quiet room" }, { id: "b", film_id: "film-b", source_start: 100, source_end: 101.06, title: "A passing taxi" }] },
    slots, filmTitles: { "film-a": "The Master (2012)", "film-b": "Taxi Driver (1976)" },
    playhead: 12, ref: playerRef,
    onTimeChange(time) { if (!Object.is(props.playhead, time)) { props.playhead = time; schedule(); } },
    onPlayingChange: (value) => playback.push(value),
  };
  const jsx = (type, props) => {
    if (type === "audio" && props["aria-label"] === "Dialogue audio") {
      const id = props["data-dialogue-id"];
      if (!voices.has(id)) {
        const element = voices.size ? media() : voice;
        element.load = () => { sourceRequests.push(element.src); element.error = null; };
        voices.set(id, element);
      }
      const element = voices.get(id); element.muted = props.muted;
      props.ref(element);
    } else if (type === "audio") props.ref.current = audio;
    if (type === "video") props.ref(videos[videoIndex++]);
    return { type, props };
  };
  const exported = {};
  vm.runInNewContext(compiled, { exports: exported, performance, DOMException,
    window: { setInterval: (callback) => { stats.transportStarts += 1; clock = callback; return 1; }, clearInterval() {}, addEventListener() {}, removeEventListener() {},
      document: { hidden: false, addEventListener() {}, removeEventListener() {}, querySelector: () => null } },
    require(name) {
      if (name === "react") return react;
      if (name === "react/jsx-runtime") return { jsx, jsxs: jsx };
      if (name === "./sequencePosition") return position;
      if (name === "./dialogueAudio") return dialogue;
      if (name === "./dialogueTransport") return dialogueTransport;
      if (name === "./dialogueGain") return dialogueGain;
      if (name === "./musicEdit") return { directionOf: () => ({ query: "" }) };
      if (name === "@/lib/lab") return { mediaUrl: (value) => value, seconds: (value) => value.toFixed(2), experimentName: () => "AI Music Video" };
      return { default: new Proxy({}, { get: (_, key) => key }) };
    },
  });
  function render() { if (disposed) return; cursor = 0; videoIndex = 0; scheduled = false; tree = exported.default(props); effects.splice(0).forEach((run) => run()); }
  render(); await flush();
  return { props, audio, voice, voices, sourceRequests, videos, playback, flush, stats,
    get handle() { return playerRef.current; },
    get nodes() { return nodes(tree); }, get text() { return text(tree); },
    button(label) { return nodes(tree).find((node) => node.type === "button" && node.props["aria-label"] === label); },
    async tick(time) { audio.currentTime = time; clock(); await flush(); },
    async seek(time) { assert.ok(playerRef.current, "mounted player exposes its transport"); playerRef.current.seek(time); await flush(); },
    async update(patch) { Object.assign(props, patch); render(); await flush(); },
    dispose() { disposed = true; hooks.forEach((hook) => hook.cleanup?.()); },
  };
}

test("canonical movie title and saved description follow the playing slot across rounded cuts and seeks", async () => {
  const app = await setup();
  try {
    assert.ok(app.text.includes("The Master (2012)")); assert.ok(app.text.includes("A quiet room"));
    await app.tick(16.939999);
    assert.ok(app.text.includes("Taxi Driver (1976)")); assert.ok(app.text.includes("A passing taxi"));
    assert.equal(app.text.includes("The Master (2012)"), false);
    app.button("Previous cut").props.onClick(); await app.flush();
    assert.ok(app.text.includes("The Master (2012)"));
    await app.update({ filmTitles: {} });
    assert.ok(app.text.includes("Film title unavailable")); assert.ok(app.text.includes("A quiet room"));
    await app.seek(18);
    assert.equal(app.text.includes("Film title unavailable"), false, "gaps never retain the last film title");
  } finally { app.dispose(); }
});

test("independent voice follows song offsets over B-roll cuts, freezes for buffering, and stops on scrub or suspension", async () => {
  const overlay = { id: "voice", film_id: "voice-film", source_start: 200, source_end: 205, start: 14,
    gain_db: 0, fade_in_seconds: .08, fade_out_seconds: .12, music_duck_db: -8 };
  const app = await setup({ dialogueClips: [overlay] });
  try {
    await app.seek(15); await app.tick(15);
    assert.equal(app.voice.currentTime, 1, "prepared audio time zero maps to the selected source in");
    assert.equal(app.voice.paused, true);
    app.button("Play preview").props.onClick(); await app.flush(); await app.tick(15);
    assert.equal(app.voice.paused, false);
    assert.equal(app.audio.paused, false);
    assert.ok(Math.abs(app.audio.volume - 10 ** (-8 / 20)) < 1e-8);
    await app.tick(17);
    assert.equal(app.voice.currentTime, 3);
    assert.equal(app.voice.paused, false);
    assert.equal(app.sourceRequests.length, 1, "changing the B-roll must not reload the voice");
    app.voice.readyState = 2; await app.tick(17.1);
    assert.equal(app.audio.paused, true, "the song waits for required voice data");
    app.voice.readyState = 4; await app.tick(17.1);
    assert.equal(app.audio.paused, false);
    await app.update({ suspended: true }); assert.equal(app.voice.paused, true);
    await app.update({ suspended: false }); await app.seek(19);
    assert.equal(app.voice.paused, true, "voice does not leak beyond its independent end");
  } finally { app.dispose(); }
  assert.equal(app.voice.paused, true);
});

test("failed dialogue decoding interrupts the live preview with a source error instead of indefinite buffering", async () => {
  const app = await setup({ dialogueClips: [{ id: "voice", film_id: "voice-film", source_start: 200, source_end: 205, start: 14,
    gain_db: 0, fade_in_seconds: .08, fade_out_seconds: .12, music_duck_db: -8 }] });
  try {
    await app.seek(15); await app.tick(15);
    app.voice.readyState = 0; app.voice.error = { code: 4 };
    await app.tick(15);
    assert.ok(app.text.includes("Dialogue audio could not be played"));
    assert.equal(app.audio.paused, true);
    assert.equal(app.voice.paused, true);
  } finally { app.dispose(); }
});

test("audio activation failure pauses immediately, survives later source preload, and recovers on a new Play gesture", async () => {
  let attempts = 0;
  const app = await setup({
    dialogueClips: [{ id: "later", film_id: "film", start: 16, source_start: 100, source_end: 103,
      gain_db: 0, fade_in_seconds: .08, fade_out_seconds: .12, music_duck_db: -8 }],
    activateVoice: async () => { if (++attempts === 1) throw new Error("AudioContext resume rejected"); },
  });
  try {
    await app.tick(12); assert.equal(app.sourceRequests.length, 0, "Play occurs before the preload window");
    app.button("Play preview").props.onClick(); await app.flush(); await app.tick(12);
    assert.ok(app.text.includes("Dialogue audio could not start"));
    assert.equal(app.audio.paused, true); assert.equal(app.voice.paused, true);
    await app.seek(13); await app.tick(13);
    assert.equal(app.sourceRequests.length, 1, "the upcoming source can still be prepared");
    assert.ok(app.text.includes("Dialogue audio could not start"), "source selection cannot erase the activation error");
    await app.seek(16); await app.tick(16);
    assert.ok(app.text.includes("Dialogue audio could not start"));
    assert.equal(app.text.includes("Preparing playback"), false, "a known failure is never shown as indefinite buffering");
    assert.equal(app.audio.paused, true); assert.equal(app.voice.paused, true);
    app.button("Play preview").props.onClick(); await app.flush(); await app.tick(16);
    assert.equal(attempts, 2); assert.equal(app.audio.paused, false); assert.equal(app.voice.paused, false);
    assert.equal(app.text.includes("Dialogue audio could not start"), false);
  } finally { app.dispose(); }
});

test("overlapping voices have independent prepared decks, preload only nearby, and pause or seek together", async () => {
  const makeVoice = (id, start, duration) => ({ id, film_id: `${id}-film`, source_start: 200, source_end: 200 + duration, start,
    gain_db: 0, fade_in_seconds: .08, fade_out_seconds: .12, music_duck_db: -8, source_audio_mode: "voice_focus" });
  const app = await setup({ dialogueClips: [makeVoice("one", 14, 3), makeVoice("two", 16, 3), makeVoice("far", 40, 2)] });
  try {
    await app.tick(12);
    assert.equal(app.sourceRequests.length, 1, "only the voice within three seconds is prepared");
    assert.equal(app.voices.get("two").src, undefined);
    assert.equal(app.voices.get("far").src, undefined);
    await app.seek(16.5); await app.tick(16.5);
    const first = app.voices.get("one"), second = app.voices.get("two");
    assert.equal(first.currentTime, 2.5); assert.equal(second.currentTime, .5);
    assert.equal(first.paused, true); assert.equal(second.paused, true);
    app.button("Play preview").props.onClick(); await app.flush(); await app.tick(16.5);
    assert.equal(first.paused, false); assert.equal(second.paused, false); assert.equal(app.audio.paused, false);
    first.currentTime = 3; second.currentTime = 1.1; await app.tick(17.1);
    assert.equal(first.paused, true); assert.equal(second.paused, false, "ending one voice cannot stop the overlapping line");
    assert.equal(app.audio.paused, false);
    await app.update({ suspended: true }); assert.equal(first.paused, true); assert.equal(second.paused, true);
    await app.update({ suspended: false }); await app.seek(16.5); await app.tick(16.5);
    assert.equal(first.currentTime, 2.5); assert.equal(second.currentTime, .5);
    assert.equal(app.voices.get("far").src, undefined);
  } finally { app.dispose(); }
  assert.ok([...app.voices.values()].every((voice) => voice.paused));
});

test("source seeks land inside a trim whose exact native boundary rounds down in HTML media", async () => {
  const sourceStart = 2892.5980416666666;
  const app = await setup({ roundSourceTimes: true, sourceStart });
  try {
    const source = app.videos.find((video) => video.src?.includes("film-a"));
    assert.ok(source.currentTime >= sourceStart, `${source.currentTime} must not show the preceding native frame`);
    assert.ok(source.currentTime < sourceStart + 0.002, "seek inset must not skip a video frame");
    source.currentTime = sourceStart - 0.01;
    await app.seek(12);
    assert.ok(source.currentTime >= sourceStart, "the old 15ms tolerance must not keep a nearby frame outside the trim");
  } finally { app.dispose(); }
});

test("paused cuts retain the valid outgoing deck until the incoming seek has decoded", async () => {
  const app = await setup();
  try {
    await app.update({
      slots: app.props.slots.map((slot) => slot.id === "gap" ? { ...slot, clip_id: "c" } : slot),
      document: { ...app.props.document, clips: [...app.props.document.clips, { id: "c", film_id: "film-c", source_start: 200, source_end: 202, title: "Third scene" }] },
    });
    await app.tick(12);
    const incoming = app.videos.find((video) => video.src?.includes("film-b"));
    incoming.seeking = true;
    await app.seek(17);
    const visible = () => app.nodes.filter((node) => node.props?.className === "cropViewport" && node.props.style.visibility === "visible");
    assert.equal(visible()[0]?.props.children.props["aria-label"], "Sequence video");
    const sourceVideo = visible()[0]?.props.children;
    assert.equal(sourceVideo, app.nodes.filter((node) => node.type === "video")[0], "hold the previous source, not an unacknowledged incoming seek");
    assert.ok(app.videos[0].src.includes("film-a"), "preloading the third shot cannot overwrite the visible outgoing deck");
    assert.ok(app.text.includes("Preparing playback"));
    incoming.seeking = false;
    app.nodes.filter((node) => node.type === "video")[1].props.onSeeked(); await app.flush();
    assert.equal(visible()[0]?.props.children, app.nodes.filter((node) => node.type === "video")[1]);
    assert.equal(app.text.includes("Preparing playback"), false);
    assert.ok(app.videos[0].src.includes("film-c"), "preloading resumes once the incoming deck is visible");
  } finally { app.dispose(); }
});

test("video ahead of the music cannot escape the trim; its final frame holds without a seek loop until the cut", async () => {
  const app = await setup();
  try {
    app.button("Play preview").props.onClick(); await app.flush();
    const source = app.videos.find((video) => video.src?.includes("film-a"));
    source.currentTime = 54.955; // Only 45ms from the audio-derived target: inside the old drift tolerance.
    await app.tick(16.91);
    assert.ok(source.currentTime < 54.94, "never accept the next native shot as harmless drift");
    assert.equal(source.paused, true);
    assert.equal(app.audio.paused, false, "music continues over the held final output frame");
    const held = source.currentTime;
    source.readyState = 2;
    await app.tick(16.925); await app.tick(16.935);
    assert.equal(source.currentTime, held, "holding must not cause repeated correction seeks");
    assert.equal(app.audio.paused, false, "a held decoded frame does not need future video data");
    await app.tick(16.94);
    assert.ok(app.text.includes("Taxi Driver (1976)"));
    assert.equal(app.videos.find((video) => video.src?.includes("film-b")).paused, false);
  } finally { app.dispose(); }
});

test("timeline playback status reflects ready media rather than a pending Play request, and resets on pause, suspension and unmount", async () => {
  const app = await setup();
  try {
    app.audio.readyState = 0;
    app.button("Play preview").props.onClick(); await app.flush();
    assert.deepEqual(app.playback, [false], "waiting for music must not start following");
    app.audio.readyState = 4;
    app.nodes.find((node) => node.type === "audio").props.onCanPlay(); await app.flush();
    assert.equal(app.playback.at(-1), true);
    app.nodes.find((node) => node.type === "audio").props.onWaiting(); await app.flush();
    assert.equal(app.playback.at(-1), false);
    app.nodes.find((node) => node.type === "audio").props.onCanPlay(); await app.flush();
    assert.equal(app.playback.at(-1), true);
    app.button("Pause preview").props.onClick(); await app.flush();
    assert.equal(app.playback.at(-1), false);
    app.button("Play preview").props.onClick(); await app.flush();
    await app.update({ suspended: true });
    assert.equal(app.playback.at(-1), false);
    await app.update({ suspended: false });
    app.button("Play preview").props.onClick(); await app.flush();
    assert.equal(app.playback.at(-1), true);
  } finally { app.dispose(); }
  assert.equal(app.playback.at(-1), false);
});

test("a failed inactive preload stops with a recoverable source error when its cut becomes active", async () => {
  const app = await setup();
  try {
    const incoming = app.videos[1];
    incoming.readyState = 0;
    incoming.error = { code: 4 };
    app.nodes.filter((node) => node.type === "video")[1].props.onError(); await app.flush();
    assert.equal(app.nodes.some((node) => node.props?.role === "alert"), false, "an unused preload does not interrupt the current scene");
    app.button("Play preview").props.onClick(); await app.flush();
    assert.equal(app.audio.paused, false);
    await app.tick(16.94); await app.tick(16.94);
    assert.ok(app.text.includes("This source could not be played"));
    assert.equal(app.text.includes("Preparing playback"), false);
    assert.equal(app.audio.paused, true);
    assert.equal(app.playback.at(-1), false);
    assert.ok(app.button("Play preview"), "the failed cut stops instead of keeping a pending Play request");
    incoming.load = () => { incoming.error = null; incoming.readyState = 4; };
    app.button("Play preview").props.onClick(); await app.flush();
    assert.equal(app.nodes.some((node) => node.props?.role === "alert"), false);
    assert.equal(app.audio.paused, false, "Play can retry the failed source");
  } finally { app.dispose(); }
});

test("an audio error remains visible while the selected source is still seeking", async () => {
  const app = await setup();
  try {
    app.videos[1].seeking = true;
    await app.seek(17);
    assert.ok(app.text.includes("Preparing playback"));
    app.audio.error = { code: 4 };
    await app.tick(17);
    assert.ok(app.text.includes("The music could not be played"));
    assert.equal(app.text.includes("Preparing playback"), false);
    assert.equal(app.audio.paused, true);
  } finally { app.dispose(); }
});

test("sustained scrub commands update media directly across clips and gaps without passive-effect updates", async () => {
  const app = await setup();
  try {
    app.button("Play preview").props.onClick(); await app.flush();
    const before = { ...app.stats };
    const identity = app.handle;
    for (let i = 0; i < 180; i++) {
      const time = 12.05 + ((i * 37) % 780) / 100;
      app.handle.seek(time);
      assert.equal(app.audio.currentTime, time, "the command reaches the media before a render/effect flush");
      await app.flush();
      assert.equal(app.props.playhead, time, "reported time can render the parent without requesting another seek");
      assert.equal(app.audio.paused, false, "a scrub preserves the intent to play after the new source is ready");
      if (time < 16.94) assert.ok(app.text.includes("The Master (2012)"));
      else if (time < 18) assert.ok(app.text.includes("Taxi Driver (1976)"));
      else assert.equal(app.text.includes("Taxi Driver (1976)"), false, "gaps clear the previous film");
    }
    assert.equal(app.handle, identity, "the event handle remains stable through media-clock renders");
    assert.deepEqual(app.stats, before, "scrubbing neither starts new passive updates nor rebuilds the transport");
  } finally { app.dispose(); }
});

test("an explicit same-time seek can repair source position and the handle follows a rebuilt passage", async () => {
  const app = await setup();
  const handle = app.handle;
  try {
    await app.seek(13);
    const source = app.videos.find((video) => video.src?.includes("film-a"));
    source.currentTime = 52;
    const beforeSeek = app.audio.seekCount;
    await app.seek(13);
    assert.equal(app.audio.seekCount, beforeSeek + 1, "an intentional repeated command is not discarded");
    assert.ok(Math.abs(source.currentTime - 51.001) < 1e-6, "source drift is corrected even when timeline time is unchanged");
    await app.update({
      document: { ...app.props.document, passage: { start: 20, end: 28 } },
      slots: app.props.slots.map((slot) => ({ ...slot, start: slot.start + 8, end: slot.end + 8 })),
    });
    const before = { ...app.stats };
    assert.equal(app.handle, handle);
    await app.seek(25);
    assert.equal(app.audio.currentTime, 25);
    assert.ok(app.text.includes("Taxi Driver (1976)"), "the stable handle consults the current passage transport");
    await app.seek(12);
    assert.equal(app.audio.currentTime, 20, "seek clamps against the new passage bounds");
    assert.deepEqual(app.stats, before);
  } finally { app.dispose(); }
  assert.equal(app.handle, null, "unmount removes the public transport handle");
  handle.seek(25);
  assert.equal(app.audio.currentTime, 20, "a retained handle cannot command a disposed transport");
});

test("an edit with render effects says the preview plays cuts and opens the latest export when there is one", async () => {
  const player = await setup();
  assert.ok(!player.text.includes("export"));
  await player.update({ document: { ...player.props.document, effects: [{ id: "tv", kind: "screen", start: 12, end: 13 }] } });
  assert.ok(player.text.includes("Effects show in an export"));
  let opened = 0;
  await player.update({ onShowExport: () => { opened += 1; } });
  const note = player.nodes.find((node) => node.type === "button" && node.props.className === "effectsNote");
  assert.ok(note && text(note) === "Effects play in the export");
  note.props.onClick();
  assert.equal(opened, 1);
  player.dispose();
});
