# Frozen music-editor comparisons

`pipeline.experiments.music_edit` compares one private frozen input without
changing a saved project. The dry default writes reviewable inputs and a manifest;
it does not load services, connect to the library or call a model. Existing output
directories are rejected so an earlier comparison cannot be overwritten.

## Timing and measured beat guides

Use a document with current scoped listening evidence, either as a plain
`ProjectDocument` or inside a project response's `document` field. The runner
privately clears the arrangement and prior AI directions. It never listens again.

```powershell
uv run python -m pipeline.experiments.music_edit timing --input frozen.json --out .tmp/timing-comparison
```

Review `beats-on-timing-payload.json`, `beats-off-timing-payload.json` and `run.json`.
The off variant withholds measured beats/downbeats and derived markers, while
retaining user markers, supplied lyrics, relative RMS and the same listening
observations. Old nested request data is not exposed through provenance. Rhythm
already described in the frozen listening text remains; this is an ablation of
explicit measured guides, not a new listen without rhythm.

To execute using an existing cached beats-on baseline and allow at most one new
timing request for beats-off, choose another new output directory:

```powershell
uv run python -m pipeline.experiments.music_edit timing --input frozen.json --out .tmp/timing-comparison-live --execute --allow-hosted --max-hosted-calls 1 --cached-baseline
```

If the baseline is missing from cache, execution stops before spending that
budget. On completion, each variant includes its resulting private document,
timing diagnostics and the source-fitting cut offers that its footage batches
would receive. The off offers also withhold additional pulse guides; they retain
the legal nominal/endpoint choices. This timing comparison does not retrieve or
select new scenes, render a video or judge playback quality.

## Targeted footage inspection

An enabled generation stores a private
`assets_dir/lab/requests/<job-id>-inspection-input.json` containing its provisional
`document` and frozen selector `contexts`. Replay that receipt so both variants
start with the same source choices, cut scope, candidate ledger and alternatives:

```powershell
uv run python -m pipeline.experiments.music_edit inspection --input inspection-input.json --out .tmp/inspection-comparison
```

Add `--execute` to replay cached stages while refusing every hosted request. A
new joint review needs a request budget; explicitly add
`--allow-hosted --max-hosted-calls 5` to permit the bounded maximum of four window
observations and one joint review. Execution uses the configured model, CPU editor
configuration and a stable library snapshot. It may write derived sampling/model
caches and private request receipts beneath the configured assets directory;
it does not modify the project store or enqueue work. `.env` is loaded only for
execution, consistently with the existing CLI and worker.

`inspection-off` is the unchanged frozen provisional arrangement with zero
inspection calls. `inspection-on` invokes the same bounded coordinator as
generation. `--known-failures cases.json` accepts a list of
`{"slot_id": "existing-slot-id", "reason": "Observed issue"}` examples. Coverage
reports distinguish flagged, inspected and missed known examples; unlabelled
shots are not counted as negatives and precision remains unknown.

To test a known caption mismatch that an older selector never flagged, optionally
provide `--inject-hints hints.json`, mapping existing ledger slot IDs to
`"action_timing"` or `"visual_fit"`. Only the private ledger hint changes. The
run records these injected IDs and marks automatic hints as modified, so a
successful correction cannot be claimed as automatic detection coverage. Keep
an unmodified replay if the question is whether the selector flags the issue.

## Footage discovery and flexible assembly

The `assembly` stage is a private pilot under [ADR-0065](../decisions/0065-private-discovery-and-flexible-assembly.md).
Use a frozen project revision with current listening evidence, a passage of at
most 45 seconds, 1–64 existing positions and no placed locks. Preparation validates
the input without loading services or calling a model:

```powershell
uv run python -m pipeline.experiments.music_edit assembly --input frozen-wish.json --out .tmp/assembly-dry
```

Execution uses a pinned index snapshot, the existing CPU editor resource policy
and configured text planner. It verifies original imported music identity before
any paid call. Original films, music and saved projects stay unchanged:

```powershell
uv run python -m pipeline.experiments.music_edit assembly --input frozen-wish.json --out .tmp/assembly-live --execute --allow-hosted --max-hosted-calls 4
```

The four-call ceiling covers initial discovery planning, fixed-slot selection,
joint assembly and optional expanded reassembly. Failed calls count; no calls
are retried and this stage never listens again or adds footage inspection.
The independent context pilot is disabled in this runtime. At most six initial
recipes and two follow-ups use the existing search adapters, with up to 48 rows
each. The model sees a round-robin catalog of at most 48 eligible sources;
the ledger retains all returned rows, exclusions and query evidence. Canonical
source IDs are merged without film quotas or new shared ranking policies.

`fixed-slots` preserves the frozen cuts and slot directions while selecting
from the common pool. Short sources remain ineligible for a longer fixed slot;
explicit abstention remains a visible gap. This is a controlled replay, not
the exact historic render. `joint-assembly` sees the same indexed evidence and
can choose count, ordering, source windows and arbitrary legal interior cut
frames. Beats and editorial-region endpoints do not force cuts.

The joint assembler may nominate two concrete missing visual roles with supported
search recipes. `expanded-discovery` retains its original catalog and adds up to
24 candidates through new queries or related retained results. Repeating an
original recipe revisits its remaining eligible rows without rerunning it;
shared exact clauses identify other eligible retained rows, without inventing
semantic relevance. There is only one expansion. No nominated need, or no new
eligible candidate, skips the final model call explicitly.

Read `initial-ledger.json` / `expanded-ledger.json`, the separate catalogs, and
each arm's payload, schema, response, receipt, document and diagnostics. Ledger
checkpoints preserve completed queries and failed-query evidence. Progress is
written to `progress.json` and printed to the terminal. `run.json` records stage
times, available usage, failures and whether the frozen input remained unchanged.
Valid arms render to `media/lab/renders/<arm>/output.mp4` inside the new output
directory. Rendering uses the normal 720p preview profile and preserves explicit
fixed-baseline gaps. A failed later arm leaves earlier documents and renders intact.
Ctrl+C stops at the existing operation boundary and records cancellation.

First compare Wish revision 4 (3.68–33.68 source-track seconds). Look at musical
phrasing, meaning, motif progression, continuity, accidental repetition, gaps and
cost. Candidate counts and model reasons do not establish creative success or
library-wide absence. Human preference stays unset. Do not automatically promote
the engine or launch the Starjunk follow-up; review this bounded pilot first.

### First Wish pilot — 2026-09-15

The bounded live run is complete at `.tmp/wish-assembly-20260915/live/run.json`,
using frozen project `8d6dc709-21ce-43f4-9897-bba4be96d445`, revision 4,
3.68–33.68 source-track seconds. Three discovery recipes returned 144 eligible
unique sources. All 144 remain in the ledger; 48 were offered to the model.

- `fixed-slots`: 16 positions, four placed scenes from four films, 12 explicit
  gaps. Its old specific shot requests often had no suitable match in the shared
  catalog, despite having duration-eligible sources. This comparison measures
  adaptation to that catalog, not the quality of the historical saved edit.
- `joint-assembly`: 12 placed scenes from 11 films, no gaps, rendered successfully.
  Source windows and timing passed validation; creative quality remains unjudged.
- `expanded-discovery`: skipped because the assembler requested no further
  discovery. This run does not evaluate whether expansion improves the result.

Three hosted calls used 5,636, 79,363 and 28,679 total tokens respectively
(discovery, fixed selection, joint assembly). The first catalog's breadth and
the baseline's much larger request are useful evaluation data, not prescribed
production limits. Both completed arms have private 30-second preview renders.
All seven saved projects retained their exact current revision and document hash.
No human preference has been recorded and no production behavior was changed.

## Reading timing and inspection results

`run.json` records frozen input hashes, source project/revision when supplied,
variant duration, stage progress, hosted calls and available usage, diagnostics,
placed/distinct-unit/gap counts and exact source/timing changes. Clip UUID churn
alone is not a source change. The comparison does not infer that every change
is better: `human_preference` remains null for later played review. Input files
and saved source provenance remain intact even on failure; failed requests count
against the explicit budget and are not retried.

The implementation boundary and evidence limits are in
[ADR-0061](../decisions/0061-targeted-footage-inspection-and-frozen-comparisons.md).
