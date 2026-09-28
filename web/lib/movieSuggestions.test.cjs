const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const ts = require("typescript");
const helpers = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "movieSuggestions.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: helpers });
const { displayFilmTitle, getMovieSuggestions, getMovieMentionRange } = helpers;
const film = (title, year, extra = {}) => ({
  title, filename: `${title}${year ? ` (${year})` : ""}.mkv`,
  film_id: `${title}-${year || "unknown"}`, status: "indexed", path: "unused",
  size_gb: 1, duration: 7200, ...extra,
});
const moonlight = film("Moonlight", 2016);
const ids = (suggestions) => Array.from(suggestions, (suggestion) => suggestion.film.film_id);

test("display titles preserve existing scope cleanup", () => {
  assert.equal(displayFilmTitle(film(" Movie — Moonlight.mp4 ")), "Moonlight");
  assert.equal(displayFilmTitle(film("", null, { filename: "Suspiria (1977).mkv" })), "Suspiria (1977)");
});

test("acceptance removes only the title and local scope phrase, preserving description case", () => {
  const query = "medium shot of two people in Moonlight, neo noir";
  const [suggestion] = getMovieSuggestions(query, [moonlight], []);
  assert.equal(query.slice(suggestion.start, suggestion.end), "in Moonlight");
  assert.equal(suggestion.remainingQuery, "medium shot of two people, neo noir");
  assert.equal(getMovieSuggestions("From Moonlight, two people at night", [moonlight], [])[0].remainingQuery, "two people at night");
});

test("accent, case and title punctuation matching retain original source offsets", () => {
  const query = "🌙 café in AME\u0301LIE, red lighting";
  const [suggestion] = getMovieSuggestions(query, [film("Amélie", 2001)], []);
  assert.equal(query.slice(suggestion.start, suggestion.end), "in AME\u0301LIE");
  assert.equal(suggestion.remainingQuery, "🌙 café, red lighting");
  const [colon] = getMovieSuggestions("Mishima a life in four chapters, a closeup", [film("Mishima: A Life in Four Chapters", 1985)], []);
  assert.equal(colon.remainingQuery, "a closeup");
  assert.equal(getMovieSuggestions("moonlighter and sunlight", [moonlight], []).length, 0);
});

test("ambiguous versions remain distinct until an explicit year selects one", () => {
  const old = film("Suspiria", 1977);
  const remake = film("Suspiria", 2018);
  assert.deepEqual(ids(getMovieSuggestions("Suspiria", [old, remake], [])).sort(), [old.film_id, remake.film_id].sort());
  for (const query of ["red from Suspiria (1977)", "red from Suspiria 1977", 'red from "Suspiria (1977)"', 'red from "Suspiria" (1977)']) {
    const matches = getMovieSuggestions(query, [remake, old], []);
    assert.deepEqual(ids(matches), [old.film_id]);
    assert.equal(matches[0].remainingQuery, "red");
  }
  assert.equal(getMovieSuggestions("Suspiria (1999)", [old, remake], []).length, 0);
  assert.equal(getMovieSuggestions("Suspiria 1977", [film("Suspiria")], []).length, 0);
});

test("explicit release years embedded in the display title are matched as metadata", () => {
  const dated = film("Suspiria (1977)");
  assert.equal(getMovieSuggestions("Suspiria", [dated], [])[0].title, "Suspiria (1977)");
  assert.equal(getMovieSuggestions("Suspiria (2018)", [dated], []).length, 0);
  const sequel = film("Blade Runner 2049", 2017);
  assert.equal(getMovieSuggestions("Blade Runner 2049 (2017)", [sequel], [])[0].remainingQuery, "");
});

test("short common titles require explicit scope, quotes, a year or a title-only query", () => {
  for (const title of ["Her", "Us", "It", "Up"]) {
    const item = film(title, 2000);
    assert.equal(getMovieSuggestions(`show ${title} in a room`, [item], []).length, 0, title);
    assert.equal(getMovieSuggestions(`a person looking at ${title}`, [item], []).length, 0, title);
    for (const query of [title, `closeup in ${title}`, `closeup from ${title}`, `closeup "${title}"`, `closeup ${title} (2000)`]) {
      assert.equal(getMovieSuggestions(query, [item], []).length, 1, query);
    }
  }
});

test("style references are never offered as a hard movie scope", () => {
  for (const query of ["blue like Moonlight", "Moonlight-like blue lighting", '"Moonlight"-like blue lighting', "‘Moonlight’-inspired lighting", '"Moonlight (2016)"-like blue lighting', "in the style of Moonlight", "the look of Moonlight", "inspired by Moonlight", 'like "Moonlight"', "like from Moonlight"]) {
    assert.equal(getMovieSuggestions(query, [moonlight], []).length, 0, query);
  }
});

test("partial titles never produce suggestions, while complete titles still do", () => {
  const library = [moonlight, film("The Godfather", 1972), film("Before Sunrise", 1995)];
  for (const query of ["the", "the ...", "the g", "The Godf", "Mo", "Moo", "bef", "Before Sun", "blue moo"]) {
    assert.equal(getMovieSuggestions(query, library, []).length, 0, query);
  }
  for (const item of library) {
    const [suggestion] = getMovieSuggestions(item.title, library, []);
    assert.equal(suggestion.film.film_id, item.film_id);
    assert.equal(suggestion.remainingQuery, "");
  }
});

test("longest overlapping exact title wins, while independent titles remain suggestions", () => {
  const first = film("The Godfather", 1972);
  const second = film("The Godfather Part II", 1974);
  const result = getMovieSuggestions("The Godfather Part II and Moonlight, two people", [first, moonlight, second], []);
  assert.deepEqual(ids(result), [second.film_id, moonlight.film_id]);
  assert.deepEqual(ids(getMovieSuggestions("The Godfather Part II", [first, second], [second.film_id])), []);
});

test("an incompatible year on a longer title cannot suggest the shorter original", () => {
  const first = film("The Godfather", 1972);
  const second = film("The Godfather Part II", 1974);
  assert.deepEqual(ids(getMovieSuggestions("The Godfather Part II (1999)", [first, second], [])), []);
  const [independent] = getMovieSuggestions("The Godfather and The Godfather Part II (1999)", [first, second], []);
  assert.equal(independent.film.film_id, first.film_id);
  assert.equal(independent.remainingQuery, "and The Godfather Part II (1999)");
});

test("only indexed distinct unselected IDs are returned, with a stable limit of four", () => {
  const others = Array.from({ length: 6 }, (_, i) => film(`Moonlight ${String.fromCharCode(65 + i)}`, 2016));
  const hidden = film("Moonlight Unindexed", 2016, { status: "not_indexed" });
  const missing = film("Moonlight Missing", 2016, { film_id: null });
  const input = Object.freeze([others[2], others[0], others[1], ...others, hidden, missing]);
  const query = [...others, hidden, missing].map((item) => item.title).join("; ");
  assert.deepEqual(ids(getMovieSuggestions(query, input, [others[0].film_id])), others.slice(1, 5).map((item) => item.film_id));
});

test("repeated mentions produce one suggestion without deleting unrelated occurrences", () => {
  const query = "Moonlight, a scene like Moonlight";
  const [suggestion] = getMovieSuggestions(query, [moonlight, moonlight], []);
  assert.equal(suggestion.remainingQuery, "a scene like Moonlight");
  assert.equal(getMovieSuggestions(query, [moonlight, moonlight], []).length, 1);
});

test("explicit @ tags complete multiword titles and consume the marker on acceptance", () => {
  const before = film("Before Sunrise", 1995);
  for (const query of ["@Before Sunrise", "@Before", "@bef"]) {
    const [suggestion] = getMovieSuggestions(query, [before], []);
    assert.equal(suggestion.film.film_id, before.film_id);
    assert.equal(query.slice(suggestion.start, suggestion.end), query);
    assert.equal(suggestion.remainingQuery, "");
  }
  const shadow = film("Shadow of a Doubt", 1943);
  const [suggestion] = getMovieSuggestions("red @Shadow", [shadow], []);
  assert.equal(suggestion.remainingQuery, "red");
  assert.equal(getMovieSuggestions("red Shadow", [shadow], []).length, 0);
});

test("complete @ tags preserve following prose and attached matching release years", () => {
  const before = film("Before Sunrise", 1995);
  const query = "red from @Before Sunrise (1995), two people";
  const [suggestion] = getMovieSuggestions(query, [before], []);
  assert.equal(query.slice(suggestion.start, suggestion.end), "from @Before Sunrise (1995)");
  assert.equal(suggestion.remainingQuery, "red, two people");
  assert.equal(getMovieSuggestions("@Before Sunrise (1999)", [before], []).length, 0);
  assert.equal(getMovieSuggestions("@Before two people", [before], []).length, 0);
});

test("@ allows common titles and explicitly requests movie identity even after like", () => {
  const [common] = getMovieSuggestions("@Her person", [film("Her", 2013)], []);
  assert.equal(common.remainingQuery, "person");
  const [explicit] = getMovieSuggestions("blue like @Moonlight", [moonlight], []);
  assert.equal(explicit.remainingQuery, "blue like");
  assert.equal(getMovieSuggestions("blue like Moonlight", [moonlight], []).length, 0);
});

test("bare markers and email addresses never produce tag defaults or movie recognition", () => {
  const library = [moonlight, film("Before Sunrise", 1995)];
  for (const query of ["@", "red @", "@ ", "person@Moonlight.com", "Moonlight@example.com", "person@Before", "person@example.Moonlight.com"]) {
    assert.equal(getMovieSuggestions(query, library, []).length, 0, query);
  }
});

test("complete tagged titles outrank completions and retain longest-title year protection", () => {
  const shadow = film("Shadow", 2018);
  const doubt = film("Shadow of a Doubt", 1943);
  assert.deepEqual(ids(getMovieSuggestions("@Shadow", [doubt, shadow], [])), [shadow.film_id, doubt.film_id]);
  const first = film("The Godfather", 1972);
  const second = film("The Godfather Part II", 1974);
  assert.deepEqual(ids(getMovieSuggestions("@The Godfather Part II", [first, second], [])), [second.film_id]);
  assert.deepEqual(ids(getMovieSuggestions("@The Godfather Part II (1999)", [first, second], [])), []);
});

test("numeric title prefixes complete without confusing title digits with release years", () => {
  const odyssey = film("2001: A Space Odyssey", 1968);
  for (const query of ["@2001", "@2001 A Space", "@2001: A Space Odyssey (1968)"]) {
    const [suggestion] = getMovieSuggestions(query, [odyssey], []);
    assert.equal(suggestion.film.film_id, odyssey.film_id);
    assert.equal(suggestion.remainingQuery, "");
  }
  assert.equal(getMovieSuggestions("@2001: A Space Odyssey (1999)", [odyssey], []).length, 0);
  assert.equal(getMovieSuggestions("2001", [odyssey], []).length, 0);
});

test("title-only punctuation residue becomes empty without stripping descriptive punctuation", () => {
  const before = film("Before Sunrise", 1995);
  for (const query of ["@Before Sunrise.", "@Before Sunrise (1995).", "@Before Sunrise…", "@Before Sunrise, ...?!"]) {
    assert.equal(getMovieSuggestions(query, [before], [])[0].remainingQuery, "", query);
  }
  assert.equal(getMovieSuggestions("@Before Sunrise, two people: talking?", [before], [])[0].remainingQuery, "two people: talking?");
  assert.equal(getMovieSuggestions("Two people. @Before Sunrise", [before], [])[0].remainingQuery, "Two people.");
  assert.equal(getMovieSuggestions("@Before Sunrise 🌙", [before], [])[0].remainingQuery, "🌙");
});

test("@ completion at a middle caret preserves the following description and its spacing", () => {
  const before = film("Before Sunrise", 1995);
  const query = "red @Bef blue  shadows, two people.";
  const caret = query.indexOf(" blue");
  const [suggestion] = getMovieSuggestions(query, [before], [], caret);
  assert.equal(query.slice(suggestion.start, suggestion.end), "@Bef");
  assert.equal(suggestion.remainingQuery, "red blue  shadows, two people.");
  const range = getMovieMentionRange(query, caret, [before]);
  assert.equal(query.slice(range.start, range.end), "@Bef");
  assert.equal(getMovieSuggestions(query, [before], [], query.indexOf("@")).length, 0);
  assert.equal(getMovieMentionRange(query, query.indexOf("@"), [before]), null);
});

test("moving within a complete @ title replaces its whole existing span", () => {
  const before = film("Before Sunrise", 1995);
  const sunset = film("Before Sunset", 2004);
  const query = "red @Before Sunrise, blue shadows";
  const caret = query.indexOf("Sunrise") + 1;
  const suggestions = getMovieSuggestions(query, [sunset, before], [], caret);
  assert.deepEqual(ids(suggestions), [before.film_id, sunset.film_id]);
  for (const suggestion of suggestions) {
    assert.equal(query.slice(suggestion.start, suggestion.end), "@Before Sunrise");
    assert.equal(suggestion.remainingQuery, "red, blue shadows");
  }
  const range = getMovieMentionRange(query, caret, [before, sunset]);
  assert.equal(query.slice(range.start, range.end), "@Before Sunrise");
  assert.equal(getMovieMentionRange(query, query.length, [before, sunset]), null);
});

test("the active @ phrase takes precedence over unrelated complete titles elsewhere", () => {
  const before = film("Before Sunrise", 1995);
  const query = "Moonlight red @Bef blue shadows";
  const caret = query.indexOf(" blue");
  assert.deepEqual(ids(getMovieSuggestions(query, [moonlight, before], [], caret)), [before.film_id]);
  assert.deepEqual(ids(getMovieSuggestions(query, [moonlight, before], [], query.indexOf("@"))), [moonlight.film_id]);
  assert.equal(getMovieSuggestions("Moonlight @unknown", [moonlight, before], []).length, 0);
});

test("mention ranges handle empty, common and numeric tags without coloring following prose", () => {
  const library = [film("Her", 2013), film("2001: A Space Odyssey", 1968)];
  for (const query of ["@", "red @", "@Her", "@2001"]) {
    const range = getMovieMentionRange(query, query.length, library);
    assert.equal(range.start, query.indexOf("@"));
    assert.equal(range.end, query.length);
  }
  const query = "@Her person";
  const range = getMovieMentionRange(query, 4, library);
  assert.equal(query.slice(range.start, range.end), "@Her");
  assert.equal(getMovieMentionRange(query, query.length, library), null);
  const punctuated = getMovieMentionRange("@Her.", 5, library);
  assert.equal(punctuated.start, 0);
  assert.equal(punctuated.end, 4);
  assert.equal(getMovieSuggestions("red @ blue", library, [], 5).length, 0);
  for (const email of ["person@Her.com", "Her@example.com"]) {
    assert.equal(getMovieMentionRange(email, email.length, library), null);
  }
});
