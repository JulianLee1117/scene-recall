const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const compile = (file) => ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const moments = {};
vm.runInNewContext(compile("../lib/resultMoment.ts"), { exports: moments });
const compiled = compile("useBookmarks.ts");
const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));

function harness() {
  const hooks = [], requests = [];
  let cursor = 0, current, scheduled = false, disposed = false, effects = [];
  const schedule = () => { if (!scheduled && !disposed) { scheduled = true; queueMicrotask(render); } };
  const react = {
    useState(initial) {
      const i = cursor++; hooks[i] ??= { value: typeof initial === "function" ? initial() : initial };
      return [hooks[i].value, (value) => { hooks[i].value = typeof value === "function" ? value(hooks[i].value) : value; schedule(); }];
    },
    useRef(value) { return hooks[cursor++] ??= { current: value }; },
    useMemo(factory, deps) {
      const i = cursor++; if (!hooks[i] || !same(deps, hooks[i].deps)) hooks[i] = { value: factory(), deps };
      return hooks[i].value;
    },
    useCallback(callback, deps) { return react.useMemo(() => callback, deps); },
    useEffect(effect, deps) {
      const i = cursor++;
      if (!hooks[i] || !same(deps, hooks[i].deps)) {
        const old = hooks[i]; hooks[i] = { deps, cleanup: old?.cleanup };
        effects.push(() => { hooks[i].cleanup?.(); hooks[i].cleanup = effect(); });
      }
    },
  };
  const exports = {};
  vm.runInNewContext(compiled, { exports, AbortController, process: { env: { NEXT_PUBLIC_API_URL: "http://api.invalid" } },
    fetch(url, init) { return new Promise((resolve) => requests.push({ url, init, resolve })); },
    require(name) {
      if (name === "react") return react;
      if (name === "@/lib/resultMoment") return moments;
      if (name === "@/lib/appClient") return { APP_CLIENT_HEADERS: {} };
      throw new Error(name);
    },
  });
  function render() {
    if (disposed) return;
    scheduled = false; cursor = 0; current = exports.useBookmarks();
    const pending = effects; effects = []; pending.forEach((effect) => effect());
  }
  const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
  render();
  return { requests, flush, get current() { return current; },
    async resolve(index, data) { requests[index].resolve({ ok: true, json: async () => data }); await flush(); },
    dispose() { disposed = true; hooks.forEach((hook) => hook?.cleanup?.()); },
  };
}

test("Save sends the displayed hero time without a conflicting indexed-frame hint", async () => {
  const app = harness();
  const shot = { unit_id: "swim", film_id: "moonlight", t_start: 1102.393, t_end: 1119.201,
    hero_url: "/hero", thumbnail_url: "/hero", hero_time: 1105,
    keyframe_index: 2, keyframe_url: "/keyframe/2", matched_frame_index: 2, matched_frame_timestamp: 1114.999 };
  try {
    await app.resolve(0, { bookmarks: [] });
    const pending = app.current.toggleBookmark(shot); await app.flush();
    assert.equal(app.requests[1].url, "http://api.invalid/bookmarks/swim");
    assert.deepEqual(JSON.parse(app.requests[1].init.body), { evidence_timestamp: 1105, frame_index: null });
    assert.equal(app.current.bookmarks[0].scene.thumbnail_url, "/hero", "optimistic UI keeps the visible picture");
    const saved = { bookmark_id: "saved", source_unit_id: "swim", film_id: "moonlight", evidence_timestamp: 1105,
      scene: { ...shot, thumbnail_url: "/media/frame/moonlight?t=1105", evidence_timestamp: 1105 } };
    await app.resolve(1, saved); await pending;
    assert.equal(app.current.bookmarks[0].scene.thumbnail_url, saved.scene.thumbnail_url);
    assert.equal(moments.displayMoment(app.current.bookmarks[0].scene), 1105);
  } finally { app.dispose(); }
});
