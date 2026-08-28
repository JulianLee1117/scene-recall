# ADR-0001: Durable evidence and replaceable derivations

- Status: Accepted
- Date: 2026-08-26
- Supersedes: None
- Superseded by: None

## Context

Film understanding models, prompts, embedding dimensions, and useful metadata
will continue to change. Treating today's caption or vector columns as the
canonical film record would make every improvement look like a destructive
full reingest and would make incompatible model generations difficult to
compare or roll back.

The original films and timestamped extraction artifacts retain substantially
more information than any single annotation or embedding.

## Decision

Treat source identity and provenance, source media, shot/time boundaries,
keyframes, dialogue evidence, and reconstructable media ranges as durable
evidence.

Treat annotations, semantic views, embeddings, indexes, summaries, and future
clip or audio representations as replaceable derivations. A new representation
must have a model/revision/contract-specific identity, coexist with incompatible
profiles where comparison or rollback matters, and support a targeted backfill
from retained evidence.

Do not continually add speculative feature columns to the canonical unit row.
Keep the existing PE unit/frame tables as a frozen legacy baseline; future
incompatible visual profiles belong in separate versioned tables.

## Consequences

- Model upgrades do not require film decoding, transcription, or hosted
  annotation when the retained evidence already contains their inputs.
- Old and new profiles can be compared and rolled back without mixing vectors.
- Completeness manifests and reconciliation add some storage and operational
  machinery.
- The legacy visual baseline still has coarser lineage than new profiles and
  must not be rebuilt piecemeal after an upstream checkpoint changes.
- New evidence that was never retained may still require a targeted media pass,
  but not an unrelated whole-pipeline reingest.
