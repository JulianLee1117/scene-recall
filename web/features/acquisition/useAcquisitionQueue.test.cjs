const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function load(file, modules, globals = {}) {
  const source = ts.transpileModule(fs.readFileSync(path.join(__dirname, file), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const exports = {};
  vm.runInNewContext(source, {
    exports, AbortController, FormData,
    require(name) {
      if (!(name in modules)) throw new Error(`Unexpected module: ${name}`);
      return modules[name];
    },
    ...globals,
  });
  return exports;
}

const model = load("model.ts", {});
const connected = { downloader: { configured: true, available: true }, search: { configured: true, available: true }, monitor: { running: true } };
const film = (status = "ingest_queued", id = "film") => ({ id, status, film_path: `V:/films/${id}.mkv` });

function queueHarness(onLibraryChange = async () => {}) {
  const hooks = [], requests = [], timers = new Map();
  let cursor = 0, timerId = 0, scheduled = false, disposed = false, output, effects = [];
  const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
  const schedule = () => {
    if (!scheduled && !disposed) { scheduled = true; queueMicrotask(render); }
  };
  const react = {
    useState(initial) {
      const index = cursor++;
      hooks[index] ??= { value: typeof initial === "function" ? initial() : initial };
      return [hooks[index].value, (update) => {
        const value = typeof update === "function" ? update(hooks[index].value) : update;
        if (!Object.is(value, hooks[index].value)) { hooks[index].value = value; schedule(); }
      }];
    },
    useRef(initial) { const index = cursor++; return hooks[index] ??= { current: initial }; },
    useCallback(callback, deps) {
      const index = cursor++;
      if (!hooks[index] || !same(deps, hooks[index].deps)) hooks[index] = { callback, deps };
      return hooks[index].callback;
    },
    useEffect(effect, deps) {
      const index = cursor++;
      if (!hooks[index] || !same(deps, hooks[index].deps)) {
        const old = hooks[index];
        hooks[index] = { deps, cleanup: old?.cleanup };
        effects.push(() => { hooks[index].cleanup?.(); hooks[index].cleanup = effect(); });
      }
    },
  };
  const api = {
    acquisitionRequest(url, init) {
      const request = { url, init, handled: false, ...deferred() };
      requests.push(request);
      return request.promise;
    },
    messageOf: (error) => error.message,
  };
  const { useAcquisitionQueue } = load("useAcquisitionQueue.ts", { react, "./api": api, "./model": model }, {
    window: { addEventListener() {}, removeEventListener() {} },
    setTimeout(callback) { timers.set(++timerId, callback); return timerId; },
    clearTimeout(id) { timers.delete(id); },
  });
  function render() {
    if (disposed) return;
    scheduled = false; cursor = 0;
    output = useAcquisitionQueue(onLibraryChange);
    const run = effects; effects = []; run.forEach((effect) => effect());
  }
  function settle(url, outcome, value) {
    const request = requests.find((request) => request.url === url && !request.handled);
    assert.ok(request, `Expected a pending ${url || "queue"} request`);
    request.handled = true;
    request[outcome](value);
    return request;
  }
  render();
  return {
    get state() { return output; }, requests, timers,
    resolve: (url, value) => settle(url, "resolve", value),
    reject: (url, error) => settle(url, "reject", error),
    async flush() { for (let i = 0; i < 40; i++) await Promise.resolve(); },
    dispose() { disposed = true; hooks.forEach((hook) => hook.cleanup?.()); },
  };
}

test("a slow downloader health check never blocks queue display or a successful action", async () => {
  const app = queueHarness();
  try {
    app.resolve("", { items: [film()] });
    await app.flush();
    assert.equal(app.state.loading, false);
    assert.equal(app.state.items[0].id, "film");
    assert.equal(app.state.status, null, "queue renders before downloader health responds");

    const action = app.state.execute("add", "/magnet", { title: "Another film" });
    app.resolve("/magnet", { item: film("queued", "another") });
    await app.flush();
    app.resolve("", { items: [film(), film("queued", "another")] });
    assert.equal(await action, true);
    await app.flush();
    assert.equal(app.state.busy, null);
    assert.equal(app.state.items.length, 2);
    assert.equal(app.requests.filter((request) => request.url === "/status").length, 1, "one pending health request survives queue polls");

    app.resolve("/status", connected);
    await app.flush();
    assert.equal(app.state.status.downloader.available, true);
  } finally { app.dispose(); }
});

test("accepted cancellation stays authoritative after failed refresh and polls through cleanup errors", async () => {
  const app = queueHarness();
  try {
    app.resolve("", { items: [film("failed")] });
    app.resolve("/status", connected);
    await app.flush();
    const accepted = app.state.execute("film", "/film/cancel", { revision: 7 });
    const cancelling = { ...film("cancelling"), revision: 8, cancel_requested: true, cancellation_cleanup: "pending" };
    assert.equal(await app.state.execute("film", "/film/cancel", { revision: 7 }), false, "no simultaneous duplicate mutation");
    app.resolve("/film/cancel", { item: cancelling });
    await app.flush();
    app.reject("", new Error("Queue temporarily unavailable"));
    assert.equal(await accepted, true);
    await app.flush();
    assert.equal(app.state.items[0].status, "cancelling");
    assert.equal(model.canCancel(app.state.items[0]), false);
    assert.equal(app.timers.size, 1, "cleanup continues polling even when its originating acquisition failed");
    assert.match(app.state.error, /Queue temporarily unavailable/);

    const pending = app.state.refresh();
    app.resolve("", { items: [{ ...cancelling, error: "File is locked" }] });
    await pending; await app.flush();
    assert.equal(app.state.items[0].error, "File is locked");
    assert.equal(app.timers.size, 1);

    const done = app.state.refresh();
    app.resolve("", { items: [{ ...cancelling, status: "cancelled", cancellation_cleanup: "complete", error: null }] });
    await done; await app.flush();
    assert.equal(app.timers.size, 0, "polling ends only after cleanup completes");
    assert.equal(app.state.error, null);
  } finally { app.dispose(); }
});

test("Refresh retries a failed library update even when the imported queue item is unchanged", async () => {
  let attempts = 0;
  const app = queueHarness(async () => {
    attempts += 1;
    if (attempts === 1) throw new Error("Temporary library outage");
  });
  try {
    app.resolve("", { items: [film("ready")] });
    await app.flush();
    assert.equal(attempts, 1);
    assert.match(app.state.error, /library could not refresh/);

    const refreshed = app.state.refresh();
    app.resolve("", { items: [film("ready")] });
    await refreshed;
    await app.flush();
    assert.equal(attempts, 2);
    assert.equal(app.state.error, null);
    assert.equal(app.timers.size, 0, "recovery works even after all films have finished");
  } finally { app.dispose(); }
});

test("an import arriving during a library refresh triggers a second update without overlapping requests", async () => {
  const first = deferred(), second = deferred();
  let attempts = 0;
  const app = queueHarness(() => (++attempts === 1 ? first.promise : second.promise));
  try {
    app.resolve("", { items: [film("ready")] });
    await app.flush();
    const refreshed = app.state.refresh();
    app.resolve("", { items: [film("ready"), film("ready", "another")] });
    await refreshed;
    await app.flush();
    assert.equal(attempts, 1, "do not overlap library requests");
    first.resolve();
    await app.flush();
    assert.equal(attempts, 2, "the older successful request must not clear a newer change");
    second.resolve();
    await app.flush();
    assert.equal(app.state.error, null);
  } finally { app.dispose(); }
});

test("polling preserves an actionable mutation error until the user retries", async () => {
  const app = queueHarness();
  try {
    app.resolve("", { items: [film()] });
    await app.flush();
    const failed = app.state.execute("add", "/magnet", {});
    app.reject("/magnet", new Error("This film is already in the queue."));
    await app.flush();
    app.resolve("", { items: [film()] });
    assert.equal(await failed, false);
    await app.flush();

    const refreshed = app.state.refresh();
    app.resolve("", { items: [film("ingesting")] });
    await refreshed;
    app.resolve("/status", connected);
    await app.flush();
    assert.equal(app.state.error, "This film is already in the queue.");

    const retried = app.state.execute("add", "/magnet", {});
    await app.flush();
    assert.equal(app.state.error, null);
    app.resolve("/magnet", { item: film() });
    await app.flush();
    app.resolve("", { items: [film()] });
    assert.equal(await retried, true);
  } finally { app.dispose(); }
});

test("superseded queue failures and late unmounted responses cannot replace current UI state", async () => {
  const app = queueHarness();
  try {
    const old = app.requests[0];
    const refreshed = app.state.refresh();
    assert.equal(old.init.signal.aborted, true);
    app.requests[2].handled = true;
    app.requests[2].resolve({ items: [film("ready", "current")] });
    await refreshed;
    app.reject("", new Error("Stale request failed"));
    await app.flush();
    assert.equal(app.state.items[0].id, "current");
    assert.equal(app.state.error, null);

    app.state.refresh();
    app.dispose();
    assert.equal(app.requests.at(-1).init.signal.aborted, true);
    assert.equal(app.requests[1].init.signal.aborted, true);
    app.resolve("", { items: [] });
    app.reject("/status", new Error("Late health failure"));
    await app.flush();
    assert.equal(app.state.items[0].id, "current");
    assert.equal(app.state.error, null);
  } finally { app.dispose(); }
});

test("editing a request clears its previous action error without hiding a queue outage", async () => {
  const app = queueHarness();
  try {
    app.resolve("", { items: [film()] });
    await app.flush();
    const clearError = app.state.clearMutationError;
    const failed = app.state.execute("add", "/magnet", {});
    app.reject("/magnet", new Error("This film is already queued."));
    await app.flush();
    app.reject("", new Error("Queue status is temporarily unavailable."));
    assert.equal(await failed, false);
    await app.flush();
    assert.equal(app.state.error, "This film is already queued.");
    assert.equal(app.state.clearMutationError, clearError, "the form receives a stable edit callback");
    app.state.clearMutationError();
    await app.flush();
    assert.equal(app.state.error, "Queue status is temporarily unavailable.");
  } finally { app.dispose(); }
});
