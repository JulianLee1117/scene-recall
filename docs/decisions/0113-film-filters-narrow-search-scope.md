# ADR-0113: Film filters narrow the search scope

- Status: Accepted
- Date: 2026-10-08
- Extends: [ADR-0084](0084-confirmed-movie-scope-in-search.md) and
  [ADR-0085](0085-inline-confirmed-movie-mentions.md) (movie scope by @mention and the
  movie picker)
- Supersedes: None
- Superseded by: None

## Context

A search could be scoped only to movies picked by name. The owner wants to
narrow it by era, genre and director, and later by shot attributes, while
Balanced, Famous and Hidden gems stay as ordering rather than filters. Open
metadata (ADR-0093) already holds a year, directors and Wikidata genres for all
186 films. Wikidata's genres are 144 fine-grained labels ("crime thriller
film", "neo-noir", "flashback film"); drama alone tags 136 films.

## Decision

Film filters are hard filters over films. The browser resolves them to the
existing `film_ids` scope, so retrieval, ranking and the request contract do
not change.

- `/library` adds `year`, `directors` and `genres` to each indexed film
  (`pipeline/search/film_facets.py`, cached per metadata table version).
  Genres are families mapped from Wikidata labels by word patterns: 24 today,
  and every film has at least one. Labels about form rather than genre
  ("independent film", "flashback film", "epic film") map to none. A film
  without metadata is left out whenever a filter is active.
- Eras are decades. The oldest decades share one "Before 1960"-style bucket,
  merged until it holds ten films.
- Values within a facet are alternatives; facets combine. @mentioned movies
  are narrowed by the filters too. When the filters leave no movie, no search
  runs and the page says so with a way to clear them. When every movie passes,
  no film list is sent.
- On the search page one **Filter** control replaces the **All movies**
  picker. Active filters sit beside it as removable chips (on phones the
  trigger's count stands in for them). Its panel has Era, Genre, Director and
  Movie sections; each option is counted against the other facets' filters,
  and options with no movie are dimmed. Changes apply as they are made and are
  scope changes: old scenes clear and the search reruns after the usual
  debounce. Picking a movie still appends its @mention. Going home clears the
  filters. The Lab keeps its own movie picker.

## Consequences

- A new film facet, such as country or language, is one entry in the facet
  table plus one field from `/library`.
- The request's 1,000-film limit bounds browser-side resolution. Past that
  library size, resolve filters on the server.
- Shot-level filters (shot size, light, camera movement, dialogue) are not
  film scope. Each retrieval channel must apply them before its candidate cut,
  or results thin out, so they need their own decision.
- Genre families are only as good as Wikidata's labels; Lady Bird, for one,
  has no coming-of-age label.
