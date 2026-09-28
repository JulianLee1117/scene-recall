# ADR-0066: Source context for bounded editorial comparisons

- Status: Accepted
- Date: 2026-09-15
- Extends: ADR-0044 and ADR-0061

## Observed need

Sparse shot descriptions have supported incorrect editorial readings in the
recorded source-fit examples. They cannot establish a character relationship,
dream context, or why a local event matters. The user also explicitly requested
intentional metaphorical editing and approved a small context-assisted selection
pilot. This is an evidence and selection experiment, not evidence that main
search needs a new representation or that context improves played edits.

## Decision

Add a source-context package with four bounded pieces: immutable context
artifacts and read-only lookup; an explicit resumable backfill command; an
optional adapter for the existing editorial selector; and a frozen comparison
tool. Reuse existing storage, source authority, hosted request receipts and
selection validation. No new service or vector database is required.

Context records have film, sequence or local scope. Their applicability ranges
are distinct from the supporting evidence ranges and from selectable source
ranges. Anchor them to the existing film identity, recorded fingerprint/profile
and source-player timeline; unit IDs are lookup hints. Statements distinguish
observations from narrative claims, retain citations and uncertainty, and never
become canonical song-specific symbolism. A film overview alone does not prove
what a particular excerpt contains. Missing context is not a negative finding.

Keep artifacts independently backfillable and model/prompt/schema/input scoped.
Publish per-film manifests atomically within an explicitly selected profile;
retain older immutable artifacts. Partial pilot coverage is explicit. Reads do
not analyze footage or create work. Source mismatch, stale/corrupt artifacts,
missing coverage and inspected-but-uncertain evidence remain distinguishable.

The first producer uses bounded ordered shot annotations, validated timed
dialogue and sampled existing frames for explicitly selected windows. It must
not substitute remembered film plots for evidence. Sparse samples do not verify
continuous action or precise onset/completion. Wider context unsupported by the
inputs remains unknown. Future video models may produce a new compatible record
profile without changing source identities or the editorial selection boundary.

The editor can attach deduplicated context after retrieving its finite offered
sources. Keep the current PE/Qwen views, categories, ordering and legal ranges.
`lab.context_profile` is unset by default. An explicit profile enables only
cached context lookup for AI selection; manual Find scenes and ordinary search
do not run it. Freeze the context artifact IDs and supplied content with the
selection input receipt. Context cannot introduce source candidates or enlarge
trim authority. Editorial interpretation remains conditioned on the song,
instructions and sequence; unsupported plot and instruction-like source text
are not authority. Missing context preserves the baseline selection path.
Bound the selector packet and share its character budget across retained records
so long earlier records do not consume every later candidate's allocation.
Keep claims verbatim, preserve uncertainty and mark omitted claims/excerpts;
immutable artifacts retain complete evidence and hashes.

## Initial scope and verification

The initial backfill is limited to three films and twenty explicit windows of
at most 180 seconds, one uncached hosted request per window and at most sixteen
images per request. Commands default to dry-run and require explicit execution
and a request ceiling. An attempted failed call consumes the budget; no retry
or provider substitution is included. Existing indexed evidence and raw films
are read-only. The builder is an independent process, not part of interactive
search or an automatic library-wide queue.

Freeze identical candidate, music, timing and source inputs for context-off/on
selection. Each comparison permits at most two hosted calls. The initial real
pilot may run up to three pairs, without listening, retrieval, saved-project
writes or global activation. Preserve receipts, factual audit material and
playable source windows. Mechanical checks and generated explanations are not
human factual or creative acceptance; report those judgments as pending.

Tests cover citation/range integrity, source staleness, read-only fallback,
immutable publication, strict budgets, cache dependencies, unchanged candidate
authority and frozen comparison inputs. Evaluate supported narrative accuracy
and whether the actual excerpt communicates a useful relationship to the song,
including visual metaphors that do not need plot context. Measure latency and
cost separately. Only after useful played comparisons should the pilot expand.

## Deferred

No context retrieval index, exhaustive character graph, mandatory full-film
segmentation, broad ontology, global re-ingestion, automatic critic loop, new
category UI or blanket context activation. A context retrieval channel requires
evidence that useful moments are missing from the offered pool and the existing
retrieval coverage/activation gates. Action-boundary refinement remains governed
by its independent temporal evidence contract.
