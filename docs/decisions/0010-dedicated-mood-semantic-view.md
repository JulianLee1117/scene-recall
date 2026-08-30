# ADR-0010: Dedicated mood semantic view

- Status: Accepted
- Date: 2026-08-29
- Supersedes: None
- Superseded by: None

## Context

The modular Mood facet searched the broad `facets` semantic document. That
document also contains framing, setting, time of day, camera movement,
palette, and subjects. A Mood clause could therefore rank a scene for visual
or situational overlap instead of feeling or intensity, substantially
overlapping Scene and Look while presenting itself as a narrower control.

The units table already retains structured mood labels and energy. Fixing this
evidence boundary does not require another model, hosted annotation, film
decoding, or a new vector space.

## Decision

Add an independent non-empty `mood` semantic view containing only stored mood
labels and known energy. Serialize it deterministically as labeled parts in
this order: `mood: <ordered labels>; energy: <value>`. Omit an empty part and
do not treat `unknown` energy as evidence.

Keep the broad `facets` view unchanged for normal hybrid text search. Typed
Mood clauses search only `mood`. A scene dragged into Mood uses the same
mood-and-energy serialization as its query, so its effective evidence is
inspectable and reproducible.

Version the ordered semantic-view projection contract in the activation
manifest. A pre-Mood manifest is not eligible under the new code. Continue to
use the same physical profile table because the encoder, immutable revision,
dimension, and embedding contract are unchanged; compatible existing rows can
be reused. Activate only after `index-text` reconciliation proves exact current
source hashes and feature IDs for all required non-empty views across the
current units generation.

## Consequences

- Mood no longer matches because a scene shares framing, setting, palette, or
  subjects with the source.
- Mood remains limited by the quality and vocabulary of the stored hosted
  mood and energy annotations.
- Normal text search keeps the richer broad facets evidence.
- Existing films need only a local text-feature reconciliation; no film
  reingestion or hosted call is required.
- Until complete reconciliation publishes the new manifest, broad semantic
  search uses its established legacy fallback and focused semantic facets fail
  explicitly instead of serving an incomplete view generation.
