# ADR-0002: Independent semantic text profile

- Status: Accepted
- Date: 2026-08-26
- Supersedes: None
- Superseded by: None

## Context

The legacy dense-text channel concatenated visual caption and overlapping
dialogue, then embedded that text with PE's paired text tower. This was a valid
text-to-image baseline, but it made visual description, spoken language, OCR,
and cinematography facets inseparable and made the configured dedicated text
encoder misleading.

A partial new index would be worse than a known compatible fallback because
results would silently mix coverage and vector generations.

## Decision

Project each non-empty `caption`, `dialogue`, `ocr`, and `facets` view into a
separate Qwen3-Embedding-0.6B row. Store those rows in a table whose identity
includes model revision, dimension, and embedding contract.

Activate that table only when a manifest proves exact coverage of the current
units-table generation. Search collapses matching views to one vote per unit
and returns the winning view and source text as evidence. If the profile is
missing, stale, corrupt, incomplete, unreadable, or locally unavailable, the
entire dense-text channel falls back to the legacy PE `txt_vec` profile.

Existing films use the local, idempotent `index-text` backfill. New ingestion
attempts the same derivation after publication, but failure does not unpublish
the film.

## Consequences

- Dialogue no longer dilutes a visual caption, and the UI can explain what
  textual evidence matched.
- Text-model generations can be rebuilt without decoding films or calling the
  hosted annotator.
- The local Qwen weights add disk, memory, and cold-start cost.
- Dialogue remains shot-level rather than an utterance-level index, and OCR is
  still supplied by the general annotator.
- Whole-profile fallback favors correctness and availability over partially
  serving a newer index.
