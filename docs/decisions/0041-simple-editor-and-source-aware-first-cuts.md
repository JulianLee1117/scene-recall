# ADR-0041: A simple editor with source-aware first cuts

- Status: Accepted
- Date: 2026-09-13
- Extends: ADR-0025, ADR-0033, ADR-0034 and ADR-0035
- Supersedes: ADR-0039 for fixing first-edit cuts before source selection; ADR-0033 for forced candidate selection; earlier music-workspace navigation
- Partially superseded by: [ADR-0064](0064-scoped-editor-direction-and-two-tab-workspace.md) for the two-tab workspace and restricting source-aware cut adjustment to explicit whole-edit regeneration, excluding Fill gaps

## Context

The editor scattered scene search, replacement, timing and generation across
nested tabs and menus. Three inspected scene mismatches showed that a legal
source range and a broad mood match do not establish the requested action or
meaning. Music-first fixed durations also excluded potentially useful shorter
sources. The user requested one simple editor and authorized clearing existing
Lab projects; there is no need for user-facing planner versions or migration UI.

## Decision

Keep one AI Music Video entry, canonical experiment identity, project document,
worker and renderer. One Generate edit action prepares musical evidence, plans
intentions, retrieves and selects. Music analysis is beside the song; beat
visibility/detection is beside the timeline; cut manipulation and explicit
Suggest cuts are in Cut tools. The Scenes panel owns one visible search and its
results. Search options are optional; Find scenes uses the existing candidate-only
draft without an editorial LLM or automatic placement. Adjust clip opens source
moment/crop editing. Following-scene exploration is a contextual clip action.
Unused retained footage is accessible in Saved clips, with undoable clearing
that preserves locked and placed clips. Keep Save, Exit, Undo and History.
Settings stage pace, lyric treatment, film scope and output together; song notes,
lyrics and visual-story overrides are optional collapsed sections in the dialog.

Apply `bounded-source-aware-first-edit-v1` only after the existing strict
provisional-starter eligibility proof, before and after listening. Current audio
moments supply ordered intentions; their count and order remain unchanged.
Build a private finite set for each interior boundary: nominal, bounded endpoints
and up to four nearby measured guides. Movement is at most two seconds; neighboring
bands cannot overlap. Quantize interior cuts to the passage-relative output grid
while retaining exact original audio endpoints. Retrieve candidates fitting any
legal local duration rather than only the nominal duration. The existing selector
chooses offered source IDs, starts and ending frames together. Validate membership,
contiguity, source bounds, frame lengths and coverage before materializing ordinary
slots. Recheck the protected edit and apply one revision. No extra hosted stage,
new service, vector profile, persistent alternate timeline or main-search change.

Fixed-timing selection and first-edit selection both permit explicit abstention:
null candidate and source start, with a reason. Empty candidate offers require
abstention; a weak unrelated result must not be rationalized merely to fill a gap.
A failed replacement preserves its previous clip. Hydrate bounded offered IDs
with available indexed scalar/context annotations, dialogue and visible text;
these remain whole-unit, sparse evidence. Refit saved alternatives to final slot
lengths and remove those that no longer fit. Keep a bounded candidate ledger under
analysis.draft: contract, search queries/counts, offered IDs/ranks/durations, choices,
trims, timing and reasons. This diagnostic records a bounded offer, not a complete
reproducible search index or all model input. Existing hosted receipts retain the
model/adapter identity; no automatic retry or repair loop is added.

Public job status exposes up to 80 recorded worker progress strings, cancellation
state and existing timestamps. The compact status row shows actual stage and
elapsed time. Anchored Details shows recorded model/provider stages and available
candidate/gap counts, including terminal failures and unapplied results. No fake
percentages, private snapshots or editable partial model output are exposed.
Cancellation is cooperative at existing operation boundaries.

Projects remain complete JSON documents/revisions and frozen job snapshots in
SQLite WAL, separate from original track files and rebuildable derived assets.
This is appropriate for the current single-user deployment. Pagination, summary
listings and history/cache retention precede a storage redesign when actual scale
requires them; multiuser isolation and a leased worker queue remain deferred.

## Validation and limits

Mechanical acceptance covers shorter-source inclusion, legal flexible cuts,
nonzero/subframe passage endpoints, fixed/manual/locked protection, abstention,
atomic apply/Undo, cancellation and invalid-model rejection. Browser acceptance
covers import, one-button generation, progress inspection, scene search/placement,
playback, timing tools, settings, save/exit and responsive overlays.

This is a bounded first-edit experiment, not a full passage assembler. It keeps
intent order/count and does not watch candidate video, verify action completion,
recover missing plot context, or establish artistic quality. Main search still
uses its existing ranking and request path. Played song/footage comparisons and
retrieval diagnostics remain the gates for more expensive temporal inspection,
query repair, broader source-aware assembly or representation backfills.
