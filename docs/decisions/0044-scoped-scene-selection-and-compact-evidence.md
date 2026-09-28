# ADR-0044: Scoped scene selection and compact evidence

- Status: Accepted
- Date: 2026-09-13
- Extends: ADR-0033, ADR-0041 and ADR-0043
- Supersedes: unrestricted model-written source IDs and the selector's choices-array wire format

## Observed failure

The Nocturne generation for passage 53.99–143.99 failed after 229 seconds because
the selector returned a source outside one shot's offers. The saved project stayed
at revision 5. All 26 distinct returned IDs exist in the index; this does not prove
which slot borrowed a source, because the failed job did not persist its offers.
The preceding cut-frame fix constrained timing but left source IDs unrestricted.
The same selection stage received 407,705 input tokens for 554 sources. Repeated
IDs, captions, matching text and internal search diagnostics enlarged its input.

A live retry with local candidate indices exposed a second identity mismatch:
shot 13 selected a scene at 3004.5015–3008.7140 but supplied a 1597.8 source start
and description belonging to a different offered scene. The new offer manifest
made this exact mismatch reproducible. The compact request used 182,155 input
tokens, but independent identity and timestamp fields still left room for error.

## Decision

Keep the existing listen, plan and select stages, retrieval, source-aware timing
bands, abstention and atomic application. Use one `scoped-scene-choices-v3` model
interface for fixed slots and provisional whole-edit selection.

The private response is a `choices` object with every requested `shot_{slot}` key
required exactly once. Each value contains a source choice, reason, and an offered
`end_frame` only for provisional timing. The source choice is a closed, one-key
object such as `{"c313": 3004.6}`: the key uses the same request-local alias as the
catalog, and its timestamp is bounded to that source. Only sources offered to that
shot appear as schema alternatives. Explicit abstention is `source: null`; empty
offers permit only abstention. Keep shared shot fields and the cut enum outside
the alternatives. This preserves 24 candidates across 100 fixed slots or 32 timed
shots within provider schema limits, without truncation or a second numbering
system. Validate schema property, enum, string and depth budgets before sending.
Local validation independently checks the exact key set, types, offered sources
and cuts, actual source duration and passage coverage. Resolve aliases to canonical
source IDs only on the server. No old-wire compatibility
branch, silent repair, source substitution or retry loop is added.

Separate timing offers (`source_timing.py`), scene-choice schema and validation
(`scene_selection.py`), and model input/prose (`selection_prompt.py`). The existing
assembler owns retrieval, applying selections, repetition guards and alternatives.

The model gets a compact source catalog with short request-local aliases and
film references. Each slot has offered source references and
query-specific evidence. Keep canonical captions, indexed metadata/dialogue,
distinct matched text, matched frame times, search intent, evidence limitations,
song evidence and neighboring images. Deduplicate exact repeated text through
explicit references; remove backend scores, distances, availability catalogs and
duplicate request plumbing. Do not truncate evidence merely to meet a token budget
or change retrieval ranking, candidate counts or the persistent search evidence.

Before a hosted call, save a compact selection-offer manifest beside its receipt:
response contract, input/schema hashes, per-shot candidate-ID lists, alias mappings,
timing options and source ranges. A failed response can then be audited without rerunning search.
The manifest is derived diagnostic state, not another project or timeline. Existing
project/revision schemas, model configuration and normal search remain
unchanged. Old receipts remain readable as historical records.

## Validation and limits

Test per-shot source mapping, the observed cross-source timestamp, empty and noncontiguous offers, maximum schema sizes,
strict nullable/type behavior, invalid cut/source rejection, source bounds, fixed
cut compatibility, cancellation and atomic failure/application. Test compact input
for retention of distinct evidence and reduced duplication. Retry the failed song
through the real provider after focused and full tests.

The final implementation passes 1,278 backend tests (one platform skip). The live
Nocturne retry completed in 101.24 seconds and saved revision 6 with 26 selected
scenes across 28 positions. Two gaps have explicit evidence-based abstentions.
All returned sources, cut frames, actual source trims and passage coverage passed
validation; revision 5 remains available. Hosted selector input was 219,358 tokens,
46.2% below the original request, including the stronger source-bound schema.

These constraints establish source authority, not semantic suitability or artistic
quality. Repeated-footage guards and explained gaps still apply. A source that fits
the shortest offered duration may not fit the model's actual joint cut choices;
local feasibility validation remains required.

The schema uses the [documented Structured Outputs constraints](https://developers.openai.com/api/docs/guides/structured-outputs#supported-schemas):
required object properties, nested alternatives, numeric bounds and enums.
