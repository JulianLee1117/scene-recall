const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
const film = (id = "film-a", evidence = 23) => ({ film_id: id, unit_id: `${id}_0001`, film_title: id, t_start: 20, t_end: 30, matched_frame_timestamp: evidence, caption: "A scene", matches: [] });
const compiled = ts.transpileModule(fs.readFileSync(path.join(__dirname, "VideoModal.tsx"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const reasons = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "../lib/matchReasons.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: reasons, require: () => ({ formatTime: (value) => `${value}s` }) });
const moments = {}, shotLists = {};
for (const [file, exports] of [["resultMoment.ts", moments], ["sceneShots.ts", shotLists]]) {
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "../lib", file), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, { exports, require: () => moments });
}

function harness({ shot = film(), apiUrl = "http://api.invalid" } = {}) {
  const hooks = [], requests = [], listeners = new Map(), elements = new Map(), bookmarks = [];
  let cursor = 0, output, scheduled = false, disposed = false, effects = [], closes = 0;
  const previousFocus = { isConnected: true, focuses: 0, focus() { this.focuses++; } };
  const document = { activeElement: previousFocus, body: { style: { overflow: "auto" } } };
  const props = { shot, onClose: () => { closes++; }, onToggleBookmark: (value) => bookmarks.push(value) };
  const schedule = () => { if (!scheduled && !disposed) { scheduled = true; queueMicrotask(render); } };
  const react = {
    useId() { cursor++; return "video-modal"; },
    useState(initial) {
      const i = cursor++; hooks[i] ??= { value: typeof initial === "function" ? initial() : initial };
      return [hooks[i].value, (update) => {
        const value = typeof update === "function" ? update(hooks[i].value) : update;
        if (!Object.is(value, hooks[i].value)) { hooks[i].value = value; schedule(); }
      }];
    },
    useRef(initial) { const i = cursor++; return hooks[i] ??= { current: initial }; },
    useCallback(callback, deps) {
      const i = cursor++; if (!hooks[i] || !same(deps, hooks[i].deps)) hooks[i] = { callback, deps };
      return hooks[i].callback;
    },
    useMemo(factory, deps) {
      const i = cursor++; if (!hooks[i] || !same(deps, hooks[i].deps)) hooks[i] = { value: factory(), deps };
      return hooks[i].value;
    },
    useEffect(effect, deps) {
      const i = cursor++;
      if (!hooks[i] || !same(deps, hooks[i].deps)) {
        const old = hooks[i]; hooks[i] = { deps, cleanup: old?.cleanup };
        effects.push(() => { hooks[i].cleanup?.(); hooks[i].cleanup = effect(); });
      }
    },
  };
  const exports = {};
  vm.runInNewContext(compiled, {
    exports, AbortController, document, process: { env: { NEXT_PUBLIC_API_URL: apiUrl } },
    fetch(url, init) { return new Promise((resolve, reject) => requests.push({ url, init, resolve, reject })); },
    window: {
      requestAnimationFrame(callback) { callback(); return 1; }, cancelAnimationFrame() {}, setTimeout() {},
      addEventListener(name, callback) { listeners.set(name, callback); },
      removeEventListener(name, callback) { if (listeners.get(name) === callback) listeners.delete(name); },
    },
    navigator: { clipboard: { writeText: async () => {} } },
    require(name) {
      if (name === "react") return react;
      if (name === "react/jsx-runtime") return { jsx: (type, props, key) => ({ type, props, key }), jsxs: (type, props, key) => ({ type, props, key }) };
      if (name === "@/lib/format") return { filmLabel: String, displayTitle: String, formatTime: (value) => `${value}s` };
      if (name === "@/lib/searchRecipe") return { FACET_LABELS: {} };
      if (name === "@/lib/matchReasons") return reasons;
      if (name === "@/lib/resultMoment") return moments;
      if (name === "@/lib/sceneShots") return shotLists;
      return { default: name };
    },
  });
  function render() {
    if (disposed) return;
    scheduled = false; cursor = 0; output = exports.default(props);
    const activeRefs = new Set();
    for (const node of nodes(output)) {
      const ref = node.props?.ref;
      if (!ref || typeof ref !== "object") continue;
      activeRefs.add(ref);
      if (!elements.has(ref) || elements.get(ref).key !== node.key) {
        elements.set(ref, { key: node.key, element: {
          currentTime: 0, plays: 0, isConnected: true,
          play() { this.plays++; return Promise.resolve(); }, pause() {},
          focus() { document.activeElement = this; }, hasAttribute: () => false,
          querySelectorAll: () => [...elements.values()].map((value) => value.element),
        } });
      }
      ref.current = elements.get(ref).element;
    }
    for (const [ref] of elements) if (!activeRefs.has(ref)) ref.current = null;
    const run = effects; effects = []; run.forEach((effect) => effect());
  }
  const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
  render();
  return {
    requests, bookmarks, listeners, document, previousFocus, flush,
    get state() { return output; }, get closes() { return closes; },
    find: (predicate) => nodes(output).find(predicate),
    video: () => nodes(output).find((node) => node.type === "video"),
    button: (label) => nodes(output).find((node) => node.type === "button" && text(node) === label),
    async resolve(index, body, status = 200) { requests[index].resolve({ ok: status >= 200 && status < 300, status, json: async () => body }); await flush(); },
    async updateShot(next) { props.shot = next; render(); await flush(); },
    dispose() { disposed = true; hooks.forEach((hook) => hook?.cleanup?.()); },
  };
}

test("compatible playback URL loads only after metadata and retains evidence seeking with audio enabled", async () => {
  const app = harness();
  try {
    assert.equal(app.video(), undefined);
    assert.match(text(app.state), /Loading player/);
    assert.equal(app.requests[0].url, "http://api.invalid/video/film-a/playback");
    assert.equal(app.requests[0].init.cache, "no-store");
    await app.resolve(0, { url: "/video/film-a?representation=0123456789abcdef01234567" });
    const video = app.video();
    assert.equal(video.props.src, "http://api.invalid/video/film-a?representation=0123456789abcdef01234567");
    assert.equal(video.props.controls, true);
    assert.notEqual(video.props.muted, true);
    video.props.onCanPlay();
    assert.equal(video.props.ref.current.currentTime, 23);
    assert.equal(video.props.ref.current.plays, 1);
    video.props.ref.current.currentTime = 27;
    video.props.onCanPlay();
    assert.equal(video.props.ref.current.currentTime, 27, "later canplay events do not rewind playback");
    video.props.onTimeUpdate({ currentTarget: { currentTime: 27 } }); await app.flush();
    app.button("Back to result · 23s").props.onClick();
    assert.equal(video.props.ref.current.currentTime, 23);
    app.find((node) => node.props?.["aria-label"] === "Save this shot").props.onClick();
    assert.equal(app.bookmarks[0].matched_frame_timestamp, 23, "saving retains the retrieved anchor after scrubbing");
  } finally { app.dispose(); }
});

test("main-query spoken evidence controls opening and dialogue playback while the source frame stays anchored", async () => {
  const result = { ...film(), matches: [{ clause_id: "main", facet: "all", rank: 1,
    evidence: { type: "text", view: "dialogue", text: "Come with me.", source: "quote", score: .9, t_start: 25, t_end: 27 } }],
    matched_line: { text: "Unrelated words", score: .6, t_start: 80, t_end: 81 } };
  const app = harness({ shot: result });
  try {
    await app.resolve(0, { url: "/video/film-a" });
    const video = app.video();
    video.props.onCanPlay();
    assert.equal(video.props.ref.current.currentTime, 24);
    const dialogue = app.find((node) => node.type === "./ShotDialogue");
    assert.equal(dialogue.props.shot, result, "details stay attached to the result");
    dialogue.props.onSeek(26); await app.flush();
    assert.equal(video.props.ref.current.currentTime, 26);
    assert.equal(video.props.ref.current.plays, 2, "timestamp activation plays even after a pause");
    app.button("Back to result · 25s").props.onClick();
    assert.equal(video.props.ref.current.currentTime, 25);
    app.find((node) => node.props?.["aria-label"] === "Save this shot").props.onClick();
    assert.equal(app.bookmarks[0].matched_frame_timestamp, 23);
    await app.updateShot({ ...result, matches: [{ ...result.matches[0], evidence: { ...result.matches[0].evidence, t_start: 28, t_end: 29 } }] });
    assert.notEqual(app.video().key, video.key, "new selected passage remounts even on the same shot");
  } finally { app.dispose(); }
});

test("a hero result opens and saves its displayed moment, while earlier alternatives stay navigable", async () => {
  const result = { ...film(), t_start: 1102.393, t_end: 1119.201, matched_frame_timestamp: 1114.999,
    keyframe_index: 2, keyframe_url: "/frame/2", hero_url: "/hero", thumbnail_url: "/hero", hero_time: 1105,
    focus_start: 1102.393, focus_end: 1106.595, scene: { title: "Swimming lesson" },
    scene_alternatives: [{ unit_id: "earlier", t_start: 1080, t_end: 1090, keyframe_index: 1, keyframe_url: "/earlier",
      thumbnail_url: "/hero/earlier", hero_time: 1085, scene: { title: "Before the lesson" } }] };
  const app = harness({ shot: result });
  try {
    await app.resolve(0, { url: "/video/film-a" });
    app.video().props.onCanPlay();
    assert.equal(app.video().props.ref.current.currentTime, 1105, "no preroll into a different picture");
    const strip = () => nodes(app.find((node) => node.props?.className === "modal-scene-alternatives")).filter((node) => node.type === "button");
    assert.deepEqual(strip().map(text), ["1085s", "1105s"]);
    assert.deepEqual(strip().map((node) => node.props["aria-pressed"]), [false, true], "chronology does not replace the initially selected best match");
    app.find((node) => node.props?.["aria-label"] === "Save this shot").props.onClick();
    assert.equal(moments.bookmarkAnchor(app.bookmarks[0]).evidence_timestamp, 1105);
    strip()[0].props.onClick(); await app.flush();
    assert.match(text(app.find((node) => node.props?.className === "modal-now")), /Before the lesson/);
    app.button("Back to result · 1105s").props.onClick();
    assert.equal(app.video().props.ref.current.currentTime, 1105);
  } finally { app.dispose(); }
});

test("same-film scene or evidence changes remount the player while reusing playback metadata", async () => {
  const app = harness({ shot: { ...film(), matched_frame_timestamp: undefined } });
  try {
    await app.resolve(0, { url: "/video/film-a" });
    assert.equal(app.video().props.src, "http://api.invalid/video/film-a");
    app.video().props.onCanPlay();
    assert.equal(app.video().props.ref.current.currentTime, 20);
    const firstPlayer = app.video().props.ref.current;
    const firstKey = app.video().key;
    await app.updateShot(film("film-a", 40));
    assert.equal(app.requests.length, 1);
    const nextVideo = app.video();
    assert.notEqual(nextVideo.key, firstKey, "the changed evidence creates a fresh video element");
    assert.notEqual(nextVideo.props.ref.current, firstPlayer);
    assert.equal(nextVideo.props.src, "http://api.invalid/video/film-a");
    // A remounted player loads normally; do not synthesize canplay on the old player.
    nextVideo.props.onCanPlay();
    assert.equal(nextVideo.props.ref.current.currentTime, 40);
    const secondPlayer = nextVideo.props.ref.current;
    const secondKey = nextVideo.key;
    await app.updateShot({ ...film("film-a", 40), unit_id: "film-a_0002" });
    assert.notEqual(app.video().key, secondKey, "changing the scene also remounts at the same evidence time");
    assert.notEqual(app.video().props.ref.current, secondPlayer);
    assert.equal(app.requests.length, 1);
  } finally { app.dispose(); }
});

test("changing films aborts old metadata and ignores a late response", async () => {
  const app = harness();
  try {
    await app.updateShot(film("film-b", 80));
    assert.equal(app.requests[0].init.signal.aborted, true);
    assert.equal(app.video(), undefined);
    await app.resolve(1, { url: "/video/film-b?representation=bbbbbbbbbbbbbbbbbbbbbbbb" });
    await app.resolve(0, { url: "/video/film-a" });
    assert.equal(app.video().props.src, "http://api.invalid/video/film-b?representation=bbbbbbbbbbbbbbbbbbbbbbbb");
    app.video().props.onCanPlay();
    assert.equal(app.video().props.ref.current.currentTime, 80);
    await app.updateShot(film("film-c", 4));
    assert.equal(app.video(), undefined, "the old film is hidden while the next URL resolves");
  } finally { app.dispose(); }
});

test("closing cancels metadata and preserves Escape, focus and scroll cleanup", async () => {
  const app = harness();
  assert.equal(app.document.body.style.overflow, "hidden");
  let prevented = false;
  app.listeners.get("keydown")({ key: "Escape", preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(app.closes, 1);
  app.dispose();
  assert.equal(app.requests[0].init.signal.aborted, true);
  assert.equal(app.document.body.style.overflow, "auto");
  assert.equal(app.previousFocus.focuses, 1);
  assert.equal(app.listeners.size, 0);
  await app.resolve(0, { url: "/video/film-a" });
  assert.equal(app.video(), undefined);
});

test("metadata failure explains the problem and retries without loading the original film", async () => {
  const app = harness();
  try {
    await app.resolve(0, { detail: "Compatible playback is not ready." }, 409);
    assert.match(text(app.find((node) => node.props?.role === "alert")), /Compatible playback is not ready/);
    assert.equal(app.video(), undefined);
    assert.equal(app.requests.length, 1);
    app.button("Retry playback").props.onClick(); await app.flush();
    assert.match(text(app.state), /Loading player/);
    assert.equal(app.requests.length, 2);
    await app.resolve(1, { url: "/video/film-a?representation=aaaaaaaaaaaaaaaaaaaaaaaa" });
    assert.ok(app.video().props.src.includes("representation="));
    assert.equal(app.find((node) => node.props?.role === "alert"), undefined);
  } finally { app.dispose(); }
});

test("network failures have actionable playback guidance", async () => {
  const app = harness();
  try {
    app.requests[0].reject(new TypeError("Failed to fetch")); await app.flush();
    assert.match(text(app.find((node) => node.props?.role === "alert")), /Playback could not be loaded.*API and film.*retry/);
    assert.ok(app.button("Retry playback"));
    assert.equal(app.video(), undefined);
  } finally { app.dispose(); }
});

test("media errors retry URL resolution and restore the original evidence seek", async () => {
  const app = harness();
  try {
    await app.resolve(0, { url: "/video/film-a" });
    app.video().props.onCanPlay();
    app.video().props.onError(); await app.flush();
    assert.match(text(app.find((node) => node.props?.role === "alert")), /This video could not play/);
    assert.equal(app.video(), undefined);
    assert.equal(app.requests.length, 1);
    app.button("Retry playback").props.onClick(); await app.flush();
    await app.resolve(1, { url: "/video/film-a?representation=aaaaaaaaaaaaaaaaaaaaaaaa" });
    app.video().props.onCanPlay();
    assert.equal(app.video().props.ref.current.currentTime, 23);
  } finally { app.dispose(); }
});

test("one action bar offers Save, Related, Match cuts at the playhead and Copy time, and explains matches in words", async () => {
  const shot = { ...film(), unit_id: "film-a_0001", keyframe_index: 2, matched_text_view: "facets", matched_text: "framing: close_up; time of day: dawn_dusk",
    matches: [{ clause_id: "main", facet: "all", rank: 1, evidence: { type: "text", view: "caption", text: "A woman waits on a rooftop" } },
      { clause_id: "composition", facet: "composition", rank: 4, evidence: { type: "frame", timestamp: 23 } }] };
  const app = harness({ shot });
  try {
    await app.resolve(0, { url: "/video/film-a" });
    const link = app.find((node) => node.type === "a" && text(node) === "Match cuts");
    assert.equal(link.props.href, "/match?unit_id=film-a_0001&time=23.000");
    assert.equal(link.props.target, "_blank");
    app.video().props.onTimeUpdate({ currentTarget: { currentTime: 26.5 } }); await app.flush();
    assert.equal(app.find((node) => node.type === "a" && text(node) === "Match cuts").props.href, "/match?unit_id=film-a_0001&time=26.500", "match cuts start from the playhead within the shot");
    app.video().props.onTimeUpdate({ currentTarget: { currentTime: 95 } }); await app.flush();
    assert.equal(app.find((node) => node.type === "a" && text(node) === "Match cuts").props.href, "/match?unit_id=film-a_0001&time=23.000", "outside the shot they fall back to the retrieved moment");
    assert.ok(app.button("Save"));
    assert.ok(app.button("Copy time"));
    const why = app.find((node) => node.props?.className === "modal-reasons");
    assert.match(text(why), /Why it's here/);
    assert.equal(nodes(why).find((node) => node.type === "./MatchBreakdown").props.shot, shot, "the shared breakdown explains the ranking");
    assert.doesNotMatch(text(app.state), /Save and Find related use/);
  } finally { app.dispose(); }
});

test("picking another matching shot updates its facts and actions without following automatic playback", async () => {
  const shot = { ...film(), keyframe_index: 1, scene: { title: "Planetarium" },
    scene_alternatives: [
      { unit_id: "film-a_0004", t_start: 40, t_end: 44, keyframe_url: "/k/4", keyframe_index: 1, thumbnail_url: "/h/4", hero_time: 42.5, scene: { title: "Another scene" }, caption: "A different picture", action: "He leaves", debug: { final_score: 1, channels: { txt: { rank: 2, score: .8, distance: .2 } } }, matches: [{ clause_id: "main", facet: "all", rank: 2, evidence: { type: "text", view: "dialogue", text: "Goodbye" } }] },
      { unit_id: "film-a_0006", t_start: 50, t_end: 70, keyframe_url: "/k/6", keyframe_index: 1 },
    ] };
  const app = harness({ shot });
  try {
    await app.resolve(0, { url: "/video/film-a" });
    const strip = () => nodes(app.find((node) => node.props?.className === "modal-scene-alternatives")).filter((node) => node.type === "button");
    assert.deepEqual(strip().map((button) => button.props["aria-pressed"]), [true, false, false], "the retrieved shot leads the strip");
    const caption = () => text(app.find((node) => node.props?.className === "modal-caption"));

    strip()[1].props.onClick();
    await app.flush();
    assert.equal(app.video().props.ref.current.currentTime, 42.5, "it opens on the frame its thumbnail shows");
    assert.deepEqual(strip().map((button) => button.props["aria-pressed"]), [false, true, false]);
    assert.equal(caption(), "A different picture", "an explicit pick owns the visible facts");
    assert.equal(app.find((node) => node.type === "./ShotDialogue").props.shot.unit_id, "film-a_0004");
    assert.equal(app.find((node) => node.type === "./MatchBreakdown").props.shot.unit_id, "film-a_0004");
    assert.match(text(app.find((node) => node.props?.className === "modal-facts")), /Another scene.*He leaves/);
    assert.equal(strip()[1].props.title, "Pick the shot at 42.5s");
    assert.match(text(app.find((node) => node.props?.className === "modal-time")), /40s – 44s/);
    assert.equal(app.find((node) => node.type === "a" && text(node) === "Match cuts").props.href, "/match?unit_id=film-a_0004&time=42.500");
    app.find((node) => node.props?.["aria-label"] === "Save this shot").props.onClick();
    assert.equal(app.bookmarks[0].unit_id, "film-a_0004");
    assert.equal(moments.bookmarkAnchor(app.bookmarks[0]).evidence_timestamp, 42.5, "it saves the frame its thumbnail showed");
    assert.equal(app.bookmarks[0].scene.title, "Another scene");
    app.video().props.onTimeUpdate({ currentTarget: { currentTime: 51 } }); await app.flush();
    assert.equal(caption(), "A different picture", "automatic playback does not replace the selected facts");
    assert.equal(app.find((node) => node.type === "./ShotDialogue").props.shot.unit_id, "film-a_0004");

    // Without a timestamped frame the shot boundary is the same fallback everywhere.
    strip()[2].props.onClick();
    await app.flush();
    assert.match(text(strip()[2]), /50s/);
    assert.equal(app.video().props.ref.current.currentTime, 50);
    app.find((node) => node.props?.["aria-label"] === "Save this shot").props.onClick();
    assert.equal(moments.bookmarkAnchor(app.bookmarks[1]).evidence_timestamp, 50);

    app.button("Back to result · 23s").props.onClick();
    await app.flush();
    assert.deepEqual(strip().map((button) => button.props["aria-pressed"]), [true, false, false]);
    assert.equal(caption(), "A scene", "Back to result restores its facts");
  } finally { app.dispose(); }
});

test("browsing beyond the scene retargets the actions to the shot on screen, with a way back", async () => {
  const shot = { ...film(), keyframe_index: 1, scene: { id: "s-planetarium", title: "Planetarium" }, action: "She looks up" };
  const app = harness({ shot });
  const now = () => text(app.find((node) => node.props?.className === "modal-now"));
  const matchCuts = () => app.find((node) => node.type === "a" && text(node) === "Match cuts").props.href;
  try {
    await app.resolve(0, { url: "/video/film-a" });
    assert.equal(now(), "Playing 23s · Planetarium", "the line names the scene on screen, the result's included");

    app.video().props.onTimeUpdate({ currentTarget: { currentTime: 95 } }); await app.flush();
    assert.match(app.requests[1].url, /[/]library[/]shot[?]film_id=film-a&t=95$/);
    assert.equal(now(), "Playing 95s", "until the library answers, the line names no scene");
    assert.equal(matchCuts(), "/match?unit_id=film-a_0001&time=23.000", "until the library answers, the result stays the target");
    await app.resolve(1, { ...film(), unit_id: "film-a_0040", t_start: 90, t_end: 100, keyframe_index: 0,
      scene: { id: "s-escape", title: "The Escape" }, action: "A car speeds off" });
    assert.equal(now(), "Playing 95s · The Escape");
    assert.equal(matchCuts(), "/match?unit_id=film-a_0040&time=95.000");
    app.find((node) => node.props?.["aria-label"] === "Save this shot").props.onClick();
    assert.equal(app.bookmarks[0].unit_id, "film-a_0040");
    assert.equal(app.bookmarks[0].evidence_timestamp, 95, "a browsed shot saves the moment on screen");
    assert.equal(app.bookmarks[0].matched_frame_timestamp, 23, "browsing does not relabel the indexed frame");

    // Playing on inside that shot asks the library nothing more.
    app.video().props.onTimeUpdate({ currentTarget: { currentTime: 97 } }); await app.flush();
    assert.equal(app.requests.length, 2);
    // At the next cut the line and the target hold until the next shot arrives: nothing flashes.
    app.video().props.onTimeUpdate({ currentTarget: { currentTime: 101 } }); await app.flush();
    assert.equal(app.requests.length, 3);
    assert.equal(now(), "Playing 101s · The Escape");
    assert.match(matchCuts(), /unit_id=film-a_0040/);
    await app.resolve(2, { ...film(), unit_id: "film-a_0041", t_start: 100, t_end: 104, keyframe_index: 0,
      scene: { id: "s-escape", title: "The Escape" }, action: "Sirens" });
    assert.equal(matchCuts(), "/match?unit_id=film-a_0041&time=101.000");

    app.button("Back to result · 23s").props.onClick(); await app.flush();
    assert.equal(app.video().props.ref.current.currentTime, 23);
    assert.equal(matchCuts(), "/match?unit_id=film-a_0001&time=23.000");
    assert.equal(now(), "Playing 23s · Planetarium");
    const facts = app.find((node) => node.props?.className === "modal-facts");
    assert.deepEqual(nodes(facts).filter((node) => node.type === "dt").map(text), ["Scene", "Shot", "Picture"]);
  } finally { app.dispose(); }
});
