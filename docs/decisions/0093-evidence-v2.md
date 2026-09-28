# ADR-0093: Evidence v2 — versioned per-film evidence and compiled search tables

- Status: Accepted
- Date: 2026-09-27
- Supersedes: ADR-0066's no-world-knowledge producer rule; relaxes ADR-0004's
  restriction on priors and re-ranking

## Context

Search and the editor both reason from per-shot evidence. That evidence was a
caption from three low-detail stills per shot, produced in isolation: no
dialogue, film identity, story, characters or fame, and camera motion guessed
from stills. Measured failures (2026-09-27 probes, see `docs/current-work.md`)
traced most search and editor problems to this evidence, not to retrieval
plumbing. A hosted video model given the film title, shot table and burned-in
shot numbers returned correct characters, story context and iconic moments for
every shot of a test chunk; optical flow measured camera motion that stills
could not.

## Decision

1. Add an evidence layer, `pipeline/evidence`, between canonical structure
   (films, shots) and indexes. Each producer writes one immutable JSON artifact
   per film to `assets_dir/<film_id>/evidence/<kind>/<profile_id>.json`.
   The profile ID covers everything that shapes the output (model, prompt,
   schema, settings); the artifact records digests of its inputs, and readers
   treat mismatched inputs as stale.
2. Measure what is measurable locally (camera motion, subject positions, look,
   quality, cuts); use models for what needs understanding (story, characters,
   fame, craft). Model guesses of measurable quantities are hints only.
3. World knowledge is allowed and labelled. Film identity, cast and plot come
   from open data (Wikidata CC0, Wikipedia/Wikiquote CC BY-SA, Wikimedia
   pageviews, IMDb non-commercial ratings). TMDB is excluded because its API
   terms forbid use with ML/AI applications. Hosted understanding keys its
   output to known shot numbers rather than inventing timestamps.
4. Search reads compact tables compiled from artifacts (starting with
   `film_meta`). Compiled tables hold no primary data and can be dropped and
   rebuilt at any time.
5. Priors (fame, craft) and optional re-ranking are ranking signals under user
   control; they reorder retrieved candidates and never inject unrelated ones.

## Consequences

- New producers or fields add artifacts and compile steps, not migrations of
  the canonical `units` table.
- Every artifact can be recomputed per film when its producer or inputs change;
  old profiles remain on disk until explicitly removed.
- Hosted cost is bounded per film and recorded in artifacts.
