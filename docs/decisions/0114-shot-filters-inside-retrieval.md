# ADR-0114: Shot filters apply inside retrieval

- Status: Accepted
- Date: 2026-10-09
- Extends: [ADR-0113](0113-film-filters-narrow-search-scope.md) (film filters)
- Supersedes: None
- Superseded by: None

## Context

Film filters (ADR-0113) narrow which movies a search covers. The owner also
wants to narrow by what is in the shot, mostly for editing: no dialogue under
music, close-ups, one person, a still camera, black and white, night,
outside. The evidence already exists for every shot: annotation columns on
`units` (framing, setting, time of day, dialogue lines) and measured columns
in `shot_evidence` (colorfulness, camera movement and its reliability, people).

Filtering after retrieval does not work: each channel keeps only its top few
hundred shots, so "rain, black and white, night" (about 1% of the library)
would keep a handful of results while better matches sat deeper.

## Decision

- **Facets.** `pipeline/search/shot_facets.py` holds a table of facets, each a
  key, labels and a function from one shot's stored evidence to one value, or
  to none when the evidence is missing or unreliable. Today: dialogue (none,
  spoken), size (close, medium, wide), people (none, one, two, group), camera
  (still, moving), color (color, black and white), time (day, dusk, night)
  and place (inside, outside). A shot with no value never passes a filter on
  that facet. Films whose shots almost never carry dialogue (silent films,
  missing subtitles) have no dialogue value, so "no dialogue" does not sweep
  them in. Black and white is colorfulness below 0.02, calibrated on the
  library: 98% of shots in black-and-white films (tinted ones included) and
  3% of shots in color films, mostly near-black fades. Camera movement below
  0.5 reliability is unknown.
- **Index.** The facet values are derived in memory as small integer codes
  per representative shot, cached per `units` and `shot_evidence` version and
  warmed at API startup (about 2 s for 237,000 shots). Nothing new is stored
  or backfilled.
- **Contract.** Recipe requests take `shot_filters` (facet to chosen values),
  validated against the table. Values within a facet are alternatives;
  facets combine with each other and with the film scope.
  `GET /search/shot-facets` returns the facets, labels and library-wide
  counts, so the Filter menu renders them without hardcoding.
- **Enforcement.** A recipe binds a `UnitScope` to its search execution
  (`pipeline/search/request.py`), so every clause and channel sees the same
  filters without new parameters. Resident vector channels (frames, semantic
  text views, category clauses) mask rows before top-k
  (`VectorMatrix.allowed_rows`), so their full depth is in scope. Database
  paths drop out-of-scope rows in `_rows_in_scope`; the keyword and quote
  channels, which can only filter after ranking, read deeper in proportion to
  how narrow the scope is (capped at 25 times). Browsing a movie in source
  order or by highlights takes the same filters (`shot` query pairs).
- **Interface.** The Filter menu adds a Shots group under Movies. Each shot
  facet has a few values, so they toggle in place; choices join the same
  draft and apply with Apply. Active shot filters show as chips beside the
  result count.

## Consequences

- A new shot facet is one table entry with its derivation, plus calibration on
  real shots; the menu picks it up from the endpoint.
- Coverage limits what a filter can find: time of day is labeled for 48% of
  shots (interiors usually are not), so Night misses some night shots.
- The rerank shortlist and priors run after the filters, unchanged.
- The keyword and quote channels can still thin out for very narrow scopes
  beyond the 25 times read limit; vector channels never do.
- Film filters still resolve in the browser (ADR-0113). Moving them server
  side would use this same scope.
