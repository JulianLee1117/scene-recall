# ADR-0085: Keep confirmed movie mentions inside the sentence

- Status: Accepted
- Date: 2026-09-20
- Supersedes: ADR-0084's leading-chip presentation and start-of-input deletion

## Context

Moving an accepted movie to the front of the search bar breaks descriptions
such as “the scene in @Before Sunrise where…”. The user wants the movie identity
to remain where they typed it, with predictable ordinary text editing.

## Decision

Keep the native single-line input. Store its visible text with validated,
non-overlapping confirmed movie ranges and catalog film IDs. The presentation
paints these ranges in place; confirmation inserts the catalog title, including
its year, and moves the caret after it. A separate compiler removes confirmed
movie phrases from retrieval text and supplies the union of their film IDs.
The visible sentence keeps its surrounding words. No editor framework, remote
query interpretation or backend recipe change is required.

Edits before or after a confirmed range preserve and shift its identity. Edits
inside or across it invalidate that range; the remaining words are literal
text until explicitly confirmed again. Backspace immediately after a mention
and Delete immediately before it remove that whole mention. Other selections,
cursor movement and text entry remain native, including IME composition.
Repeated mentions of the same film share union scope: removing one does not
remove the filter while another confirmed mention remains.

Autocomplete masks confirmed ranges while preserving offsets. Its existing
caret anchoring, stable bar height, full-title guards, Enter/Tab confirmation
and sticky Escape cancellation remain. A new explicit mention can confirm the
same film elsewhere in the sentence without moving the earlier mention.

The movie picker uses this same draft. New selections append confirmed mentions;
deselection removes their @ markers and retains the title words as plain text.
All request paths use the compiled description and film IDs, including category
searches, voice completion and title-only browse. Scope changes still cancel
stale work. No automatic recovery broadens explicit scope.

## Verification

Cover insertion in a sentence, multiple and repeated films, edits on both sides,
partial selections, atomic boundary deletion, picker synchronization, Escape,
voice replacement and stale search cancellation. Check caret placement, colored
range alignment, horizontal scrolling and dropdown placement in the live UI.

## Limits

These are validated ranges over ordinary input text, not rich editor atoms.
Editing within a title intentionally returns that title to literal text. Native
undo can restore words without restoring a previously invalidated confirmation;
film identity must never be inferred merely because the words reappear.
Category presentation, Words separation and hosted interpretation remain
independent decisions.
