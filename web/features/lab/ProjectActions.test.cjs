const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const compiled = ts.transpileModule(fs.readFileSync(path.join(__dirname, "ProjectActions.tsx"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
function setup(overrides = {}, component = "default") {
  const hooks = [], calls = [], exported = {};
  let cursor = 0, tree, result;
  const props = { project: { name: "Edit", revision: 1 }, name: "Edit", dirty: true, busy: false, activeJob: false, compact: true,
    save: async () => calls.push("save"), saveForExit: async () => false, remove: async () => false, ...overrides };
  vm.runInNewContext(compiled, { exports: exported, Error, require(name) {
    if (name === "next/navigation") return { useRouter: () => ({ push: (url) => calls.push(["push", url]), replace: (url) => calls.push(["replace", url]) }) };
    if (name === "react") return {
      useState(initial) { const index = cursor++; if (!(index in hooks)) hooks[index] = initial; return [hooks[index], (value) => { hooks[index] = value; }]; },
      useRef: () => ({ current: null }), useId: () => "dialog", useEffect() {},
    };
    if (name === "react-dom") return { createPortal: (node) => node };
    if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }), Fragment: "fragment" };
    return { default: name };
  } });
  const render = () => { cursor = 0; result = exported[component](props); tree = component === "useProjectExit" ? result.dialog : result; };
  render();
  return { props, calls, render, exported, get tree() { return tree; },
    exit: () => result.onExit(),
    button: (label) => nodes(tree).find((node) => node.type === "button" && text(node).trim() === label),
    menu: () => nodes(tree).find((node) => node.type === "./EditorPopover"),
  };
}
const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };

test("project menu retains delete guards and navigates only after a successful deletion", async () => {
  const app = setup({ activeJob: true });
  assert.equal(app.button("Exit"), undefined, "navigation belongs to the shared header");
  assert.equal(app.button("Save").props.disabled, true);
  assert.equal(nodes(app.tree).some((node) => node.type === app.exported.DeleteProjectButton), false);
  let content = app.menu().props.children(() => {});
  let remove = nodes(content).find((node) => node.type === app.exported.DeleteProjectButton);
  assert.equal(remove.props.disabled, true);
  app.props.activeJob = false; app.render();
  content = app.menu().props.children(() => {});
  remove = nodes(content).find((node) => node.type === app.exported.DeleteProjectButton);
  await remove.props.onDelete(); assert.equal(app.calls.length, 0);
  app.props.remove = async () => ({ deleted: "project" }); app.render();
  remove = nodes(app.menu().props.children(() => {})).find((node) => node.type === app.exported.DeleteProjectButton);
  await remove.props.onDelete(); assert.deepEqual(app.calls, [["replace", "/lab"]]);
});

test("successful deletion with deferred file cleanup navigates to a neutral Labs notice", async () => {
  const app = setup({ remove: async () => ({ deleted: "project", cleanup_pending: true }) });
  const remove = nodes(app.menu().props.children(() => {})).find((node) => node.type === app.exported.DeleteProjectButton);
  assert.equal(await remove.props.onDelete(), true);
  assert.deepEqual(app.calls, [["replace", "/lab?cleanup=pending"]]);
});

test("delete confirmation names generated files and receipts while preserving originals", () => {
  const app = setup({ onDelete: async () => true }, "DeleteProjectButton");
  app.button("Delete").props.onClick({ currentTarget: { ownerDocument: { activeElement: null } } });
  app.render();
  assert.match(text(app.tree), /job history and receipts, and generated previews and exports/);
  assert.match(text(app.tree), /original music and film library are kept/);
});

test("dirty Exit preserves save failure and only exits after the save succeeds", async () => {
  const app = setup({}, "useProjectExit");
  app.exit(); app.render();
  assert.equal(app.calls.length, 0);
  app.button("Save and exit").props.onClick(); await flush(); app.render();
  assert.equal(app.calls.length, 0);
  assert.ok(app.button("Cancel"));
  app.props.saveForExit = async () => true; app.render();
  app.button("Save and exit").props.onClick(); await flush(); app.render();
  assert.deepEqual(app.calls, [["push", "/lab"]]);
});

test("clean Labs exits, including untouched drafts and active jobs, never call save", () => {
  for (const activeJob of [false, true]) {
    const app = setup({ dirty: false, activeJob, project: { name: "Untitled", revision: 0 } }, "useProjectExit");
    app.exit(); app.render();
    assert.deepEqual(app.calls, [["push", "/lab"]]);
    assert.equal(app.tree, null);
  }
});

test("discarding an edited draft exits without a save, and cancelling stays in the workspace", () => {
  const app = setup({ project: { name: "Untitled", revision: 0 } }, "useProjectExit");
  app.exit(); app.render();
  app.button("Cancel").props.onClick(); app.render();
  assert.equal(app.tree, null); assert.deepEqual(app.calls, []);
  app.exit(); app.render(); app.button("Discard and exit").props.onClick();
  assert.deepEqual(app.calls, [["push", "/lab"]]);
});

test("drafts offer Save only after an edit and never offer Delete", () => {
  const app = setup({ project: { name: "Untitled", revision: 0 }, dirty: false });
  assert.equal(app.menu(), undefined); assert.equal(app.button("Save").props.disabled, true);
  app.props.dirty = true; app.render(); assert.equal(app.button("Save").props.disabled, false);
});

test("opening Delete closes its menu but never skips confirmation, including an active operation arriving", async () => {
  let closed = 0, deleted = 0;
  const app = setup({ name: "Edit", onOpen: () => closed++, onDelete: async () => { deleted++; return false; } }, "DeleteProjectButton");
  app.button("Delete").props.onClick({ currentTarget: { ownerDocument: { activeElement: null } } }); app.render();
  assert.equal(closed, 1); assert.equal(deleted, 0);
  app.props.disabled = true; app.render();
  assert.equal(app.button("Delete project").props.disabled, true);
  app.button("Delete project").props.onClick(); await flush();
  assert.equal(deleted, 0);
  app.props.disabled = false; app.render();
  app.button("Delete project").props.onClick(); await flush(); app.render();
  assert.equal(deleted, 1); assert.ok(app.button("Cancel"), "a rejected delete leaves confirmation open");
  app.button("Cancel").props.onClick(); app.render();
  assert.equal(app.button("Delete project"), undefined);
});
