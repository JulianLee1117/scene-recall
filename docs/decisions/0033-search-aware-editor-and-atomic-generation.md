# ADR-0033: Search-aware editing and atomic generation

- Status: Accepted
- Date: 2026-09-11
- Supersedes: ADR-0031 for executable direction output; ADR-0032 for the default generation workflow
- Superseded by: ADR-0034 for the selected-shot interaction

## Context

The music planner produced one text query/facet per slot despite the existing
recipe engine supporting multiple clues and indexed references. Its creative
instructions asked for framing and movement decisions that caption-only
selection could not verify. Query-specific matched evidence could be overwritten
when different searches found the same unit. Users also had to coordinate
listening, planning and finding scenes manually, including two actions to apply
shot feedback.

## Decision

Expose a small versioned search capability description shared by editorial
planning and its search adapter. Describe evidence, supported inputs, current
availability and limits. Delegate execution to the existing retrieval contract;
do not duplicate ranking, add vector spaces or make ordinary search depend on
an LLM. Backend ranking and evidence improvements flow through this boundary.
A new operation still needs explicit schema, execution and quality support
before the editor advertises it.

Keep the existing human-readable query, facet, purpose, musical cue and timing
note. Direction planning can additionally propose one validated recipe of at
most three distinct facets, plus requirements that retrieval cannot verify.
Use text or server-offered indexed references; never accept arbitrary model
paths, vectors or invented source identities. References retain concrete
source/frame authority and become unusable when that authority no longer
matches. Planning context identifies each neighboring shot's actual offered
references. If a requested neighbor has none, the planner must explain that
unverified requirement rather than substitute another shot's reference. The
audio interpretation schema remains separate from executable search planning.

Use the existing equal-rank recipe fusion, film scope, source exclusions and
mandatory Framing candidate gate. Keep the simple query path compatible with
older documents and manual directions. Failed evidence availability does not
silently broaden a recipe. Expose the reason so a user can change the search.
Retrieval remains bounded to 32 distinct recipes, at most three clauses each,
48 results per recipe, 24 duration-fitting offers per slot and six alternatives.

Preserve query-specific match evidence separately from canonical source ranges.
Pass compact text/frame/clause evidence and known annotations to selection, and
retain inspectable search details in the slot. A caption, still-image annotation
or embedding score is not verification of action completion, exact geometry,
camera motion, story causality or an utterance's precise timing. All generated
source trims must still fit the fixed slot and actual source bounds.

Add one durable `generate` job over a frozen project revision. Default generation
listens only when current track/passage analysis is absent, plans eligible empty
positions and fills them. Existing cuts, placed footage, user directions and
locks are preserved. Explicit improvement of one unlocked position plans and
replaces that position with its saved feedback as context. Stage progress and
cancellation reuse the current worker. Apply only one final revision; a failure
or cancellation cannot leave an intermediate edit applied. Completed stage
caches remain reusable, with no automatic hosted retries or provider fallback.

Make Generate edit the primary action and Improve this shot the targeted action.
Keep individual stages available as advanced controls. Search details are
optional and editable; changing a simple query clears its obsolete generated
recipe. One Undo restores the project from before a generated result.

## Consequences and limits

- The editor can combine capabilities already present in main search and retain
  the evidence needed to understand why a candidate was offered.
- Human and AI workflows share executable search semantics without introducing
  an always-on query router or an extensible agent framework.
- Reference-based search uses indexed instants, not a newly decoded exact cut
  boundary. Dense image matching and temporal motion remain gated Lab work.
- Generation does not automatically watch shortlisted footage, optimize cut
  times, transcribe lyrics, mix dialogue or export a video. Existing live preview
  and optional source review remain available.
- Mechanical acceptance covers recipe/reference validation, evidence isolation,
  legacy documents, stage cancellation, revision conflicts, ownership/locks and
  browser Undo. Editorial acceptance compares real musical moments against the
  single-query baseline; legal trims or synthetic music do not establish quality.
