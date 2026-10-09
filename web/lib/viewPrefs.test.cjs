const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");

const lib = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "viewPrefs.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: lib });

const plain = (value) => JSON.parse(JSON.stringify(value));

test("saved view settings are read back, and anything unknown falls back to the default", () => {
  assert.deepEqual(plain(lib.parseViewPrefs(JSON.stringify({ order: "gems", size: "large", details: true }))),
    { order: "gems", size: "large", details: true });
  assert.deepEqual(plain(lib.parseViewPrefs(JSON.stringify({ order: "loud", size: "huge", details: "yes" }))), plain(lib.DEFAULT_VIEW));
  for (const raw of [null, "", "not json", "null", "7"]) assert.deepEqual(plain(lib.parseViewPrefs(raw)), plain(lib.DEFAULT_VIEW));
});
