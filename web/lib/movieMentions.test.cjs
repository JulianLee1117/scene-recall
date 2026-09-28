const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const compile = (name) => ts.transpileModule(fs.readFileSync(path.join(__dirname, `${name}.ts`), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const suggestions = {}, helpers = {};
vm.runInNewContext(compile("movieSuggestions"), { exports: suggestions });
vm.runInNewContext(compile("movieMentions"), { exports: helpers, require: () => suggestions });
const { acceptMovieMention, compileMovieDraft, editMovieText, movieCompletionText, removeMovieMention, setMovieScope } = helpers;
const film = (id, title) => ({ film_id: id, title, filename: `${title}.mkv`, status: "indexed" });
const films = [film("sunrise", "Before Sunrise (1995)"), film("moonlight", "Moonlight (2016)")];
const draft = (text) => ({ text, mentions: [] });
const scope = (value) => Array.from(compileMovieDraft(value).filmIds);
function accept(value, id, caret = value.text.length) {
  const suggestion = suggestions.getMovieSuggestions(movieCompletionText(value), films, [], caret).find((item) => item.film.film_id === id);
  assert.ok(suggestion);
  return acceptMovieMention(value, suggestion);
}

test("an explicit middle mention stays in its sentence while search compiles the scope separately", () => {
  const text = "the scene in @Before where they listen to a song";
  const next = accept(draft(text), "sunrise", "the scene in @Before".length);
  assert.equal(next.draft.text, "the scene in @Before Sunrise (1995) where they listen to a song");
  assert.equal(next.draft.text.slice(next.caret), "where they listen to a song");
  assert.equal(compileMovieDraft(next.draft).query, "the scene where they listen to a song");
  assert.deepEqual(scope(next.draft), ["sunrise"]);
  const quoted = accept(draft('scene from "Before Sunrise", two people'), "sunrise");
  assert.equal(quoted.draft.text, "scene from @Before Sunrise (1995), two people");
  assert.equal(compileMovieDraft(quoted.draft).query, "scene, two people");
});

test("native edits shift intact mentions, demote edited titles and never infer scope from paste or undo", () => {
  const original = accept(draft("red @Before"), "sunrise").draft;
  const prefixed = editMovieText(original, `🌙 ${original.text}`, 3);
  assert.equal(prefixed.mentions[0].start, original.mentions[0].start + 3);
  assert.deepEqual(scope(prefixed), ["sunrise"]);
  const changed = prefixed.text.replace("Sunrise", "Sunset");
  const demoted = editMovieText(prefixed, changed, changed.indexOf("Sunset") + 6);
  assert.deepEqual(scope(demoted), []);
  assert.equal(demoted.text, changed);
  assert.deepEqual(scope(editMovieText(demoted, prefixed.text)), [], "undo text alone cannot restore catalog authorization");
  assert.deepEqual(scope(editMovieText(draft(""), "@Before Sunrise (1995)")), [], "pasted tags need explicit confirmation");
});

test("acceptance leaves space for continued typing and suffix insertion never extends a mention", () => {
  const next = accept(draft("@Before"), "sunrise");
  assert.equal(next.draft.text.slice(next.caret - 1), " ");
  const appended = editMovieText(next.draft, `${next.draft.text}two people`);
  assert.deepEqual(scope(appended), ["sunrise"]);
  assert.equal(compileMovieDraft(appended).query, "two people");
  assert.equal(appended.mentions[0].end, next.draft.mentions[0].end);
  const atBoundary = editMovieText(next.draft, next.draft.text.trimEnd() + " by a train ", next.draft.mentions[0].end + 12);
  assert.deepEqual(scope(atBoundary), ["sunrise"]);
});

test("multiple occurrences share one scope identity and removing one does not clear the other", () => {
  const first = accept(draft("@Before"), "sunrise").draft;
  const again = editMovieText(first, `${first.text}and @Before`);
  const two = accept(again, "sunrise").draft;
  assert.equal(two.mentions.length, 2);
  assert.deepEqual(scope(two), ["sunrise"]);
  const removed = removeMovieMention(two, two.mentions[0]);
  assert.equal(removed.draft.mentions.length, 1);
  assert.deepEqual(scope(removed.draft), ["sunrise"]);
  assert.equal(removed.draft.text, "and @Before Sunrise (1995) ");
  assert.deepEqual(scope(removeMovieMention(removed.draft, removed.draft.mentions[0]).draft), []);
});

test("partial selections across a title boundary invalidate just the affected scope", () => {
  const selected = setMovieScope(draft("red"), ["sunrise", "moonlight"], films);
  const first = selected.mentions[0];
  const start = 2, end = first.start + 8;
  const text = selected.text.slice(0, start) + "blue " + selected.text.slice(end);
  const next = editMovieText(selected, text, start + 5);
  assert.equal(next.text, text);
  assert.deepEqual(scope(next), ["moonlight"]);
  assert.equal(next.text.slice(next.mentions[0].start, next.mentions[0].end), "@Moonlight (2016)");
});

test("deleting repeated adjacent titles uses the native resulting caret to retain the correct occurrence", () => {
  const first = accept(draft("@Before"), "sunrise").draft;
  const two = accept(editMovieText(first, first.text + "@Before"), "sunrise").draft;
  const next = editMovieText(two, two.text.slice(two.mentions[1].start), 0);
  assert.equal(next.mentions.length, 1);
  assert.equal(next.mentions[0].start, 0);
  assert.deepEqual(scope(next), ["sunrise"]);
});

test("picker removals delete the whole mention without changing the compiled query, and additions append without moving prose", () => {
  const before = accept(draft("the scene in @Before where they listen"), "sunrise", 20).draft;
  const both = setMovieScope(before, ["sunrise", "moonlight", "not-indexed"], films);
  assert.ok(both.text.startsWith(before.text));
  assert.deepEqual(scope(both), ["sunrise", "moonlight"]);
  const removed = setMovieScope(both, ["moonlight"], films);
  assert.equal(removed.text, "the scene where they listen @Moonlight (2016) ");
  assert.equal(compileMovieDraft(removed).query, compileMovieDraft(both).query);
  assert.deepEqual(scope(removed), ["moonlight"]);
  const clear = setMovieScope(removed, [], films);
  assert.equal(clear.text, "the scene where they listen");
  assert.deepEqual(scope(clear), []);
});

test("checking then unchecking a movie restores the exact original search text", () => {
  for (const text of ["", "red", "red ", "two people talking", "a scene, at night"]) {
    const checked = setMovieScope(draft(text), ["sunrise"], films);
    assert.deepEqual(scope(checked), ["sunrise"]);
    const unchecked = setMovieScope(checked, [], films);
    assert.equal(unchecked.text, text.trimEnd());
    assert.equal(unchecked.mentions.length, 0);
  }
});

test("unchecking a mid-sentence, leading or punctuated mention leaves clean prose", () => {
  const cases = [
    ["scenes from @Before, two people", "scenes, two people"],
    ["@Before two people walking", "two people walking"],
    ["in @Before", ""],
    ["a kiss in @Before.", "a kiss."],
  ];
  for (const [input, expected] of cases) {
    const value = accept(draft(input), "sunrise", input.indexOf("@Before") + 7).draft;
    const removed = setMovieScope(value, [], films);
    assert.equal(removed.text, expected);
    assert.equal(compileMovieDraft(removed).query, compileMovieDraft(value).query);
    assert.deepEqual(scope(removed), []);
  }
});

test("only complete validated ranges compile and title-only scope produces empty search text", () => {
  const selected = accept(draft("@Before"), "sunrise").draft;
  assert.equal(compileMovieDraft(selected).query, "");
  assert.equal(compileMovieDraft({ text: "replacement", mentions: selected.mentions }).query, "replacement");
  assert.deepEqual(scope({ text: "replacement", mentions: selected.mentions }), []);
  assert.equal(compileMovieDraft(draft(" a  literal, query! ")).query, " a  literal, query! ");
});
