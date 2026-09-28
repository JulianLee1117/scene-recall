# ADR-0065: Private discovery and flexible assembly comparison

- Status: Accepted
- Date: 2026-09-15
- Extends: ADR-0058, ADR-0061 and ADR-0064
- Supersedes: the blanket deferral of discovery-before-assembly, only for this private experiment

## Context

The current selector fills a predetermined count and order of intentions. Its
source-fitting timing choices remain bounded around those positions. Increasing
the per-position candidate count does not let discoveries change that structure.
Meanwhile, a model's immediate candidate catalog must not be mistaken for the
available film library. We need to distinguish assembly limits from discovery
limits before replacing production generation.

## Decision

Extend the standalone frozen comparison runner with an `assembly` stage. Keep
production jobs, project schemas, main-search ranking and the two-tab UI unchanged.
Use independent, versioned discovery, candidate-ledger and assembly interfaces.
Persist all retrieved rows and exclusions privately, separately from the bounded
catalog a model can select. Canonical film/unit/time authority remains server-owned.

One discovery request describes 2–4 soft editorial regions and at most six
complementary recipes through existing verified search capabilities. Preserve
canonical global and timed user direction, independently scoped music evidence,
exact film selection, and evidence uncertainty. Do not plan exact cuts first.
Collect at most 48 returned rows per recipe on a pinned index snapshot. Keep
query order/rank and rejected rows with reasons. Merge canonical source identities
and query evidence; select at most 48 eligible sources round-robin across recipes.
There are no new film quotas, ranking weights or semantic eligibility claims.

Compare three arms using the same configured text model:

1. A controlled replay of the existing fixed-slot selector using frozen timing
   and the shared initial catalog, with normal per-slot source-duration filtering.
2. A joint assembler using that catalog, choosing count, order, source windows
   and arbitrary legal integer output end frames, with at most 64 shots.
3. One optional expansion and reassembly, prompted by at most two concrete unmet
   discovery needs from arm 2. Retain the initial catalog and append at most 24
   related retained or newly retrieved candidates. Stop after this expansion.

Candidate authority and recorded query matches are distinct from captions and
observations. This experiment adds no footage inspection, continuous video
perception, motion adapter, exact Match Cuts, or plot verification. Beats and
regional instruction endpoints are soft timing context. Full passage coverage,
source validity and existing automatic source-reuse guards are hard constraints.
Reject invalid proposals instead of sorting cuts, substituting footage or hiding
failure with a repaired sequence. Fixed-slot abstention remains an explicit gap.

Use one frozen passage of at most 45 seconds with at most 64 baseline positions;
reject placed locks. The first case is Wish revision 4, 3.68–33.68 track seconds.
Dry-run is the default and does not connect to runtime services. Explicit execution
permits at most four hosted stage calls and eight search recipes total; failed
requests count and are not retried. Reuse existing listening evidence and refuse
missing or stale analysis. One arm's failure does not overwrite earlier artifacts.
Render technically valid arms with the existing renderer into private run output.
Never write projects, queue jobs or modify original film/music evidence.

## Evaluation and promotion gate

Record input, configuration and index identities; prompts, schemas and receipts;
query/candidate lineage; model-visible catalogs; source/timing choices; technical
failures; progress, latency and available usage. Describe the baseline as a
controlled replay, not an identical reproduction of the historical edit. More
cuts, more films and higher beat alignment are not quality scores.

Compare played results for musical phrasing, thematic relevance, continuity,
motif development, accidental repetition, gaps and cost. Leave human preference
unset until reviewed. A request for more footage is a model judgment; a failed
bounded search does not establish absence from the library. An inconclusive pilot
does not authorize production promotion. If useful, repeat on frozen Starjunk
evidence under a separately bounded run, then decide integration through the
existing Generate action. Keep rhythm, inspection and ingestion experiments separate.
