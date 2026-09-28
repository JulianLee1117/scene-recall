"""Evidence v2: versioned per-film evidence and its compiled search tables.

Layers (see docs/decisions/0093-evidence-v2.md):

* sources      — films, subtitle files and other raw inputs; never modified;
* structure    — shots (the ``units`` table), scenes and dialogue lines;
* evidence     — producer outputs stored as immutable per-film artifacts under
                 ``assets_dir/<film_id>/evidence/<kind>/<profile_id>.json``;
* indexes      — compact LanceDB tables compiled from artifacts for search.

Producers are independent and rerunnable. A producer's profile identity covers
everything that can change its output (model, prompt, schema, settings), and an
artifact records digests of the inputs it was derived from, so stale evidence
is detected instead of silently reused.
"""
