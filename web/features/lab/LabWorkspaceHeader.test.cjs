const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const compiled = ts.transpileModule(fs.readFileSync(path.join(__dirname, "LabWorkspaceHeader.tsx"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
function render(props) {
  const exported = {}, calls = [];
  vm.runInNewContext(compiled, { exports: exported, require(name) {
    if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
    if (name === "./ProjectActions") return { default: "ProjectActions", useProjectExit: (state) => ({ onExit: (destination) => calls.push([state, destination]), dialog: null }) };
    if (name.endsWith(".css")) return { default: new Proxy({}, { get: (_, key) => key }) };
    return { default: name };
  } });
  const tree = exported.default(props);
  return { tree, calls, nodes: nodes(tree), bar: nodes(tree).find((node) => node.type === "@/components/AppBar") };
}
test("session headers carry the app bar as plain links and one direct Labs link under the experiment title", () => {
  for (const title of ["Match Cuts", "AI Music Video"]) {
    const ui = render({ title });
    assert.equal(ui.bar.props.active, "lab");
    assert.equal(ui.bar.props.onNavigate, undefined, "nothing to guard: the bar's links navigate themselves");
    const links = ui.nodes.filter((node) => node.props?.href);
    assert.equal(links.length, 1); assert.equal(links[0].props.href, "/lab");
    assert.equal(links[0].props["aria-label"], "Back to Labs");
    assert.equal(text(ui.nodes.find((node) => node.type === "h1")), title);
    assert.equal(ui.nodes.some((node) => node.type === "ProjectActions" || node.type === "input"), false);
  }
});
test("project headers route Labs and every app bar place through the one guard and convey draft/saved status", () => {
  for (const [revision, dirty, status] of [[0, false, "Not saved"], [0, true, "Unsaved changes"], [3, false, "Saved"], [3, true, "Unsaved changes"]]) {
    const project = { project: { revision }, name: "My edit", dirty, busy: false, activeJob: true };
    const ui = render({ title: "AI Music Video", project });
    const back = ui.nodes.filter((node) => node.props?.["aria-label"] === "Back to Labs");
    assert.equal(back.length, 1); assert.equal(back[0].type, "button");
    assert.equal(back[0].props.disabled, false, "active background work may be left running");
    back[0].props.onClick(); assert.deepEqual(ui.calls, [[project, "/lab"]]);
    ui.bar.props.onNavigate("/?tab=saved");
    assert.deepEqual(ui.calls[1], [project, "/?tab=saved"], "leaving by the app bar asks the same question");
    assert.ok(text(ui.tree).includes(status));
    assert.equal(ui.nodes.filter((node) => node.props?.href).length, 0);
    assert.ok(ui.nodes.some((node) => node.type === "ProjectActions"));
  }
});
