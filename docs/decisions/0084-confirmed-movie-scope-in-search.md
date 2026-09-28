# ADR-0084: Confirmed movie scope in the search input

- Status: Accepted
- Date: 2026-09-20
- Presentation and deletion behavior superseded by [ADR-0085](0085-inline-confirmed-movie-mentions.md).

## Context

People naturally include film titles alongside descriptions, shot types and
feelings. A title was previously ordinary embedding text unless the separate
movie picker was used. Silently interpreting every matching word as scope
would misread titles such as Her and style references such as like Moonlight.

## Decision

Keep native text editing in the isolated `MovieSearchInput.tsx` presentation
component. Color the active `@` phrase with a subtle static glow. Show at most
four indexed-film options in a compact absolute dropdown near the caret, clamped
to the viewport. The dropdown does not grow the bar; movie chips scroll
horizontally. No backend or ranking change is introduced by this presentation.

Ordinary prose recognizes complete titles; partial words such as "the" or "bef"
do not trigger autocomplete. An explicit `@` at the start or after whitespace
enables movie-prefix completion, including multiword names (`@Before`,
`@Before Sunrise`). Bare `@` prompts “Type a movie name…” without arbitrary
defaults; email addresses are not tags. Use title tokens and explicit catalog
years, without a query model, network request per keystroke or guessed identity.
Common-word and style-reference guards apply to ordinary prose; explicit `@`
requests movie identity even after wording such as "like".

Explicit `@` completion highlights its first match. ArrowUp/ArrowDown navigate;
Enter or Tab confirms only the visible active option. Clicking an option also
confirms it. Ordinary full-title suggestions start unselected, so plain Enter
searches the typed words; arrows can select a match for Enter/Tab confirmation.
Escape exits completion: remove only the active `@`, keep the words and caret,
and suppress highlighting and suggestions during continued typing or refocus.
A newly inserted valid `@` or clearing the field resets that cancellation.
Escape on ordinary title suggestions leaves text unchanged. Outside blur never
applies a suggestion.
Completion follows the caret and preserves description before and after the
mention. If the caret lies inside a recognized full movie mention, acceptance
consumes that whole match rather than leaving its suffix. Acceptance removes
only the matched title/scope phrase, optional `@` and year.
Backspace with empty text or a collapsed caret at the input start removes the
last movie chip; text deletion elsewhere and selected text retain native behavior.
Removable movie chips and the existing picker share `selectedFilmIds`. Multiple
films form a union. Scope changes cancel stale requests and clear old results.
No result recovery silently broadens confirmed movie scope.

An empty description with selected films uses the read-only
`GET /library/scenes` route. It requires explicit published film IDs and returns
ordinary playable cards in selected-film order and source chronology, using
the existing bounded prefix/paging envelope. It invokes no embedding or
generation model. Its eligibility and retained-frame rules are explicit in the
architecture contract; it does not submit a fabricated text query or loosen
the recipe's nonempty-clause rule.

Natural descriptions may mix shot scale, subjects, action, appearance and mood.
Category-only recipes remain valid without a main description. This change
does not split Words, add shot-type hard filters, activate plot context or
change similarity ranking. Those evidence and interaction decisions remain
separate from title identity and scope.

## Verification

Test bare `@`, prefix matches, ordinary unselected titles, keyboard navigation,
visible-option acceptance and dismissal without application. Cover caret edits
inside full mentions, preserved surrounding descriptions, ambiguous versions,
common-word titles, style references, title-only browse and stale requests.
Check dropdown position, stable bar height, chip overflow and native editing in
the running UI. Film-browse tests cover ordering, publication,
bounded paging, eligibility and valid frame evidence using indexed tables.
