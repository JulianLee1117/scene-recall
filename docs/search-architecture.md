# Scene Recall architecture

Status: current architecture contract.

`README.md` documents commands and runnable behavior. This document defines the
current system boundaries. Records in `docs/decisions/` explain why material
choices were made but do not override this document.

## Map

Where to read and what to change. Each row names the contract section, the
code, and the decisions in force. The decision log's index
([`decisions/README.md`](decisions/README.md)) marks superseded and frozen
records.

| Area | Contract section | Code | Decisions in force |
|---|---|---|---|
| Evidence: per-film artifacts, compiled tables | Evidence v2 | `pipeline/evidence/` | 0001, 0093, 0095, 0098, 0099 |
| Ingestion, acquisition, storage | Ingestion | `pipeline/ingest/`, `pipeline/acquisition/`, `pipeline/index/` | 0014-0016, 0023, 0025, 0047, 0049, 0052, 0056, 0057, 0059, 0069, 0079, 0080, 0102 |
| Search | Search and Lab application boundary; Text retrieval; Reference and Framing retrieval; Modular recipe retrieval; Activation and fallback | `pipeline/search/`, `pipeline/index/`, `pipeline/api/main.py` | 0094, 0097, 0101, 0111, 0112, 0113, 0114, with 0002-0021 and 0082-0087 where not superseded |
| Saved scenes and interaction log | Durable user state | `pipeline/bookmarks.py`, `pipeline/interactions.py` | 0006 |
| Lab projects, jobs, workers | Durable projects and jobs | `pipeline/lab/` (store, worker, registry) | 0024, 0025, 0051, 0055, 0059, 0068, 0100 |
| AI Music Video v1 (`lab.harness: v1`) | AI Music Video | `pipeline/lab/` (music, planners, generation, media) | 0028-0045, 0050, 0058, 0060, 0064, 0077, 0078 |
| Editor harness v2 (default) | Editor harness v2 | `pipeline/lab/harness/` | 0096, 0098, 0099, 0103, 0104, 0105 |
| Render effects (feature-locked overlays, hard crops, panels, masks, screens) | Effects | `pipeline/lab/effects.py`, `pipeline/lab/screens.py`, `pipeline/lab/media.py` | 0106, 0107, 0108, 0099 |
| Generated sources (AI clips placed as shots) | Generated sources | `pipeline/lab/generated.py` | 0109 |
| Alg Mods (treatments on one film window; tile bank) | Alg Mods Lab | `pipeline/algmods/`, `web/features/lab/algmods/` | 0110 (0070 pattern) |
| Regions (masking spec shared by treatments; subject by video matte, box, near/far by depth) | Alg Mods Lab | `pipeline/lab/regions.py`, `pipeline/lab/matte.py`, `pipeline/lab/depth.py` | 0110 |
| Grafts (landmark-aligned pieces of other shots, as plans) | Alg Mods Lab | `pipeline/lab/grafts.py`, `pipeline/algmods/composite.py` | 0110 (landmarks from 0099) |
| Match Cuts | Moment-level Match Cuts | `pipeline/evidence/moments.py`, `pipeline/matching/moments/`, `web/features/lab/matching/` | 0099 (0008 gates ordinary search) |
| Web app | Search and Lab application boundary | `web/` (read `web/AGENTS.md`) | 0051, 0067, 0115, 0117 |

Frozen: kept runnable, with no new investment. Their decision records hold the
detail.
- Transitions Lab: 0070-0076.
- The saved-project Match Cuts editor and its finder, removed: 0026, 0027, 0038 (0116, 0117).
- Source context: 0066.
- Targeted footage inspection: 0061.
- Flexible-assembly comparison: 0065.
- Framing representation pilot: 0092.

Retired: Jev and intent routing (0086-0091, by 0094).

## Product contract

Scene Recall supports four related jobs:

1. Find a film moment someone remembers (description, dialogue, character, event).
2. Surface great footage for an idea — famous and forgotten — without junk.
3. Save source moments for later retrieval.
4. Assemble and revise source-backed sequences in bounded Lab experiments.

Retrieval returns source-backed evidence: film identity, time range, and the
matched frame, line or text. AI Music Video can interpret music and organize
retrieved evidence, but it must not invent first-stage results or legal source
ranges.

```text
text query (search v2, ADR-0094)
  -> channels: PE text-to-frame | semantic text views (each view ranked on its
     own, fused by rank) | full text | quotes over subtitle lines
  -> weighted reciprocal-rank fusion (quote channel weighted up for quote-like queries)
  -> one relevance score per candidate (ADR-0097): fused evidence, then the
     cross-encoder's verdict on the fused shortlist plus every retriever's
     best matches, then bounded factors for query signals
  -> junk filtering and visual deduplication
  -> bounded prior factors under a preset (balanced | famous | gems)
  -> one card per dramatic scene, temporal spread, film diversity (named films exempt)
  -> hero thumbnails, badges, story line, scene and matched line

reference image (standalone API compatibility)
  -> PE frame candidates
  -> bounded spatial reranking

reference image + text (standalone API compatibility)
  -> mandatory reference shortlist
  -> text reranks only that shortlist

typed recipe (product UI; one to three total clauses)
  -> explicit text or indexed-scene evidence adapters
  -> optional query-bound uploaded Look or Framing adapter
  -> one ranking per independent input and reciprocal-rank fusion
  -> mandatory uploaded-image or composition gate when present
  -> final filtering, priors, scene cards and diversity

result
  -> film + timestamp + matched frame/line/text + playable media
```

The product calls spatial reference matching **Framing**; the standalone API
and recipe contract retain the `composition` name for compatibility. It is
coarse appearance and position matching, not an exact editorial Match Cut
mode. Framing v2 (measured subject layout, ADR-0093) will replace its 6×6
embedding grids; until then the grids stay active and their bulk preparation
queue stays frozen.

Match cuts are deliberately separate from ordinary search:

```text
any instant of any shot (Lab Match Cuts and editor transitions; ADR-0099)
  -> library moment index: every usable 4 fps instant (masks, keypoints, light,
     edges, colour, measured motion)
  -> coarse part-weighted retrieval, exact calibrated pair scoring
  -> optional crop (vertical formats, reframing) and browser audition
```

The Jev/intent-routing comparisons (ADR-0086, 0088, 0091) are retired by
ADR-0094; their receipts remain historical evidence only.

## Durable evidence and replaceable derivations

Durable assets are the source film and timestamped evidence that can support
future derivations:

- source identity, hash, and path;
- a selected raw external subtitle sidecar when the release provides one;
- raw release subtitle copies retained in their imported directory, whether
  still in staging or moved intact to an operator-managed evidence archive;
- shot and time boundaries;
- extracted frames and media recipes;
- reconstructable clip ranges.

Model outputs are replaceable derivations:

- annotations and semantic views;
- parsed subtitle and speech-transcript rows;
- embeddings and indexes;
- future clip, audio, or summary profiles;
- active-profile manifests.

A derivation must be independently backfillable and identified by its model,
available immutable revision, dimensions, contract, inputs, and relevant
schema or prompt. When a model resolver exposes only an alias, the manifest
must at least include that exact identifier plus the engine and profile
versions, and the alias must not be refreshed without a profile bump. Never mix
incompatible vector spaces. Preserve old profiles until a replacement is
complete and deliberately activated; missing optional derivations must degrade
to a known-safe baseline.

### Evidence v2

ADR-0093 adds `pipeline/evidence` between canonical structure (films, shots,
frames, units) and the indexes. Each producer writes one immutable JSON artifact
per film to `assets_dir/<film_id>/evidence/<kind>/<profile_id>.json[.gz]`. The
profile ID hashes everything that shapes the output (model, prompt, schema,
settings); recorded input digests make stale artifacts detectable. Producers:

| Kind | Producer | Facts |
|---|---|---|
| `metadata` | open data | Wikidata identity, cast and characters, directors, genres; Wikipedia plot; Wikiquote quotes; IMDb votes and Wikimedia pageviews (TMDB excluded by its terms) |
| `audio`, `subtitles` | Silero VAD, OpenSubtitles | English subtitles for Whisper-only films, synced to speech (FFT alignment, frame-rate scales, windowed shifts) and accepted by lift/prominence/text agreement; raw downloads archived |
| `understanding` | Gemini 3.8 Flash | per chunk of ≤160 shots: 240p shot-numbered proxy + shot table + dialogue + cast/plot context → scenes, per-shot characters, action, peak time, emotion, line, sound, fame 0-3, craft 0-3, cut hint, iconic moments; resumable chunk receipts; standard or half-price batch transport; a synopsis that trips a content filter is retried without it; a clip refused even without it is closed with no records and listed in the artifact; shots an answer skipped are re-requested once per consecutive run and spliced into their chunk |
| `highlights` | Gemini 3.8 Flash (text) | one call per film: merges the understanding pass's iconic flags into the film's best-known moments, ranked by recognizability (with Wikiquote quotes), plus visual motifs |
| `measure` | RAFT-small, RF-DETR | one GPU decode per film: camera flow series (labels derived at compile time, including slow drift), hidden cuts, subject boxes and main-subject track, letterbox-aware look and palette, sharpness |
| `hero` | frame pick, keyframe embeddings | how a shot is shown (ADR-0098): its pictures (split at hidden cuts and wherever neighbouring keyframes stop looking alike, black removed), the focus span (the picture holding the peak), the best still inside it at 1280 px, and a 4 s H.264 hover preview around the peak where the ingest one strays outside a focus span of at least 1 s |
| `synthesis` | priors | within-film and library fame, craft, distinctiveness; rare iconic and hidden-gem flags; per-film highlights and gems |

Measured facts come from pixels; model estimates of measurable quantities are
hints only. World knowledge is allowed and labelled by its producer. Search
reads compiled tables — `film_meta`, `shot_evidence`, `scenes`, `dialogue_lines`
(quote index: positions, no stemming, stop words kept) — which hold no primary
data and are rebuilt by `python -m pipeline.evidence compile [--rebuild]`.
A schema that only adds nullable columns migrates in place (existing rows read
null until recompiled); removed or retyped columns need `--rebuild`.
Compilation serves each kind's current profile, else the newest earlier profile
of the same producer, so a settings change never blanks evidence while its new
profile is backfilled; each row's `sources` names the profiles that served it.
Producers read only current-profile inputs and recompute once those exist.
`prune` removes a superseded profile only after its replacement exists.
After ingestion, `pipeline.evidence.pipeline.refresh_films` brings a new film's
evidence up to date (`ingest.evidence`); cached passes skip and failures never
undo publication.

### Optional source context

Status: frozen. The understanding pass (ADR-0093) supersedes it for story
context.

`pipeline.context` (ADR-0066) keeps immutable, scoped context artifacts from a
twenty-window pilot. With `lab.context_profile` set (default null), v1 scene
selection reads them after retrieval, read-only. They never change retrieval,
ranking or legal clip ranges. Details are in ADR-0066.

## Durable user state

Bookmarks are user-authored state, not an index derivation. They live in a
schema-versioned SQLite database under the configured `state_dir`, outside the
replaceable `assets_dir`. Index repair, backfill, and film reingestion must not
delete them.

Each bookmark preserves the source `film_id` and evidence timestamp as its
durable anchor. The unit ID and frame index recorded when it was saved are
derived lookup hints. If a later compatible reingest changes shot boundaries,
the API may resolve the bookmark to the current unit containing that timestamp,
but only within the same film identity. Unit intervals are resolved as
half-open ranges, so an exact shared boundary belongs to the following unit;
the absolute final boundary of a film deterministically falls back to its last
unit. Missing or temporarily unavailable source/index data leaves an explicit
unavailable bookmark rather than silently rebinding or deleting user state.

The interaction (taste) log, `state_dir/interactions.sqlite3`, records only
searches, plays and saves made in the web app. The app marks its requests with
`X-Scene-Recall-Client: app`; unmarked API calls from scripts and agents are
served normally but not recorded. The log is local and append-only, and no
ranking reads it yet.

## Dataflow

### Search and Lab application boundary

The Info tab is a lazy-loaded, read-only feature under `web/features/info`.
Versioned explanatory content and lightweight overview diagrams are separate
from executable ingestion/retrieval logic. Topic navigation presents one readable
article at a time, with visible steps and adjacent technical details. Diagrams
are noninteractive; there are no nested disclosures or scripted scroll jumps.
`GET /project/info` projects an explicit allowlist of the API's already-loaded
model, threshold, retrieval and Lab configuration. It reads no environment
secrets, source paths, database tables or per-film manifests and makes no model
or provider call. Configured values are not feature-readiness or per-film
provenance claims. Refresh rereads this snapshot without reloading configuration;
an unavailable endpoint leaves the guide usable with settings marked unavailable.
Content changes accompany changes to the architecture described here (ADR-0081).

Ordinary search starts with broad text and persistent centered category controls.
The description accepts mixed intent, including subjects, action, shot scale,
appearance and feeling; these words are relevance clues, not inferred hard
filters. ADR-0084 adds local catalog completion through `MovieSearchInput.tsx`.
It preserves a native input, colors the active `@` phrase with a subtle static
glow, and positions an absolute dropdown near the caret, clamped to the viewport.
At most four options appear without growing the bar; the native input scrolls
horizontally. An explicit `@` at the start or after whitespace enables prefix
completion (`@Before`, `@Before Sunrise`); bare `@` prompts for a movie name,
and email addresses are not mentions. The first `@` match is highlighted.
ArrowUp/ArrowDown navigate; Enter or Tab confirms only a visible active option,
and clicking confirms that option. Escape cancels completion, removes only its
active `@`, and preserves the words and caret as plain text. Suggestions remain
suppressed through typing and refocus until a fresh `@` or an empty field.
Outside blur dismisses without applying a film. Ordinary prose offers complete-title matches only, initially
unselected: plain Enter searches the original words; arrows can select a match
for Enter/Tab confirmation. Completion uses the caret position and preserves
description on both sides; inside a recognized full mention it consumes the
whole mention. ADR-0085 keeps the confirmed `@Title (year)` at that exact position
in the sentence using validated text ranges, with the caret immediately after
it. The page compiles the visible draft into a separate retrieval description
and the union of confirmed film IDs; surrounding visible wording is preserved.
Backspace immediately after a mention or Delete immediately before it removes
the whole mention. Edits inside or across a title invalidate its confirmation;
other editing remains native. Repeated mentions retain scope until the final
one is removed. Native undo may restore
words without restoring invalidated scope; confirmation is never guessed.
Scope changes cancel
stale requests and clear previously displayed scenes from the old scope.
No result recovery silently broadens these filters. Ambiguous versions display
their catalog years; common-word and style-reference guards avoid obvious
misreadings without claiming general natural-language intent recognition.

Film filters (ADR-0113) narrow that scope by era, genre family, director and
title.
`/library` reports each indexed film's year, directors and genre families
(`pipeline/search/film_facets.py`, from its Wikidata genre labels and its film
form, so an animated or anime film joins the Animation and Anime families with
no genre label of its own; a live-action film with animated passages does
not); the browser resolves the filters to
`film_ids`, so retrieval and ranking are unchanged. Values within a facet are
alternatives, and excluded values leave movies out ("Drama, not Animation");
facets combine; @mentioned movies are narrowed too. Filters
that leave no movie run no search and say so. One **Filter** control beside
Refine, home screen included, opens a menu of the facets (Movie among them; it
never writes @mentions), one section at a time; active filters show as one chip per section beside
the result count, and each option is counted against the other facets. A
**View** menu holds presentation only: the ranking preset, row size and
Details, remembered in the browser, with a non-default preset shown as a chip
beside the count. A grid that cannot fill the viewport from the loaded prefix
requests the next one itself. Filter choices are a draft until the menu
closes (Apply or a click away), then rerun the search once like an edited
query, keeping the current scenes until the filtered ones arrive; ordering
changes rerun after the same short pause.

Shot filters (ADR-0114) narrow by what is in the shot: dialogue, size,
people, camera, color, time and place. `pipeline/search/shot_facets.py`
derives one value per representative shot from `units` annotations and
`shot_evidence` measurements (none when missing or unreliable; people come
from the annotation's clearly-visible count, which reads drawn and stop-motion
characters the COCO detector cannot and agrees with it on live action, the
detector's count only when the annotation is missing), in memory per
table version; `GET /search/shot-facets` lists them. A recipe's
`shot_filters` bind a `UnitScope` to its search execution: resident channels
mask rows before top-k, database paths drop out-of-scope rows in
`_rows_in_scope`, and the keyword and quote channels read deeper in
proportion to the scope's narrowness. Browsing takes the same filters. The
Filter menu shows Shots beside Movies; each shot facet starts at Any and picks
one value (the API also takes several), applied with the film filters.

With only movie scope supplied, `GET /library/scenes` browses selected published
films in request order and source chronology using the usual bounded result
prefix and `has_more`/`next_limit` envelope. It requires nonempty explicit scope,
pins a shared snapshot and invokes no models. Browse keeps representative units
and the existing caption-junk exclusions; every returned card has retained
frame identity, path and a valid source timestamp. Similarity, semantic profile
readiness and diversity ranking are not prerequisites for chronological browse.
Category-only searching still submits its existing focused clauses without a
fabricated main-description clause. Words remains dialogue plus OCR until its
separate retrieval-policy change; plot context and exact shot-type filters are
not activated by these input changes.

Each category keeps the same footprint in empty, text and reference states. One
compact editor anchors near the chosen category without moving the main bar.
Empty text categories open directly to text entry; a reference opens only its
preview and actions. **Use text instead** changes the editor mode while retaining
the active reference until nonempty text is applied. **Change scene** opens a
clearly labeled reference lookup with its own query/results, independent of the
main query. **Remove** is an editor action. Examples appear only when no category
editor is open; closing it leaves the selected clue in its category control.
**Change aspect** stages a keyboard/touch move through the same typed reference
callbacks as dragging. Selecting a destination is inert until **Move to…** or
**Replace…** is applied; cancel preserves the original clue. Indexed references
can change among the existing facets; uploads can move only between Look and
Framing. Moves preserve source/frame identity, replace the destination explicitly
and obey the existing clause limit. IME composition Enter does not commit a text
detail. When a description orders their shortlist, Framing and uploaded Look
expose their mandatory visual-gate explanation/removal action; indexed Look
remains a relevance preference.
A dragged thumbnail follows the cursor, the source dims and the destination
highlights. Releasing a reference outside the Refine area removes it through
the same typed removal, with a short-lived undo. Search cards remain draggable
without a drag badge; Saved cards expose no dragging. Their playback, bookmark
and Related actions remain available. The existing typed recipe remains the
backend contract. Dragging adds a scene reference to the current recipe;
the keyboard-accessible Related menu starts a new recipe from that reference,
keeping only movie scope and preset. Both use the same typed clue operations.
If no current Framing result also has main-description evidence, the interface
explains the missing overlap within that result set and offers removal of Framing
or deeper retrieval when available. Returned scenes and paging remain visible;
the browser never converts partial agreement into an empty result stream.
This presentation does not change backend ranking, facet
semantics, mandatory visual gates, movie scope or progressive result windows.
The source player distinguishes a retrieved keyframe from its live playhead;
moving playback does not silently change the indexed bookmark or search anchor.

Result hover (ADR-0117) shows gold film title and timestamp, then one snippet.
The explicit Words clause's own dialogue/OCR evidence takes priority, followed
by the main query's strong quote (ordered overlap >= 0.8) or selected semantic
dialogue/OCR evidence. These excerpts are labelled Spoken or On screen; otherwise
the shot's `action`, falling back to `caption`, appears without a label. Channel
ranks and unrelated clauses do not choose the snippet. Recipe text evidence
preserves its own source, quote score and source timing before fusion; the
browser never borrows another clause's top-level text or timestamp. The selected
timed passage controls the hover time and initial playback, while source/save
actions retain the indexed frame anchor. Retrieval and ranking are unchanged.

Shot details load `GET /library/shot/{unit_id}/dialogue` on demand. It reads the
canonical published shot and compiled `dialogue_lines` from one pinned snapshot,
selecting by film and half-open time overlap so cues crossing cuts remain visible.
The response retains source times and provenance and returns at most 200 lines
chronologically, with an explicit truncation flag. Missing film-level dialogue
coverage is unavailable, not proof of silence. There is no inference or backfill
on this path. The Dialogue row shows a few lines around the matched passage,
expands longer content and plays source timestamps. On-screen match text stays
separate. These facts remain attached to the retrieved result while playback
advances; empty/unavailable dialogue is omitted and failed reads can be retried.
The detailed retrieval breakdown remains available separately.

ADR-0062 adds an optional source-preserving browser audio representation for
full-scene playback. `GET /video/{film_id}/playback` performs a read-only cache
lookup and returns either the original URL or a URL pinned by a representation
token. The modal resolves this once before assigning its video source and
discards stale resolution responses. Unqualified `/video/{film_id}` always
streams the original, including existing Lab callers. A request with
`representation=<token>` must match the current validated cache; a stale or
missing representation fails with HTTP 409 and requires reopening, rather than
switching an active sequence of byte ranges to a different file. API requests
do not probe, transcode or enqueue preparation work.

The initial `video-copy-aac-stereo-v1` profile copies H.264/HEVC video and repairs
incompatible source-default audio as stereo AAC, retaining its language rather
than preferring an English alternative. Already-supported audio and unsupported
video codecs retain original playback. Original film bytes, audio tracks, film
identity, evidence timestamps, bookmarks and search/model profiles are unchanged.
The derived MP4 is optional, versioned and independently rebuildable. ADR-0069
allows a separate configured `paths.playback_dir/<film_id>/<profile>` root;
omitting it retains the legacy film asset directory. New ingestion uses the
configured root while readers accept valid legacy copies during migration.
A manifest ties source identity, the conversion recipe
and FFmpeg version to the artifact fingerprint. Publication follows bounded
source/output duration, decoded presentation-time and audio checks. Validation
uses the same container-relative timeline as existing source evidence and must
preserve relative audio/video offsets; sampled checks are not proof of complete
continuous synchronization or universal browser codec support.

Playback relocation is an explicit, resumable offline operation. It takes the
film and preparation locks, copies and independently hashes destination bytes,
then atomically publishes a receipt with the destination file identity and the
unchanged representation token. Only the verified legacy derivative is removed;
the tiny old receipt remains for recovery. Busy films are skipped. Prepared
range responses open the validated file before streaming, so relocation cannot
redirect an already-open range to different bytes. Original source identities,
timestamps and search profiles do not change.

Database history pruning is independently explicit and idle-only. A pinned,
compatible optional Lance maintenance runtime provides native dry-run and
pruning-only operations, without compaction, reembedding or reindexing.
The operator chooses retention age (fourteen days by default) or latest version
count. Latest data, tagged versions and unverified files remain protected.
The command excludes API lifetimes, both workers, independent ingestion and
publication through shared storage locks. API startup acquires its reader lease
before opening the database. It explains every table before applying any cleanup
and verifies current versions, row counts and index definitions afterward.
No interactive endpoint or ingestion job implicitly retires history.

Explicit index withdrawal identifies a film by its immutable ID and exact indexed
source path. Under ingestion, film and publication locks, it removes only that
film's canonical and model-scoped rows, repairs full-text coverage and republishes
only profiles that were already complete. An audit receipt records pre/post table
versions and readiness; recoverable failures restore the retained original table
versions. Jobs, download records and authored state remain separate. Replacing
a source filename cannot cause this operation to remove the replacement's bytes.

Rejected releases are deleted, not archived (ADR-0102). With `--delete-files`,
after the index rows are removed, the same operation deletes:
- the source video, only while its content hash still equals the film ID;
- the film's asset folder, emptied under the film lock and removed after it is
  released while the global ingestion lock is still held;
- its playback copy.

Deletion refuses links and paths outside the configured roots. A failure leaves
the index withdrawal in place with a `files_pending` receipt, and rerunning
finishes an already withdrawn film. Without the flag, files are untouched.

One `AppBar` (Search, Saved, Films, Info, Lab) tops every route. On the home
page the four views switch in place; elsewhere they are links, and
`/?tab=saved|films|info` lands on that view (ADR-0115). The Lab is a
registry-driven experiment directory, a page-chrome page like Films, listing
saved edits as summaries: `GET /lab/projects` carries `track_name`,
`clip_count`, `sheet_unit_ids` and `active_job_count`, never a document. The
explicit registry owns each entry `route`, saved-edit `project_route` and
entry `persistence` (`project` or `session`). AI Music Video uses
`/lab/music-sketch`; Match Cuts enters the projectless `/match` session, which
is also its `project_route`, under the `visual-rhymes` identifier it has always
had (its frozen editor is gone, ADR-0116).
Transitions enters the projectless `/lab/transitions` workspace (ADR-0070).
Alg Mods enters the projectless `/lab/alg-mods` session (ADR-0110).
Every lab screen, including loading, empty and error states, starts with
`LabWorkspaceHeader`: the app bar, then the Labs return and the experiment's
title; project editors add the project's name, save state and
`ProjectActions`, sessions add nothing. The header is the app's, rendered
before any container of the experiment's; everything below it is the
experiment's own. Every way out of a project editor, the app bar's places
included, passes the one unsaved-edits guard (`useProjectExit`). Reusable
conventions for future experiments are in [Lab workspaces](lab-workspaces.md)
(ADR-0051, ADR-0115).
Music uses one project workspace with **AI direction** and **Edit** views
(ADR-0064). The first holds global/passage instructions and generation preferences;
the second holds synchronized sequence preview, selected-shot inspector and a
two-row clip/music timeline. Save, Undo, History, music selection and job progress
are shared. Beat and cue visibility plus zoom are optional controls. Passage selection
uses one waveform in a focused dialog; trimming remains local until Use this
section. Cancelling an uploaded replacement restores the opening revision
and pre-picker Undo history. Whole generation remains explicit; filling gaps or
replacing a chosen shot preserves the existing timeline.
Match Cuts is one screen: an outgoing cut point, a results grid of incoming
instants and an in-browser audition (ADR-0099). The Lab is one folder,
`web/features/lab`: its directory at the root, the shared kit in `kit/` (the
workspace header, the project lifecycle, the source browser and job status),
and one folder per experiment beside it (`music`, `matching`, `transitions`,
`algmods`); an experiment imports the kit and nothing else of the Lab
(ADR-0116). All call shared Python project, source, job and render services
under `pipeline/lab`; no generic plugin system or second search engine is
required.

### Durable projects and jobs

Lab uses `state_dir/lab/lab.sqlite3`, independently schema-versioned from the
bookmark store. Entering an editor without a saved project reads canonical
defaults from `GET /lab/experiments/{experiment_id}/draft`, with an empty ID and
revision zero, and writes no project, revision or job. Name or document edits
make a draft dirty; browsing, playback and transient view changes do not.
Untouched exit returns directly to Labs. Dirty exit offers Save/Discard/Cancel;
failed saves or edits racing a save keep the workspace open. A clean Save is a
client no-op. First save sends the complete changed document to
`POST /lab/projects`; all new source anchors are validated before the project
and first revision are inserted together. Explicit creation with an omitted
document remains compatible. Do not delete historical empty projects implicitly.
Projects have optimistic revisions; every persisted save and restore
appends a revision. Source clips preserve film identity and start/end times,
with optional unit hints, normalized crop/region and fixed-instant or bounded
reference-window metadata. Originals and unavailable saved selections survive
derived index changes; new source ranges must resolve against known films.

Audio imports preserve original bytes under `state_dir/lab/tracks`, identified
by full SHA-256. `POST /lab/tracks` imports this evidence independently of an edit
and returns its public identity, name and duration. New drafts stage the track
and passage locally; picker cancellation restores the opening document, name
and Undo history. Applying a passage saves its input before its rhythm job.
Discarding a draft or cancelling an import picker never deletes original media.
Saved-project imports retain their existing revision and replacement behavior.
Clients never select server paths. Project jobs require a saved input revision;
Match search and transition-render sessions retain their projectless job boundary. Clean exits
can leave durable work running. Job snapshots freeze the
project revision. Generated results preserve locked clips and their positions;
revision conflicts retain unapplied proposals rather than overwrite new work.

The API transactionally enqueues Lab and ingestion jobs in one SQLite WAL ledger.
ADR-0059 separates two local roles: the CPU editor runs music generation,
analysis, searches, next-scene work, transition renders, previews and export; the ingestion/GPU role
runs ingestion and temporal backfill. Each role
claims FIFO work transactionally; library maintenance follows its foreground jobs.
Lifetime shared legacy-lock ownership and exclusive role locks prevent duplicate
workers or a serial worker colliding with separate roles. Ingestion retains its
isolated low-priority child and global ingest lock. API restart does not lose
jobs. Only the restarting role's abandoned jobs become **interrupted**, with no
automatic replay. An ingest that fails for lack of memory (commit limit, allocator
failure) returns to its queue position and the ingest worker backs off before the
next claim, at most three times per job. Cancellation stops rendering subprocesses and is checked
between model stages; an already-issued hosted call may complete and be cached
but cannot apply a cancelled job's revision. Heartbeats expose activity and
graceful stop requests; file locks, not heartbeat expiry, own execution. CLI
`--status`/`--stop` and read-only `/lab/workers` make queue ownership visible.
This is a local boundary, not distributed scheduling or automatic GPU eviction.

Development can run `python -m pipeline.lab.worker --reload` (ADR-0055). A
launcher starts one fresh process per role. Runtime Python source
changes are checked before job claims; an active job finishes before its
process releases its role locks and is replaced independently. The launcher preserves
configuration arguments, observes startup failures without a retry loop, and
does not replay failed/interrupted jobs. Tests, assets and configuration files
do not trigger reload. This mode is incompatible with `--once`; normal worker
startup without `--reload` also starts both roles. Explicit `--role` selects one;
`--role all` and unqualified `--once` retain serial compatibility.

Editor local PE/Qwen inference uses CPU with the same configured vector profiles;
Beat This uses CPU. Search/planning jobs pin a read-only set of table versions and
semantic/framing readiness manifests under a short publication lock. Inference
and hosted calls hold no publication lock. The process retains its last complete
snapshot during ongoing publication; a fresh process without one waits at most
60 seconds, with progress/cancellation, before reporting incomplete readiness.
Final source checks use the current canonical index. The snapshot freezes index
rows and readiness, not arbitrary derived image files overwritten by legacy
reingestion. API search retains its independent model runtime and uses the same
read-only snapshot primitive for one whole request, including nested recipe
clauses. It pins the latest complete generation: while a writer holds the lock,
or units are published ahead of their semantic text features, a request reuses
the process's last complete snapshot without waiting. A fresh API without one
returns retryable HTTP 503 under the lock and otherwise serves the incomplete
library as published, never retaining it. Capture does not make a multi-table
publication crash-atomic. Request-local query vectors and metadata hydration
are shared within a 4 MiB budget, with stage timings, never across requests.
Managed scalar lookups preserve still-valid feature coverage across controlled
index-only version changes; stale evidence is never certified by reindexing.
ADR-0083 requires complete filtering before bounded compound scalar reads.
`pipeline.index.reads` leaves the engine scan unlimited and streams fully
filtered Arrow batches, stopping and closing after the requested matches.
Ordering consumers retain only their bounded selection while inspecting the
stream. This avoids LanceDB 0.33's indexed scan limit preceding residual
predicates. Vector and full-text ranked queries keep their existing policies.
Memory and returned rows are bounded; candidate scanning time need not be.

ADR-0063 warms the editor's configured PE/Qwen models during idle queue time.
Readiness checks acquire the publication guard with a 0.1-second acquisition
timeout; model loading/inference releases that guard and keeps the existing CPU
policy and vector profiles. Each model is attempted once per process, with
30-second readiness retries for empty or incomplete libraries. Queue, stop and
reload checks precede each model; an active load finishes before yielding.
Failures warn without claiming jobs or applying revisions. `--once` skips this
preparation. Text-only editorial searches also skip offering unused source
references; recipes containing a source clause retain their current authority
and staleness validation.

### AI Music Video

#### User direction and workspace views (ADR-0064)

One optional persisted `editor_direction` object owns current creative input.
Its global `instruction` accepts at most 24,000 characters. Its `ranges` accepts
at most 32 `{id, start, end, instruction}` entries with unique IDs, finite
positive in-track spans and at most 2,000 characters per instruction. Ranges
cannot overlap; touching endpoints are allowed. These source-track timestamps
remain stable when the selected passage changes. Explicit track import clears
ranges but keeps global direction. A full-document Save validates and preserves
the supplied song and ranges, including a snapshot restored through Undo;
historical revision restoration also keeps that revision's song and ranges
together. Existing project JSON/revision storage holds the field without a new
table or schema migration.

An absent/null object uses the legacy brief and user-authored visual arc/motifs
as fallback. A present empty object is an explicit clearing and suppresses
that fallback. `visual_plan` remains derived AI output or legacy user context;
an old user plan cannot compete with canonical direction. The shared
`editorial_context` helper emits `scoped-editor-direction-v1`, including global
instruction and only nonblank ranges overlapping its request passage. It clips
these ranges in model input without mutating their saved extents. Timing,
direction planning, source selection, long-form batch context and optional
footage review consume this same requested context. Range instruction is more
specific inside its span, including with lyric treatment set to ignore.
Boundaries do not require a cut; instructions are neither heard music nor
verified source evidence. Generated proposals remain subordinate to current
user input. Changed direction participates in editorial request/cache identities.

`listening_evidence` separately whitelists `music-listening-input-v1`: audio
track/passage, beat/downbeat estimates, passage RMS and user-supplied song
notes/lyric meaning. New listening requests/cache identities exclude editor
direction, legacy brief, visual plans, pacing and lyric-treatment preferences,
manual markers, per-slot measurements, feedback and previous output. Editing
these creative inputs does not require re-listening. Existing valid same-scope
analysis retains its provenance and remains reusable; the listener's bounded
output schema is unchanged. Supplied song context is still unverified user
context rather than a transcript or something the model claims to have heard.

The two views share one canonical document and local Undo stack. AI direction
offers optional waveform range selection/movement/resizing, direct global
instruction, pace, lyric treatment, film scope and optional song notes/cues.
These controls only edit input. Its **Generate edit / Regenerate edit** explicitly
saves current input and queues `generate.mode=regenerate`; placed locks block
whole regeneration. A newly applied successful result can open Edit after its
revision is accepted; failure, cancellation or an unapplied result does not.
Edit's **Fill gaps** uses `generate.mode=fill` with all current cuts and placed
scenes retained, including untouched starter placeholders. It does not invoke
whole-passage timing or source-aware cut adjustment. Candidate-only scene search
and explicit placement stay intact.
Timed directions do not implement selected-range regeneration.

New work opens AI direction; an existing arrangement opens Edit, unless
`view=ai|edit` explicitly selects a view. Switching writes no project revision,
does not dirty input and cannot start generation. Both views remain mounted;
inactive content is hidden/inert, its media and shortcuts are suspended and
gestures end. Hidden zero-width layout/scroll events cannot discard timeline
zoom, waveform width or its previous horizontal position. In Edit,
Delete/Backspace clears only the explicitly selected unlocked scene into a
placeholder; an active cut instead retains its join behavior. Text entry,
dialogs/popovers, repeated keys and disabled editing are guarded.

`useLabProject.change(update, group?)` supports one Undo entry per consecutive
focused-field edit or drag. `endChange`, Save, job starts, tab/modal boundaries,
Undo and accepted project/track/revision changes close the group. Ungrouped
callers retain one entry per change. Canonical document/history refs advance
synchronously so same-event Save, generation and Undo use the latest value.
Shared project controls and progress remain visible from either view. This
adds no search adapter, model stage or assembly strategy. ADR-0065 permits only
the private discovery/assembly comparison described below; production integration
and existing perception gates remain deferred.

#### Next-scene suggestions and pair audition (ADR-0036)

The `next-scene` job freezes an anchor and its immediately following position.
It returns up to three immutable choices for actual A-to-B playback with music,
without committing an edit. It reuses the capability-aware search adapter,
scoped musical evidence and configured audio/text providers. At most three
recipes retrieve 48 rows each, with 24 pooled legal offers for selection. Audio
interpretation is reused or cached privately; this job never invokes analysis
in a way that rewrites the timeline. Hosted attempts remain bounded and explicit.

Fixed timing is the default. Explicit Flexible cut allows only the shared cut
to move up to two seconds, narrowed by available anchor handles and candidate
duration, when the anchor is unlocked and the following position is empty.
The anchor's source-in, pair outer boundaries and all outside edits remain fixed.
A locked following clip is never replaced. Candidate eligibility uses the full
permitted cut interval; exact chosen windows must fit server-resolved offered
unit bounds as well as the film. Normal Generate/fill cuts remain authoritative.
Selection receives a bounded list of exact cut options with precomputed incoming
durations and source-in bounds, including the feasible original cut and nearby
measured guides. It must copy an offered time; user adjustment can use the full
permitted frame grid. An existing non-grid cut may remain unchanged. The selector
may abstain instead of forcing a weak scene relationship.

Finding and rendering save results in the job ledger. Applying revalidates the
original revision, source authority, locks and exact previewed source windows,
then writes one normal revision. Modified source-in/cut choices require a
completed matching `next-scene-preview` before Apply. Saved preview assets remain
available, but stale choices cannot overwrite a changed edit. Client dirty-state/race guards
preserve local work. Changed ranges invalidate obsolete reference/evidence and
timing-dependent metadata while retaining user-written direction.

ADR-0037 adds manual incoming framing to the same adjustment boundary. The
optional normalized `crop` field preserves framing when omitted and resets it
when explicitly null. The preview's local draft holds source-in, cut and crop;
dragging does not enqueue work. An explicit preview job renders their combined
result, and Apply requires exactly that completed clip/manifest proof. The
anchor's framing and all outside edits stay fixed. Model choices cannot introduce
crops. Changed framing clears obsolete visual references, inspection and resolved
search proof and flags the incoming direction for review. Subsequent planning
does not use a cropped clip's uncropped indexed frame as Look/Framing evidence.
No new renderer, database table, model call or automatic crop search is added.
The same source control is reused by ordinary footage review, preserving its
existing manual project-edit/Undo lifecycle. Local source and crop drafts apply
only on Use this footage and are discarded on Cancel; locked clips remain
read-only. Browsing beyond the supplied source window requires a verified film
duration. Manual source or framing changes clear reused visual search evidence.

The excerpt renderer shares full-edit clip rendering and uses global cumulative
frame boundaries, the corresponding song offset and original passage-relative
fade and independent source dialogue mix (ADR-0077). Its profile is
`next-scene-shared-voice-global-frame-excerpt-v3`. Full music rendering now
uses `decoded-reel-shared-voice-mix-v6`, separates
source decode duration from output frame count, and checks the decoded count.
Every reel/pair manifest includes the `display_profile` component
`square-pixel-display-aspect-fit-v1`, preserving non-square pixel display
proportions and invalidating incompatible earlier manifests.

Optional next-scene sampled-frame inspection is a Lab comparison, off by default. It
provides timestamped derived JPEGs for at most six candidate windows plus the
anchor to the configured OpenAI planner in one selection request. The image
transport is separately versioned and records content hashes without media
payloads in receipts. Sampling uses
`next-scene-three-pts-display-cropped-jpeg640-v2`, preserving decoded PTS and
normalizing display proportions before crop and JPEG encoding. This is sparse
visual evidence, not verified continuous
motion or an activated Match Cuts profile. Unsupported providers and failed
inspection report an error rather than silently substituting text selection.

The Library exposes one optional instruction, compact alternatives and the
same monitor for music-backed audition, with clear scope and timing changes.
The user applies a choice explicitly and can restore it with one Undo. Human
creative acceptance remains open; a formal labeling campaign is not required
to use or polish this workflow. Source-bound and playback checks cannot certify
artistic quality. No learning pipeline or new retrieval representation is added.

#### Editor harness v2 (ADR-0096, ADR-0103, ADR-0104, ADR-0105)

`lab.harness: v2` (the default; `v1` keeps the earlier editor) replaces
whole-edit regeneration with `pipeline.lab.harness`. Language models plan
meaning; the edit itself is measured and optimized.

- **Music map** (`music-map-v1`): Beat This! beats and downbeats, a band-wise
  spectral-flux onset envelope, accents (peaks at least 100 ms apart, marked
  on-beat within 60 ms), per-beat loudness percentiles, and the listening
  sections snapped to downbeats. Span intensity blends the listening section's
  energy (65%) with relative loudness (35%).
- **Song profile and treatment** (ADR-0105): one cached request identifies the
  song from its track name and records what is known about it (genre, scene,
  sound, how its lyrics are read, its shape, its uses), beside the whole track's
  measured loudness. A second request chooses this edit's treatment: idea,
  pace and its shape, footage fame and look, match cuts and cutting, impact,
  what to avoid. The direction comes first. With `planner_settings.auto` the
  treatment also sets pace, footage and match cuts for the run. The listening
  pass still does not identify songs.
- **Concept** (`harness-concept-v7`): one cached planner request turns the scoped
  editor direction, song meaning, profile, treatment and sections into one act
  per section: intent,
  one to four search-v2 queries, a fame target (anchor, fresh, any) and a pace.
  Sections the plan skips keep the listening suggestion.
  - Pacing is a shape (ADR-0103, ADR-0104). The pacing preference is every
    section's default; a section may be faster, but at most one step slower.
  - An act may carry up to four moves. A **hold** is one shot across the move,
    split in two (never under 1.5 s) only when nothing covers it. A **flash**
    (a burst of short shots) is placed only where the direction asks.
  - For an edit scoped to at most three films, the planner also sees those
    films' key moments and hidden gems (the catalog) and builds the arc on
    them.
  - Move times are seconds from the section's start, snapped to a beat or a
    strong off-beat accent within half a beat. Overlapping or too-short moves
    are dropped. Gaps under a beat join the move, and so do gaps beside a hold
    shorter than the section's shortest shot.
  - Each section shows the planner its measured shape: strongest accents, rises
    (entrances out of quiet first), quietest spans and loudness per second.
  - Fame targets never contradict `planner_settings.footage`: `famous` allows no
    fresh act and `gems` no anchor act.

`planner_settings.footage` (`balanced` by default, or `famous`, `gems`; the UI's
Footage control) is also the ranking preset of every editor search in v1 and v2.
- **Pools**: each act's queries run through `search` with the preset matching the
  fame target. Candidates carry compiled shot evidence and a normalized image
  embedding; the previous edit's shots are excluded. Shots that would read
  wrong under music never enter a pool:
  - recognized on-screen text that reads as a caption (subtitles, credits,
    title cards, multi-word signs; short signs stay);
  - units the understanding pass describes as a dissolve or superimposition
    that the cut detector did not split.
- **Casting** (`harness-cast-v1`): one cached planner request reads each act's
  top 16 candidates (plus the catalog) and casts the shots that carry the act,
  in order, with the one that lands its biggest musical moment. A shot is cast
  once. Fill gaps is not cast.
- **Assembly** (`beat-lattice-assembly-v3`): a deterministic lattice beam search
  (8 states per grid point) over frame-snapped beats. Kinetic and rapid acts also
  offer half-beats and strong accents as cut points. Each move is its own act
  with fixed bounds, unscaled by intensity or critique.
  - A flash steps through a steady pulse: one shot per beat subdivision, the
    one nearest 0.25 s. Shots that look alike score higher inside it.
  - Cast shots score a bonus, plus more for the act's peak, and keep their cast
    order within the act.
  - Screen time carries relevance, motion versus intensity, craft and the fame
    target.
  - Each shot adds its peak-on-accent alignment and a log-duration penalty
    around its act's target (the four paces scale theirs by intensity).
  - Transitions add eye-trace and screen-direction continuity, and penalize the
    same scene, the same film back to back and jump cuts. With a moment index,
    the eye-trace term becomes a measured match between the actual cut frames,
    weighted by `planner_settings.match_cuts` (ADR-0099).
  - Variety costs cover image similarity to the last six shots, reuse of a
    visual cluster (spherical k-means on the pool) anywhere in the edit, and
    growing film reuse.
  - Windows stay inside the shot's focus span (the picture its evidence
    describes, never across a dissolve; ADR-0098), never straddle hidden cuts
    and never overlap near-black stretches (`dark_spans`: sampled luma below
    0.03 for at least 0.4 s, for example fades). Locked shots are fixed spans. Spans only end where the remainder
    before the next hard boundary is empty or at least the pace minimum.
- **Review** (`harness-sequence-review-v1`): one cached planner request sees each
  slot with up to four alternatives already placed on its span. It may swap a
  shot for meaning, rhymes, fit or variety. Swaps to shots used elsewhere are
  skipped. Timing never changes.
- **Critique** (`harness-critique-v1`, off unless `lab.harness_critique: true`):
  the reviewed edit is rendered as a preview. Gemini watches it with its audio at
  4 fps and returns up to 16 timestamped issues, which drive one re-assembly and
  review:
  - weak, repetitive or off-direction shots are banned;
  - "too fast" or "too slow" scales the pace of the overlapping acts by 1.35;
  - off-beat and continuity flags are recorded only.

  The upload is deleted after the request.

The result is an ordinary document:
- AI slot directions with resolved text searches and up to six alternatives;
- earlier clips kept in the bin;
- a `timing_plan` receipt in the v1 shape, whose notes are per-act editorial
  choices;
- `direction_plan` recording the concept, acts, music-map summary and review.

**Fill gaps** under v2 keeps every cut and placed shot:
- placed shots are fixed neighbours, built from their index rows, or neutral
  when unindexed;
- the edit's v2 concept is reused when its passage matches;
- user-directed slots get one-slot acts whose pools come from their own queries;
- the lattice runs on the pinned slot boundaries, and `_guard_edit` still
  rejects moved cuts or changed protected fields.

Targeted replacement and next-scene suggestions keep their v1 contracts. `GET /lab/projects/{id}/timeline.otio` exports the saved edit as
OpenTimelineIO (`scene-recall-otio-v1`). It has a video track of source-film
clips and gaps, with frame counts from the renderer's cumulative quantization;
the song passage; and non-overlapping dialogue clips. Crops and mix levels
travel as clip metadata.

#### Music preparation and generation

The workflow handles selected audio passages up to 600 seconds (ADR-0045). FFmpeg
decodes a mono PCM derivative; local waveform and RMS intensity are separate
from a pinned Beat This! 1.1.0 beat/downbeat derivation. Runtime verifies local
checkpoint hashes and never implicitly downloads weights. Timing suggestions
are editable and are not treated as exact musical truth.

ADR-0035 adds a local-only durable `rhythm` stage before creative generation.
Applying a new passage queues beat/RMS preparation and initial empty placeholders;
existing timelines stay authoritative during filling, including untouched starters.
Explicit `replan_timing` rebuilds only
after lock checks and retains old clips in the bin. Versioned timing suggestions
record bar/pulse landmarks and relative-amplitude changes under pacing bounds.
They do not claim phrase recognition. Detector beats are separate from manual
markers and visible by default; missing beats produce no invented grid. Listening
and prompt generation remain separate, optional stages above this local timing.

ADR-0039 marks newly created empty rhythm starters with optional timeline
`provisional_timing` (`local-rhythm-starter-v1` plus a fingerprint of track,
passage and ordered slot IDs/start/end). This metadata remains readable, but
ADR-0064 removes its authority to retime a fill request. Both first **Generate
edit** and subsequent **Regenerate edit** explicitly use whole-edit regeneration
to choose new timing. **Fill gaps** always preserves existing cuts, regardless
of starter status. Manual operations and explicit shot planning/search clear
the marker; successful generation also clears it even when gaps remain.
Cancellation/failure applies no intermediate edit.

ADR-0060 separates musical timing from visual directions. A compact text-model
request considers the whole selected passage and returns its ordered cut frames
with bounded musical reasoning and evidence references. It receives current
timestamped listening evidence, measured rhythm/intensity, user musical context
and the selected pace; it does not search for or inspect footage. Patient,
Balanced, Energetic and Rapid are soft preferences for sustained development,
balanced movement, active changes and quick clusters respectively. None sets an
average-duration envelope, minimum count or per-processing-section quota. All
allow meaningful holds and bursts when the musical evidence supports them.
Measured amplitude is not emotion and processing boundaries are not phrases.
Missing or uncertain interpreted events remain unknown; existing broader
interpretation and measured cues can still support explicitly qualified timing.

Before re-listening to an invalid saved aggregate, generation may privately
restore number types lost through browser JSON serialization from its original
interpretation caches. Strict 64-hex cache IDs, matching provenance hashes,
track/passage and full evidence equality are required; only integer/float
representation differences are permitted, never boolean substitutions. Missing
or changed originals use the existing invalid-analysis fallback. This does not
rewrite saved state or change global derivation hashes.

The whole-passage timing response permits 1–300 positions, bounded by available
output frames. Its compact notes are separate from per-shot visual directions.
Existing audio editorial moments remain readable; the timing request omits their
previously proposed cuts and scene searches rather than inheriting their count.
Local validation requires integer, strictly increasing passage-relative output
frames, at least one output frame of source time per position and the exact
original passage endpoint. Invalid output is never sorted, clamped or replayed.
Uniform timing is diagnostic, not an aesthetic rejection rule. Whole-passage
planning applies to explicit regeneration, used by both Generate edit and
Regenerate edit. Fill gaps and per-shot work keep existing timing.

`direction_plan.timing_plan` and the generation result's `timing_plan` retain the
`music-led-passage-timing-v1` contract and artifact identity, track/passage/fps,
proposed `end_frames`, up to 32 notes (`start_frame`, `end_frame`, `reason`,
`evidence_ids`) and cache-reuse status.
Nominal and final duration diagnostics plus `final_end_frames` distinguish the
musical proposal from later source-fitting adjustments. Musical reasons are
recorded model judgments, not verified accents, emotional readings or proof of
creative quality. Older saved plans remain readable without this optional field.

ADR-0041 keeps the resulting whole-edit regeneration intentions provisional until
source selection; ADR-0064 excludes fill requests from this source-fitting step.
The private `bounded-source-aware-first-edit-v1` scope offers finite interior
boundaries within non-overlapping bands at most two seconds from each musical
proposal, including nearby measured guides. Intent count/order stays fixed.
Candidates can fit any offered local duration. ADR-0058's provisional response
`scoped-source-timing-preferences-v1` requires one entry per shot: an offered source
alias with normalized position in its legal trim interval, and an offered
`preferred_end_frame`. A bounded deterministic path solver uses only offered
boundaries to fit all chosen sources, minimizing preferred-cut displacement,
then changed-cut count, then earlier boundaries. Only afterward are source starts
resolved for the actual durations. Record preferred/applied cuts and report
adjustments; impossible combinations fail without source substitution, invented
cuts or forced gaps. Explicit abstention uses `source: null`; empty offers permit
only abstention. Fixed slots retain ADR-0044's `scoped-scene-choices-v3`, with an
offered source alias bound to an absolute source start and no cut preference.
The server resolves aliases against each shot's own
candidates, and checks provider schema limits before sending. Validate offered identities/cuts, exact passage coverage,
source bounds and passage-relative frame timing, retaining the original audio
endpoint. The result remains the ordinary committed timeline. Existing/manual
arrangements retain fixed timing. This source-fitting step adds no hosted repair
stage or general action-aware retiming.

ADR-0061 adds a separate optional post-selection inspection slice, controlled by
`lab.footage_inspection` and off by default. It runs inside the same private
Generate proposal before atomic publication. The selector's candidate ledger
can nominate `action_timing` or `visual_fit` uncertainty; `none` is also valid.
Across the entire job, select at most two eligible unlocked positions, prioritizing
action-dependent claims and then timeline order. Record all eligible flagged IDs
separately from the bounded inspected/reviewed scope. User-owned directions and
locks are protected; a flag is not evidence that the selection is wrong.

For each target inspect its selected source and at most one already-offered,
distinct legal alternative. `source-window-observations-v1` retains neutral
before/after observations with sample evidence and uncertainty, independent of
music. `source-window-2fps-endpoints-pts-jpeg640-v1` samples no more than eight
seconds at approximately two frames per second plus endpoints, capped at 17
actual decoded presentation-timestamp frames. Display proportions and requested
crops are preserved. Cache dependencies include authoritative source fingerprint,
unit/window/crop, sampling and provider/model/prompt identity; source authority
is checked before reuse. Raw films and prior derivations remain unchanged.
At most four observation requests on cache misses and one joint text review are
allowed per job, with no retry, provider fallback or repeated selection loop.

The review receives sampled observations, musical evidence, target intentions,
offered trim options and caption-only neighbors. It may retain the baseline,
choose a server-offered trim or explain a gap when the baseline is contradicted
and no supported alternative fits. Uncertainty preserves the baseline. An
inspection-rejected targeted replacement retains the previously placed clip
with an explicit uninspected label under the existing replacement lifecycle;
the earlier clip is not retroactively verified. An
observed-action option includes its sampled before/after bracket and final
evidence frame; it does not establish exact action onset/completion. Sparse
stills do not verify continuous motion, exact object positions or narrative
continuity, and a long clip can extend beyond the inspected window.

Reuse ADR-0058's deterministic solver for the smallest movement within original
offered cuts. Keep group endpoints, fixed/manual cuts, protected neighbors,
unselected source starts, legal durations and complete passage coverage. Check
source reuse and update affected search evidence/alternatives. No new cuts,
merges, query repair, reordering or unoffered sources are available. Individually
valid choices that cannot fit together preserve supported baselines and explain
gaps for contradicted ones. Authority violations fail the job; optional inspection
unavailability retains provisional choices with explicit incomplete diagnostics.
Cancellation, stale revisions and final source/lock checks remain unchanged.
Private `<job-id>-inspection-input.json` receipts preserve the provisional
document and selector contexts; public Details exposes bounded recorded scope,
observations, cache use, changes and uncertainty without paths or raw snapshots.

The standalone frozen comparison runner defaults to dry/read-only preparation.
Explicit execution may write model/sample caches and receipts but never saved
projects, jobs or new listening evidence. Inspection off/on replays one frozen
provisional selection. Known-failure coverage distinguishes automatic flags from
deliberately injected fixture hints; unlabelled positions are not negative labels.
Timing beat ablation uses internal `beat_guides` keywords, default true, to omit
measured beats/downbeats and derived markers from the timing request and extra
pulse choices from source-fitting offers. User markers, lyrics, RMS and listening
observations remain. Provenance is whitelisted metadata plus dependency digests,
so nested old requests cannot replay removed pulse arrays. Saved provenance stays
intact and treatment identity cannot collide with the default cache. The runner
records calls, cache reuse, latency, source/timing changes and gaps; human played
preference remains separate. See the
[comparison workflow](experiments/music-edit-comparisons.md).

ADR-0065 adds a private `assembly` comparison using
`broad-assembly-discovery-intent-v1`, `bounded-assembly-discovery-ledger-v1`,
`frozen-joint-sequence-v1` and `frozen-discovery-assembly-comparison-v1`.
The dry default validates a frozen passage of at most 45 seconds with 1–64
baseline positions, current listening evidence and no placed locks. Explicit
execution uses the CPU editor profile and a pinned index snapshot, verifies
registered music bytes, and permits at most four hosted stage requests without
retries. It creates no projects, jobs, listening or new inspection evidence;
the optional context pilot is disabled for this comparison.

One request proposes 2–4 soft contiguous editorial regions and at most six
complementary recipes using existing verified search adapters. Up to 48 returned
rows per recipe are retained with original query ranking, match evidence and
exclusion reasons. Canonical source identities merge without film quotas, new
ranking weights or relaxed gates. Round-robin selection across query ranks
exposes at most 48 eligible sources in a separate model catalog. The complete
retrieved ledger is not the model's immediate context and not a library inventory.

Compare fixed-slot selection using the shared pool and ordinary per-slot duration
eligibility with joint count/order/source/timing selection from the same pool.
`assembly-music-without-previous-cuts-v1` supplies the same frozen observations,
measured guides, meaning and user direction while omitting prior AI shot imagery,
cut markers, per-slot RMS and feedback. The baseline retains its slot directions;
it is a controlled replay, not the historical request. Joint output chooses at
most 64 sourced shots using offered source aliases, normalized legal source
positions, strictly increasing passage-relative integer end frames and concise
reasons. Any legal interior frame is available; music and regional boundaries
are soft cues. Source bounds, exact passage coverage and existing source-reuse
guards remain authoritative. Invalid output fails without source substitution,
cut sorting or automatic repair. Fixed-baseline abstention remains explicit.

The joint response may nominate at most two targeted discovery needs. One
expansion can execute further recipes or revisit retained candidates through
an original recipe/shared exact executable clause. Keep the original catalog
and append at most 24 related candidates, preserving aliases. Record request
versus execution counts; at most eight recipes are requested in total. No need,
or no additional eligible source, skips reassembly without a model call. A
failed bounded search never establishes that the library lacks suitable footage.

Checkpoint query ledgers and preserve catalogs, requests/schemas/receipts,
responses, source choices, reasons and valid ordinary project documents privately.
The existing renderer produces 720p comparison media inside the new run output;
failures or cancellation preserve earlier artifacts. Record available usage,
latency, gaps and source changes without inferring creative quality. Human played
preference and production promotion remain explicit evaluation gates. The first
fixture is Wish revision 4, 3.68–33.68 source-track seconds; Starjunk is a later
separately bounded follow-up. Ordinary main search and generation remain unchanged.

The selector's compact input keeps source annotations once under request-local
aliases, film references and slot-specific evidence. Exact duplicate text becomes
an explicit reference; internal ranking diagnostics and request plumbing stay out
of the model input. Distinct indexed context, song evidence, intent and neighboring
footage remain. Retrieval and stored search evidence do not change. A compact
offer manifest beside each hosted receipt preserves the canonical candidate and alias maps,
source ranges, timing options and input/schema hashes even when selection fails.
Timing, choice validation and prompt projection live in separate focused modules.

ADR-0045 separates project capacity from model-request capacity: up to 600 seconds,
300 timeline positions and 600 saved clips. ADR-0060 plans musical timing before
partitioning text work. Consecutive planned shots are grouped into batches of at
most 32, preferring spans no longer than 90 seconds. A single longer hold gets
its own batch, including for fixed-timing fills. Batching never inserts a cut,
splits a planned hold or allocates a shot-count quota; the 300-position capacity
applies to every pace. Local rhythm scaffolds remain editable heuristics and can
use the larger timeline capacity across the complete passage.

Hosted listening remains separately bounded to original source-track sections
no longer than 90 seconds. Local rhythm spans the selection. The aggregate
preserves every section's validated evidence and provenance rather than
truncating it to earlier short-edit limits; a deterministic scoped projection
supplies each text batch without another listening call. These listening sections
and text batches are resource boundaries, not detected musical changes.

Visual planning and selection use the established musical timing, local detail,
a shared song overview, neighboring context, one visual arc and previously chosen
footage. Source fitting remains bounded within each provisional batch; it cannot
merge musical intentions into a new action-aware arrangement. Used-footage checks
cross batch boundaries. Existing cuts, directions and locked or placed clips stay
authoritative for fills and targeted changes. A single frozen job assembles the
complete proposal and commits once; failure, cancellation or a stale revision
cannot save partial results. Progress distinguishes listening, whole-passage
timing, visual planning, retrieval and selection, with batch/operation details and
complete-edit diagnostics. There are no child projects, new vector indexes or
changes to normal search.

ADR-0050's Rapid preset and optional evidence inspector remain available;
ADR-0060 replaces Rapid's 25.6-second text scopes and 288-shot automatic quota.
Changing pace alone preserves existing cuts; explicit regeneration rebuilds them.
Local starter cuts resume at real landmarks after beat gaps, and over-cap proposals
retain landmarks distributed across the entire passage rather than exhausting
capacity near the start.

The optional **Why this shot** inspector displays current neighbors, recorded
intent/selection notes and separate source evidence; it does not generate a new
rationale. Indexed thumbnails are sample frames, not cut-boundary verification.
The preview uses cached canonical film titles. Client waveform peaks retain 5ms
detail from all decoded channels; rendering aggregates only the visible interval
at bounded pixel resolution. Actual playback pages the timeline viewport when
the playhead exits; paused seeks and active drags do not.

The editor labels untouched layouts as starters. **Cut tools → Suggest cuts**
explicitly rebuilds empty positions using music analysis, with retained source
selections, lock checks and Undo. The listening contract
`phrase-first-music-observations-v8` separates heard vocal meaning from musical
atmosphere, retaining approximate observations and suggested editorial moments.
The existing audio request returns bounded meaning, themes, heard paraphrases and
uncertainty. Cues are shifted to source time and validated; unclear/absent vocals
cannot establish lyric themes. Shared evidence contract `music-led-evidence-and-preferences-v3`
only supplies current, provenance-scoped meaning. Legacy meaning remains unknown.
Music analysis exposes this interpretation and its caveats; user notes are optional.
This does not establish
precise vocal timing or action completion; source-aware selection still uses sparse
indexed evidence, not candidate video.

A separately configured audio provider listens to each bounded audio section plus
the ADR-0064 listening context and returns at most eight chronological emotional
segments and search intents per section. Full-length aggregation retains the validated
segments from every section. Each segment has an editable query and supported text facet:
`all`, `scene`, `words`, `look` or `mood`; old segments default to `all`.
Framing remains reference-only and does not become a textual music search
capability. The default is OpenAI `gpt-audio-1.5` through Chat Completions:
the request contains actual PCM audio and asks for text output. It never
substitutes a transcription-only or text-only model for listening. Gemini
remains an explicitly selected compatibility provider, including older
configurations that name a Gemini model. Film annotation configuration is
independent; provider availability never triggers implicit fallback.

The versioned interpretation also requires 1–32 contiguous editorial moments
per listening section in each audio response. Full-length aggregation retains
all section moments. Each has start/end, a distinct-purpose query/facet,
an audible cue, editorial purpose and timing note. These are proposed edit
moments, not precise beat-detector measurements. Musical phrases, pauses,
accents and changes in texture can justify long holds or short cuts; neither
fixed duration buckets nor arbitrary variation define the plan. Legacy
interpretations without moments remain readable. All moments use the same
validated passage coverage and source-track time base as sections.

OpenAI audio responses are prompted as JSON and checked locally against the
strict interpretation schema and complete ordered passage coverage. The
implementation does not claim that this model supports Structured Outputs.
Both providers use the same source/time validation before any generated
revision applies. Interpretation caches include original hash, passage, audio
recipe, provider, hosted adapter contract, requested model, schema, prompt
version, settings and the versioned listening input. Editor direction and pace
are excluded from new listening identities. A current valid saved interpretation
is reusable across creative-input regeneration; editorial requests own those
changed preferences. Explicit listening still follows its input-scoped cache and request
controls. The requested hosted model ID is retained; no
provider-resolved immutable revision is claimed. Hosted response storage and
transport retries are disabled.

Music jobs persist descriptive progress before preparation, rhythm analysis,
listening, validation, retrieval and planning. OpenAI output streams provide a
writing stage and periodic activity updates without exposing incomplete JSON
as an editable interpretation or inventing percent completion. Hosted requests
have bounded timeouts; cancellation is checked at progress boundaries, while
an already-issued call may wait for output or timeout before stopping. Failures
leave a receipt and a readable job error, never a silent provider switch or
automatic replay. ADR-0028 records this provider and progress boundary.

Music editing uses an explicit `music_timeline`, scoped to the track and
passage, with at most 300 contiguous slots in source-track seconds. Local
rhythm can prepare it before hosted audio analysis.
Each slot has a stable ID, start/end, section index, optional selected clip ID,
editorial reason, search error and at most six source-backed alternatives.
An optional per-slot `direction` stores query, text facet, purpose, audible cue
and timing note, with an optional executable search plan described below;
`direction_source` distinguishes generated and user-written
intentions. Missing directions inherit the legacy section query. A direction
edit affects only that slot, and retrieval uses that effective slot direction.
An optional `needs_direction` flag defaults false. Splitting, rolling a cut or
joining marks only the affected slots for prompt review, preserving their
previous intent as context. Timing edits never invoke a hosted provider.
The existing clip list is a selection bin; slots define playback order and
empty slots retain their time. Validate full ordered passage coverage, a
minimum output frame per slot, unique IDs, section references, matching
track/passage and selected clip duration. A manual timeline may exist before
analysis. Old documents read with a null timeline and migrate only on an
explicit timing save or music job, preserving source selections.

Editorial moment boundaries suggest initial cuts; explicit user markers create
exact initial boundaries. Existing cut positions are authoritative during filling
and new beat detection, including untouched starters under ADR-0064. Explicit
Generate/Regenerate chooses new whole-passage timing. Explicit analysis
reinterprets the current music/provider inputs and refreshes generated
directions while preserving existing timing, clips and user-written slot
directions; identical inputs may reuse the interpretation cache.
Drafting requires a current same-track/passage interpretation and never
implicitly listens again.
Changing the selected track/passage invalidates its old timeline context.

An explicit analyze job option `replan_timing: true` replaces the cut plan with
empty slots derived from the musical moments. It is frozen with the job and
rejected when a placed clip is locked. Existing clips remain unplaced in the
selection bin, and revision history/Undo preserves the full prior arrangement.
No other job can request it. Manual cut movement, splitting and joining remain
available without hosted calls; joining extends real source footage and keeps
the removed right clip unplaced. Beat snapping is optional. This is separate
from watching candidate action to optimize a cut instant, which is not added.

An explicit text-only `plan` job generates directions for existing exact slots.
Its optional `slot_ids` are validated and frozen at enqueue, including resolved
default targets. Defaults select empty positions without user-written directions;
the editor explicitly prioritizes timing-affected generated directions. Explicit
targets may rewrite a user direction. Reject unknown/repeated IDs, locked placed
clips and more than 32 targets in a standalone plan request before hosted work.
It changes only those slots'
directions, provenance and stale suggestion metadata, keeping all cut times,
source selections, analysis and unrequested directions. `direction_plan` records
the last plan's provider/model, contract, input hash and targets independently of
audio interpretation. Track/passage changes invalidate this metadata.

One bounded call to the existing text planner uses the full timeline, creative
brief, current musical evidence and available captions for chosen sources. Ask
for purposeful story progression, framing/shot-scale relationships, visual motifs
and feasible action at the exact fixed duration. Distinguish creative intent from
observed evidence; do not invent audible events or verified visual continuity.
Every target must return exactly one validated `MusicDirection`. Validated outputs
have a separate context/model/schema/settings-scoped cache. No implicit listening,
scene retrieval, video analysis or GPU lock is needed. User-direction-only planning is
permitted before listening and leaves `analysis` null; automatic drafting still
requires current audio interpretation. Manual candidate-only search can use a
slot's own valid direction without listening or creating an interpretation.
Cancellation, stale-revision, locked-source and
single-attempt provider guards continue to apply. ADR-0031 records this boundary.

ADR-0032 extends these stages with one versioned, provenance-checked musical
evidence packet. Prompt planning and selection share measured timing, relative
RMS summaries (at most 24 windows plus per-slot summaries), supplied lyrical
context and explicit planner preferences. ADR-0064 projects a separate
listening-only whitelist, excluding previous output and editing preferences.
New audio results add up to 32 sparse
observations per listening section, retaining every section's observations
in a full-length aggregate. Each has start/end, kind, label and model-reported confidence, separately
scoped as `ai-observed`. They are approximate observations, not verified event
detections or required cuts. Legacy results without events remain readable.

Durable `planner_settings` control pacing and lyric treatment; `song_context`
stores track-scoped notes and up to 80 user-supplied lyric/paraphrase spans with
meaning. Validate finite in-track ranges and unique IDs; only overlapping lines
condition planning. `editor_direction` owns global and time-scoped creative
instructions under ADR-0064. `visual_plan` stores generated arc/motifs or legacy
user context; a legacy user plan applies only without canonical direction.
Direction planning can return a generated sequence plan alongside exact target
directions while honoring that authority. Per-slot feedback
requests less literal imagery, more contrast, different energy or more readable
action. None of these preferences moves existing cuts. Inputs participate in
editorial cache identities. Audio still proposes creative moments in the listening call;
its recorded provenance is preserved when current scoped listening is reused.
ADR-0060 separates a new timing decision from that saved evidence, so a pace-only
rebuild does not discard it or require another listen. Changed or invalid music
scope still requires current evidence before automatic planning.

AI direction preferences apply as local undoable input without hosted work or
changes to existing shot directions. Regeneration applies them to a new edit;
legacy settings APIs remain compatible. A cue lane distinguishes
AI observations from supplied lyrics. Exact source footage can be auditioned and
its source-in adjusted before Apply, within film bounds and at fixed duration;
actual video seeking is not an indexed keyframe claim. No automatic lyric
transcription, structural model, video model or automatic retiming is activated.

The draft stage fills only empty slots by default. Optional `slot_ids` on a
draft job explicitly requests replacement of selected unlocked slots; the IDs
are validated and frozen with its revision snapshot. Keep existing choices
until valid replacements succeed. A section with no fitting candidate retains
its original choice or gap with a readable search error. Other successful
slots can still be filled. Locked generation guards check absolute timeline
positions as well as existing clip content and bin positions.

Each draft invocation, including a footage batch of full-length generation,
makes bounded calls to the existing typed retrieval adapters
(at most 32 distinct slot recipes, at most three clauses each, 48 results each) and passes a finite source
catalog to a second hosted planner. Each requested slot offers at most 24
legal candidates. Its explicit `lab.planner_model` defaults to OpenAI `gpt-5.6-terra`
with text-only Chat Completions, low reasoning effort and strict JSON-schema
output. It omits audio-specific request options and independently records
planner model, adapter contract and settings. Changing the planner does not
invalidate the audio interpretation cache. Under an explicit Gemini provider,
an omitted planner model inherits the selected Gemini music model. This is
deterministic operation routing, never error-triggered provider fallback.
For editing, near-identical visual alternatives remain eligible in broad and
focused recipe retrieval while junk filtering stays active. No new embedding
space is introduced and ordinary search policy is unchanged. Only returned
IDs and legal source trims may become clips. The planner receives the complete
timeline with current selections before and after requested slots, full
section and individual clip intentions, audible cues, timing notes,
beats/downbeats, intensity and scoped editor direction. It must
keep all slot times fixed outside the ADR-0041 private first-edit scope. Six bounded legal alternatives remain available
for explicit switching after each successful fill. The planner sees captions
rather than candidate video, so action and camera continuity remain human
judgments. ADR-0029 records authoritative timing and replacement;
ADR-0030 adds song-specific moments, slot directions and explicit replanning;
ADR-0031 separates coordinated prompt planning from listening and retrieval.

ADR-0033 connects those stages through a shared versioned search capability
description and an atomic default workflow. The catalog reports facet evidence,
accepted text/reference inputs, active index/profile readiness and explicit
limitations. `GET /lab/music/search-capabilities` exposes the same description
without searching or loading models. Unknown readiness is distinct from ready;
profile readiness is not a guarantee of available runtime resources. The editor
uses the existing recipe engine directly through a small adapter. Backend
ranking/evidence improvements propagate without duplicating retrieval code;
new operation types still require explicit validation and quality support.

The text direction planner can return an optional search plan with one to three
unique-facet clauses and up to eight concise requirements needing verification.
Clauses are text or server-offered indexed references. The server binds reference
IDs to selected clip identity/range and an actual indexed unit/frame/timestamp
inside that range. Persist this authority; replacing or trimming the anchor
cannot silently redirect a saved recipe. Missing, changed or unavailable
references yield a readable search error. Planning context explicitly links
offered references to their timeline positions and identifies neighbors with
none; instructions prohibit substituting another neighbor for a requested
missing reference and require explaining that unverified requirement.
No model-provided path, vector,
arbitrary source identity, image upload or motion operation is accepted here.
Audio interpretation retains its existing text-direction schema and cannot
invent executable reference plans.

Old and manually edited directions continue through the single query/facet
path. Combined/reference searches reuse equal rank fusion, film scope, source
exclusions, junk policy and the mandatory Framing candidate gate. They do not
introduce weights, hard metadata predicates, negation or new vector spaces.
Each slot retains its resolved search and query-specific evidence. Shared
canonical source bounds are separate from per-query ranks, matched text/views,
frame identity/time and clause evidence, so another query cannot overwrite the
evidence used for a slot. Known still-image annotations remain approximate;
text selection does not watch candidate video or verify actions, motion or plot.
ADR-0061's optional subsequent inspection only provides sparse observations for
its explicitly recorded target windows.

A `generate` job accepts optional `generate.mode: fill | improve | regenerate`;
default fill and explicit regenerate use no target IDs, while improve requires
exactly one top-level `slot_ids` entry. It freezes the document and validates
ownership and locks. Visual direction/selection batches handle at most 32 target
slots and prefer spans up to 90 seconds; an individual longer hold stays intact
in its own batch within the 300-position timeline limit. Fill reuses a valid same-track/passage interpretation,
otherwise explicitly runs the audio stage as part of the authorized Generate
action. It plans eligible empty positions, preserving custom directions, then
retrieves and selects. Improve replans and replaces its one unlocked target,
using saved feedback and neighboring footage as context. Existing cut positions
and unrequested selections stay authoritative. This does not change standalone
draft jobs' rule against implicit listening.

ADR-0042 adds explicit whole-edit regeneration: reject placed locks, create a
private fresh arrangement from the current track/passage/global settings and user
context. Preserve current same-track/passage listening with its provenance while
clearing old generated directions and selection diagnostics; when listening is
missing or invalid, run the existing bounded audio stage. A pace-only rebuild
therefore reuses scoped evidence. ADR-0060's whole-passage timing stage proposes
new cuts, then bounded visual planning and ADR-0041/0058 source-aware selection
assemble the footage.
Old per-slot directions, cuts and placements are replaced only by the completed
proposal; old clips remain in Saved clips. Retain the 600 saved-clip bound with
an explicit capacity error and instructions to clear unused clips. Unused locked
bin clips remain protected and do not block regeneration. This explicit mode is
the exception to current-cut protection; fill and targeted improve stay unchanged.

Regeneration privately supplies previously placed sources and excludes their units
or overlapping film windows before the final candidate limit. Automatic selection
cannot repeat a source unit or overlap retained/already selected footage. A later
repeat becomes an explained abstention, preserving an original targeted clip when
applicable. Progress and the candidate ledger expose exclusion/repetition counts
and reasons. Manual search and placement continue to allow deliberate reuse. This
does not change main-search results, film metadata or vector spaces. See ADR-0043.

Stages operate on one private proposal with existing cache identities, progress,
cancellation and single-attempt provider boundaries. Only a complete proposal
can apply one final revision; errors and cancellations apply no intermediate
project edits. A late result remains an unapplied proposal. A completely filled
edit is a no-op for default fill and creates no extra revision. One frontend
Undo restores the edit before generation. AI direction exposes Generate edit for
the first edit and Regenerate edit for an existing arrangement; both explicitly
save current input before queuing a whole rebuild. Edit exposes Fill gaps,
retaining current cuts and scenes. Separate listening remains beside the music;
beat detection/cut planning stays beside the timeline. Optional per-clip AI
direction rewriting remains available. ADR-0064 owns the shared view and input
boundary and makes fill timing fixed even for untouched starters.
The individual stage APIs stay compatible. Editing the simple search clears its
obsolete generated recipe; optional Search options exposes the executable clues.

ADR-0034 makes the inspector's selected-shot flow explicit: edit its search,
Find scenes, preview an alternative, then Use scene. A draft request with
`suggest_only: true` requires exactly one unlocked slot and saves at most six
ranked duration-fitting alternatives, resolved search and search error. It reuses
the same retrieval/evidence path but skips hosted planning and selection. Current
clips, timing, directions, feedback, analysis and the placed scene's reason and
evidence remain unchanged. Choosing a candidate is a separate undoable placement.
Optional AI prompt assistance exposes the existing plan job as Rewrite prompt;
the user reviews that output before searching. Generate edit remains automatic,
and the standalone automatic placement stage is labeled Fill gaps. The existing
targeted generation API remains compatible but is not the inspector's action.

The right-hand Scenes panel combines the selected position’s visible search and
saved slot suggestions through the same candidate-only draft adapter. Unused
retained bin clips appear in Saved clips and can be cleared with Undo, preserving
locked and placed clips.
Preview/trim and explicit placement are available before AI planning. Dragging
uses an in-memory source selection, never browser-supplied paths or vectors;
destination duration and lock checks precede placement, and shorter drops retain
the available matched moment. Source-window changes clear obsolete evidence.
The interface exposes listening, musical timing, visual planning, retrieval,
text selection and optional targeted footage review as distinct recorded stages
within the existing Generate flow.

Project lifecycle includes revision-checked `DELETE /lab/projects/{id}` with a
required `base_revision` query parameter. A transaction rejects queued/running
work and removes that project's records, history and jobs. The same transaction
records cleanup tickets for those job IDs. After commit, remove their render
directories (including previews/exports), request receipts/snapshots and Match
thumbnails; files are owned by an exact UUID plus a separator, never a fuzzy
prefix or a path supplied by the browser. Tickets retain the configured assets
root and survive crashes, locked files and configuration changes. Deletion
returns `cleanup_pending` if any ticket remains; filesystem failure must not
turn a committed project deletion into an apparent failed request.

The existing editor worker retries cleanup while idle, at most once every five
minutes, with up to 100 tickets and 100 garbage paths per pass. A dry-run/apply
CLI uses the same policy. It also reclaims terminal-job render intermediates,
orphan UUID artifacts older than 24 hours, and reproducible decoded-audio WAVs
older than 24 hours while no editor work is active or queued. Project render
outputs are a cache of saved revisions: each project keeps its newest completed
`render` job per mode (preview/export), ordered like the editor's job list, and
the render directories of its older completed renders are removed. Failed,
cancelled or unfinished renders never supersede a completed one; job records
stay, and the output endpoint already reports a removed cache (ADR-0100). Maintenance
rechecks the ledger under a write transaction before garbage removal, excluding
worker claim/enqueue races; linked/reparse paths and escapes are refused.
Discovery scans are proportional to the Lab artifact directory size; deletion
batches bound removals, not scan time. New renders discard their own intermediate
clips/concat/partial files in `finally`, after the encoder has stopped.

Other live job outputs/manifests/receipts (including projectless Match and Transitions sessions),
unknown experiment namespaces, shared versioned AI/rhythm/observation/context
evidence, model files, original tracks, film evidence and unrelated ingestion
jobs remain. No broad age-based evidence eviction or per-artifact registry is
introduced. Add new disposable namespaces explicitly to this lifecycle rather
than adding a generic recursive purge. See ADR-0068 and ADR-0100. Named deletion confirmation,
guarded unsaved exits and save-race checks belong to the UI; clean exits can leave
durable jobs running. No project schema bump is needed. See ADR-0035.

Beyond ADR-0061's bounded sampled-window review, broader candidate-video
inspection, general action-aware timing optimization, automatic dialogue selection and Match
Cuts integration remain deferred. Real-song acceptance must
separate retrieval relevance, useful source instants and played transition
quality; successful jobs and legal source ranges do not prove editorial quality.

ADR-0041 permits explicit candidate abstention, with a reason and no invented
source start. A failed replacement retains the existing scene; unfilled slots
remain editable. Bounded offered IDs are hydrated with available indexed metadata,
dialogue and visible text, without claiming temporal or narrative verification.
`analysis.draft` retains contract, per-query counts, offered IDs/ranks/durations,
source/window choices and reasons as diagnostic provenance. Saved alternatives
are refitted to final slot durations. This ledger is not a complete frozen index.
Public job status exposes bounded recorded progress, cancellation state and durable
timestamps; Details can inspect actual provider/model stages and supplied counts
without revealing snapshots or inventing percentage completion. One editor serves
all new work; planner strategy is diagnostic provenance, not a user-facing version
selector. See [ADR-0041](decisions/0041-simple-editor-and-source-aware-first-cuts.md).

The music workspace supports replacing through search or saved alternatives,
source-in adjustment, cut dragging, splitting, clearing, locks, undo and saved
revision restoration. Explicit track replacement starts an empty arrangement;
previous music, placements and locks remain recoverable through revisions.
It exposes source Moment and Crop & zoom controls; clip reordering is not implemented.
Its two source-video decks follow the music clock for immediate whole-sequence
preview. Browser seeks land slightly inside the selected window to avoid
microsecond rounding into a preceding native frame. An incoming deck is shown
only after its seek is decoded; the outgoing deck is retained until then and
is not reused for preload while visible. At a source window's end, its final
legal output frame is held while the music reaches the cut. Empty slots remain
visible at their musical positions. These browser guards do not establish that
an indexed source window contains no internal cuts; source review and rendered
evidence remain separate. One
deterministic manifest drives original-speed decoded/reencoded trims and the
downloadable video.
The output is 24-fps H.264/AAC, 16:9 or 9:16, fitted full picture or uniform
whole-picture crop. ADR-0077 extends music audio with independent original-source
dialogue; ADR-0078 adds shared voice-focused PCM and overlapping audio. Optional
`dialogue_clips` defaults to empty and permits at most 32 independent clips inside
the selected passage. Clips may overlap for ambient handoffs or voice bridges.
Each has unique `id`, indexed
`film_id`, nullable `unit_id` hint, `title`, `text`, source bounds and absolute
song-clock `start`; duration remains `source_end-source_start` at original speed.
Picture cuts and gaps do not interrupt or retime these clips. Each clip stores
`gain_db` (default 0), `music_duck_db` (default -8), and independent linear source
fades (defaults .08/.12 seconds). Dialogue gain ranges from -60 to +24 dB; music
duck ranges from -60 to 0 dB. Positive dialogue gain is explicit amplification,
not automatic loudness normalization, and excessive gain can clip. Source fades
range from 0 to 90 seconds and clamp independently to clip duration.

Project `music_gain_db` defaults to 0 dB. `audio_fade_in_seconds` and
`audio_fade_out_seconds` default to zero, are finite, nonnegative, at most 90 seconds
and no longer than the selected passage. They affect only music. Ducking ramps
linearly in amplitude from 1 to `10^(music_duck_db/20)` before the dialogue start,
holds until its end, then returns to 1. Per-clip `duck_attack_seconds` and
`duck_release_seconds` are finite from 0 to 5 seconds. Missing values retain the
legacy .25/.5-second ramps; newly added UI clips explicitly choose .6/1.2 seconds.
Zero requests an instant transition. The
minimum of intersecting ramp tails multiplies the music gain and passage fades.
Source dialogue gain/fades remain independent; the mix does not isolate speech
from source music/effects or normalize loudness.

The frozen `decoded-reel-shared-voice-mix-v6` manifest includes the
`source-dialogue-shared-pcm-envelope-v2` component, the original default audio stream
index (first when none is default), and validated source fingerprints. Film identity,
indexed/actual duration and audio presence are checked; changed/unavailable sources
and decoding errors fail explicitly. Documents cannot supply media paths. Original
source timing, including audio-stream offsets, is preserved. Each clip's
`source_audio_mode` defaults to `original`; `voice_focus` uses an explicitly declared
5.1/7.1 front-center channel when available, copied to stereo, with gentle 80 Hz
high-pass and 9 kHz low-pass filters. Other layouts retain their ordinary mix with
those filters; there is no stereo cancellation or speech-separation claim.

Browser sequence/solo playback and export share a bounded source-window 48 kHz
stereo float WAV under `source-window-pcm48-center-band-v1`, starting at asset time
zero. `GET /lab/dialogue-audio` prepares by indexed source/range/mode and redirects
(relatively, so a proxy path prefix such as the web app's `/api` survives) to
immutable SHA-addressed WAV bytes with range support. Profile, source fingerprint,
range and mode key the lookup receipt; content hashes validate representation bytes.
Gain and fades remain outside the cached asset. The small cache under
`assets_dir/lab/dialogue-audio/<profile>` is independently regenerable: the existing
idle collector removes only stale 24-hour PCM with no active editor jobs, under
the reader's per-asset lock and a renewed age check. Verified reads refresh age;
small receipts/locks remain. This does not remove source evidence or user revisions.

Both render modes use the same PCM, stereo mixer and AAC encoder. Pair excerpts mix on the original
passage clock before trimming, preserving ongoing lines and envelopes. Live browser
audition follows the same gain convention with normal scheduling/decoding tolerance;
rendered playback and download use the same deterministic mix.

Audio clips/levels are durable revisioned project state. New source anchors are
validated; unchanged unavailable anchors can still be edited. Song replacement
clears its dialogue arrangement with the previous revision retained. AI generation
cannot change the user's audio arrangement. Labels do not establish transcription,
speaker identity or exact utterance boundaries. Manual listening remains necessary.

Timeline projects resolve picture in
slot order. Preview renders an empty slot as black for its complete duration
while music continues; export rejects remaining gaps. Unplaced selections
remain saved and do not block rendering placed clips. Legacy projects retain
their sequential ordering and complete-duration requirement under the new profile.
Rendered preview uses 720 output; the API also supports 1080 export.
Derived caches and renders remain
under `assets_dir/lab`; user originals and revisions remain durable.

#### Effects (ADR-0106, ADR-0107, ADR-0108)

A music-video document may carry `effects`, timed on the song clock. Each
effect has an ID, a kind and a span. A document without effects keeps exactly
its previous manifest and render.

**Kinds:**
- `overlay` plays another source window (`source`: film, start, optional crop)
  over the picture;
- `lock_cut` is an eye-locked dissolve across the cut at `at`. The incoming
  pre-roll fades in pinned to the outgoing feature; after the cut, the
  incoming shot eases back to its own framing while the outgoing post-roll
  fades out on top;
- `zoom_through` pushes into the outgoing feature and pulls out of the
  incoming one;
- `punch` is a decaying zoom around the feature;
- `flash` lifts exposure toward white;
- `echo` leaves a decaying trail of earlier frames;
- `fill` shows its source inside the picture's own segmented subject;
- `panel` sets its source, covering, into `rect` (output fractions), turned by
  `turn` degrees;
- `strips` lays 2-12 `sources` side by side in vertical strips;
- `screen` plays its source on a screen in the picture, in perspective inside
  the corners keyed in `quad` (top-left, top-right, bottom-right,
  bottom-left, output fractions, linear between keys). It shows the middle of
  the source cut to the screen's shape, with `radius` rounded corners and a
  curved screen's falloff. The picture is toned like the set's own: about half
  of its brightness and tint, its grey black level, a little bloom and a trace
  of the glass's reflection, fading out as a push enters. `static` seconds of TV noise come first, and
  `classes` stay in front, except instances lying mostly on the screen, which
  are its own picture. From `push` to the end the frame zooms into the
  screen, speeding up, until the source fills the frame on the last frame.

**Settings:**
- `align`: `eyes`, `subject` or `none`;
- `region` (overlays): `full`, a hard `eyes`, `mouth` or `face` patch from the
  overlay's own face, or its segmented `subject` (`classes`, COCO names,
  default person);
- `edge` (`soft` only for full overlays by default) and `matte` (a flat
  colour, for silhouettes);
- `rect` on an overlay: it shows only inside that screen rectangle (split
  screens, half-and-half faces); an aligned overlay zooms, or eases its move,
  to keep that window filled;
- `rect` on a fill: the source is set into that rectangle and shows only where
  the picture's own segmented subject is (a scene playing on a TV, behind
  whoever sits in front of it);
- `settle` (lock cuts): opt-in easing from the outgoing feature back to the
  incoming shot's own framing;
- `track`: re-align every frame;
- `blend`: normal, screen, lighten, multiply, difference, or `luma` (a double
  exposure through the picture's shadows);
- opacity, attack and release;
- `zoom` (punch and zoom-through peak) and `strength` (echo).

**Anchoring.** Effects anchor to song time, never slot IDs. A cut effect uses
the cut within one frame of `at`. Effects with no such cut, or outside the
reel, are skipped and reported. Passage and timeline edits never block a save.

**Features** come from the moment index (ADR-0099):
- the main person's two eyes when both are shown, else the eye-trace point
  sized by the subject box;
- interpolated between 4 fps instants;
- mapped through the film's content box, the clip's crop and the renderer's
  fit into output pixels.

Alignment is a similarity transform. A turn over 15° is dropped, and scale
stays within 0.6-3. Overlay edges fade over 8% of the picture's shorter side.
A moved base picture zooms up to 2x about its feature so it keeps filling its
frame. With no feature, or no index, an effect centres at unit
scale and is reported as unaligned. When the index knows the shot, pre-rolls
and post-rolls hold a frame instead of showing a neighbouring shot.

**Screens** are found by tooling, not by the render. In
`pipeline/lab/screens.py`, detection takes the lit part of the segmentation
model's TV mask and fits a line to each edge for the corners. A TV's picture
can be dark in places and people can cover it, so an editor may give the
corners instead. Following moves corners with the set: patches on the casing
are matched frame to frame and a robust scale, turn and shift is fitted.
Documents store the corners.

**Masks** come from the library's RF-DETR segmentation model. It loads in the
editor worker only when an effect needs a mask, and runs at about 15 ms a
frame. Fill masks are taken from the picture being composited.

**Rendering.** After the cut sequence renders, a compositing pass decodes it
(PyAV), composites with NumPy and PIL affine resampling, and re-encodes at
libx264 CRF 18. Screens and their pushes resample through projective
matrices. Audio is then muxed as before, and the frame count is
enforced. The manifest records `feature-locked-effects-v2` (v2: screens are toned like the
set's own picture). The job result
lists skipped and unaligned effects.

**Scope.**
- Effects show in renders only: the browser player plays cuts, and OTIO export
  carries cuts. When an edit has effects, the preview says so and opens the
  project's latest render.
- The editor harness does not place effects. Automatic placement and a UI wait
  for the owner to keep effects in reviewed edits.

#### Generated sources (ADR-0109)

A document can place a generated (AI) clip like a film shot, as a clip or an
effect source. `python -m pipeline.lab.generated register <mp4> --title T
--provenance JSON` copies the clip by content into `state_dir/lab/generated`
and records a `gen-<sha256[:20]>` identity, its duration and fps, and its
provenance in the `lab_generated` table. Source resolution falls back to that
table for `gen-` IDs only, so validation, renders, effects, OTIO export and the
player work unchanged; the API streams the original bytes. Search, ingestion
and the `films` table never include generated clips. The editor does not
choose, request or pay for them.

### Alg Mods Lab

`/lab/alg-mods` is a projectless session (ADR-0110) on the ADR-0070 pattern:
one indexed film window of 0.2-12 s from the shared source browser, one
treatment with bounded parameters (painted dots with a `vivid` or `pastel`
style, time stripes, a quadtree, or a mosaic from the `tiles-v1` bank of every
keyframe, sourced from the library, the film itself or a search pinned at request
time; the workspace is a scenes-by-treatments board whose cells are the latest
render of each pair). Where a treatment applies is a region spec resolved to
per-frame masks by `pipeline/lab/regions.py`, shared by treatments; `near` and
`far` regions come from monocular depth (`pipeline/lab/depth.py`), person
regions from a video matte (`pipeline/lab/matte.py`), and every
per-shot treatment carries a `live` region through which the film shows
unchanged). A graft
(`pipeline/lab/grafts.py`) sets a landmark-aligned piece of another shot on the
host from a plain-data plan: window, donor, light, timing and entrance; landmarks
come from the moments index. The craft
that held per treatment is kept in `docs/experiments/alg-mods-craft.md`,
and explicit renders as durable
`algmods-render` jobs on the editor worker. A job freezes source identity,
window and parameters under `algmods-v10`; exact completed requests are
reused after their artifacts verify; variants stay listed. Output is a 720x1280
H.264 preview (optionally beside the original) and a manifest. The renderer is
`pipeline/algmods/` (OpenCV dense flow and a global fit move the dots, a
light-and-colour map places them; the `algmods` extra; RF-DETR subject detection
from the `measure` extra centres the crop, paints the subject and can keep it real).
It is a treatment lab: no effect kind, search model, embedding profile,
evidence derivation, paid call or library-wide pass.

### Transitions Lab

Status: frozen. The workspace runs, but finishing now happens in Resolve
(OTIO export).

`/lab/transitions` is a projectless session (ADR-0070) for comparing a cut
between two source windows. Each window is 0.2-12 s, with Fit or Fill, an
anchor and a zoom. Browsing creates no job; explicit actions create durable
jobs on the editor worker:
- `transition-render` renders a local recipe;
- `transition-bridge` imports an externally generated video;
- `transition-generate` sends an explicitly quoted Runway request, at most 300
  credits (ADR-0071, ADR-0074).

Local renders use `transitions-rgb-v9`. It has fifteen versioned recipes
(whip, crash zoom, light and lens effects, wipes, dissolves, hard cut),
optional speed ramps that keep the native endpoint frames, and optional
interpolation (ADR-0072, ADR-0075, ADR-0076). Output is compact H.264
(ADR-0073).

Jobs freeze:
- source identity (the indexed first/last 4 MiB digest);
- windows and framing;
- every recipe parameter.

Outputs are verified and reused only for identical requests, and earlier
profiles stay available for comparison. Profile history, limits and acceptance
notes are in ADR-0070 to ADR-0076, `pipeline/transitions/` and
`web/features/lab/transitions/`.

### Ingestion

The current pipeline performs:

1. Content-addressed film probing. An HDR source (PQ or HLG transfer) is
   refused with the reason: every stage decodes to 8-bit RGB without tone
   mapping.
2. Dialogue extraction from a usable canonical English SRT sidecar, a
   metadata-eligible English embedded text subtitle stream, an accepted synced
   English download (evidence v2), or local speech transcription, in that order.
3. Shot detection and bounded subdivision of long shots.
4. Up to three ordered keyframes per shot, with temporal coverage for short shots.
5. Local preview generation and optional browser-audio playback preparation.
6. PE Core visual embeddings for frames and legacy shot vectors.
7. One hosted structured annotation over the ordered keyframes.
8. Publication of film, frame, and unit records.
9. A non-blocking local semantic-text derivation.
10. A non-blocking evidence-v2 refresh (`ingest.evidence`): metadata,
    subtitles (an accepted synced download is adopted into the published
    dialogue at once, before understanding), understanding, measurement, hero
    frames, priors, compiled tables and text views, each skipped when current.

Hosted annotation caches are scoped by provider, requested model, prompt,
schema, settings, ordered frame hashes and, when present, the sampling profile.
Changed profiles coexist instead of overwriting prior generations.

Playback preparation runs after media extraction under the existing film lock.
It uses bounded local CPU work, with no new job kind, worker role, GPU model or
hosted operation. A conversion or validation failure reports unavailable
prepared playback without making search publication depend on it; cancellation
and source-identity changes retain their existing failure semantics. Explicit
`python -m pipeline.ingest.playback <film_path>` prepares an existing film without
rerunning ingestion or indexing and serializes against that film's operations.
Only a complete validated artifact with its matching manifest is served; an
interrupted temporary output is not a completed cache.

ADR-0052 introduces `short-shot-edge-middle-native-pts-v2` for shots shorter
than the configured short-shot threshold (default 2 seconds). It proposes
beginning, midpoint and ending instants, then retains distinct native decoded
frames strictly within the retained shot range. Each artifact records raw
decoded presentation time as `source_pts`, the container's start time as
`timestamp_origin` (zero when absent), and the difference as `timestamp`, in
the same relative seconds used by the player and ffmpeg. Frame-index rows use
timestamp source `decoded_container_relative_pts_v2`; nonzero or negative
container epochs must not shift the selected shot evidence. Extremely short
shots can yield only one or two frames. Longer shots
retain their quartile sampling and existing annotation identity. Shot detection
and sampling have independent cache identities so changing sampling does not
rerun detection or alter stable shot IDs and boundaries.

Only shots carrying the new sampling profile select the enhanced annotation
prompt. Both providers still receive at most three images in chronological
order in one normal request. The prompt prioritizes observable changes,
including appearance and disappearance, keeps subjects visible in any sample,
and takes the highest visible people count across the supplied images. It
separates subject energy from camera movement and uses `unknown` when motion
evidence is insufficient. It cannot infer a fade from absence alone, guess
character identity, or claim continuous-motion verification. The selected
prompt SHA and sampling profile participate in primary and fallback cache
identities. Unchanged legacy prompts, schema and inputs retain their cache
identity, avoiding an implicit library-wide hosted refresh.

`backfill-temporal` defaults to a read-only plan over eligible legacy one-frame
short shots. Film, unit and placed project-source scopes bound the work. A
small source-backed evaluation of disappearance, entrance, exit and gesture
cases must precede a broad paid migration. Apply mode takes the existing ingest
resource lock. Enqueue mode stores frozen, bounded maintenance batches in the
existing durable job ledger and uses its ingestion/GPU worker. Foreground ingest
and GPU Match jobs take precedence; the worker yields to newly queued foreground work
between batches. New maintenance batches start in `waiting_worker` rather than
`queued`, so an older worker cannot claim a job kind it does not understand.
Updated workers claim both queued foreground work and waiting maintenance,
with foreground work first. The CLI renders the waiting state as `queued`,
including later batches waiting behind an already-updated worker. If an older
worker is still running, replace it only when current work is idle, without
interrupting ingestion. No second inference queue or
autonomous library refresh is introduced. Failed or interrupted batches require explicit retry, reusing
matching successful annotation caches.

An explicit `--unit-id` retry may include already-current evidence to reconcile
semantic text without decoding or repeating its hosted annotation. Broad film,
project and library plans do not enqueue current units solely for this repair;
they direct the operator to `index-text --film-id`. A deferred semantic-text
failure retains published evidence and the result counts but marks the
maintenance job failed with an actionable repair message. Direct apply writes
its requested receipt before exiting nonzero. Current legacy search remains
available while the independent text profile awaits complete activation.

Backfill reuses raw films, source identity, boundaries, dialogue and previews.
It retains prior frame evidence and annotation profiles while deriving only the
selected units' new images, annotations and compatible visual/text vectors.
Existing encoder-name and vector-dimension guards run before publication. The
legacy visual baseline retains coarser checkpoint lineage under ADR-0001;
backfill must keep its existing weights. A checkpoint upgrade requires a
separate versioned profile even if its name and dimensions are unchanged.
Derived profile activation continues to require exact current-generation
coverage under ADR-0002 and ADR-0009.
Target publication validates the batch before mutation, writes frame changes,
then merges only the selected unit rows. It has the same brief old-unit/new-frame
visibility window as existing ingestion, not a cross-table atomic transaction.
Bookmarks, Lab project revisions, chosen footage and edit timings are preserved.
Saved clip titles and selection `search_evidence` remain historical provenance;
new metadata changes future searches rather than rewriting earlier decisions.
Updated whole-shot annotations remain candidate evidence; they do not replace
checking a particular chosen trim against its source footage.

Under ADR-0056, manual and managed film intake share deterministic, versioned
external-subtitle validation and selection. Candidates require positive feature
filename association or a generic English label scoped by a matching release
folder. ADR-0057 also permits a matching film ancestor inside outer wrapper
folders; generic tracks must stay inside that film's subtree. A managed flat
root with exactly one video may associate generic English tracks under `Subs`
or another child directory. This exception is not applied to a shared manual
incoming root or an ambiguous managed root with multiple videos.
Forced, commentary, extra-associated, known foreign-marked and
unassociated tracks remain excluded. A bounded full-file validator is separate
from the legacy dialogue parser: every cue block must parse, timestamp fields
and positive intervals must be valid, control characters must be acceptable,
and cue times must fit the measured film duration. Automatic selection also
requires English lexical/script evidence across portions of the track, enough
diverse dialogue and density, and broad temporal coverage. These heuristics can
flag misleading language labels and likely partial tracks; they do not prove
completeness, the correct film cut, audio synchronization or translation quality.
An English filename alone is insufficient.

Intake automatically copies one passing track, including an unmarked associated
track, beside the canonical film as `<film-stem>.en.srt`. The existing preference
for one ordinary track over accessibility variants remains; conflicting tracks
remain separate review choices. Within the preferred eligible pool, copies with
the same validated full-file SHA-256 count as one automatic choice; all original
files and review handles remain intact. Review supplies bounded, non-persisted previews
and defaults to automatic selection, with explicit use or skip also available.
Automatic mode selects a uniquely passing track or preserves all external
tracks in the release and lets ingestion use its existing embedded-text or
Whisper fallback. An explicit selection must remain an eligible exact
root-relative handle when recomputed before mutation; arbitrary, stale or
structurally invalid selections fail closed. Choosing an uncertain but eligible
track explicitly does not certify its language or synchronization.

Manual `GET /incoming` remains a discovery operation without video probing or
hashing. Import obtains duration with a bounded local metadata-only probe and
revalidates subtitle eligibility before mutation. Managed intake reuses its
existing media inspection duration before subtitle selection. Neither the API
nor acquisition monitor runs a language model, GPU inference or hosted subtitle
check. Selected SRT bytes are preserved exactly, and every original release
track remains raw evidence regardless of the selection outcome.

Manual intake and ingestion never delete or automatically archive a release
directory. After its canonical film is published and downloading or seeding
has ended, an operator may move the entire marked release directory intact to
external archival storage. That archive is preservation material rather than
a configured runtime input: search and reingestion use the canonical film and
selected sidecar in `films`, while the archive retains the import marker and
raw timestamped tracks for provenance and recovery.

ADR-0047 adds managed acquisition ahead of this same ingestion boundary.
`pipeline.acquisition` owns durable intents and recovery journals in
`state_dir/acquisition/acquisition.sqlite3`, separate from the existing Lab job
database. API and CLI submit identical commands. An independent singleton
monitor polls qBittorrent 5.x with at most two simultaneous downloads; Prowlarr
provides optional search over configured indexers. There is no site-specific
scraper or second ingestion pipeline. Source descriptors stay private; browser
search results carry expiring opaque IDs. Browser-origin and bounded-body checks
apply before acquisition mutations or external search.

An empty usable release search may retry once as a quoted phrase through the
same configured Prowlarr movie-category search. Successful searches and provider
errors do not trigger that fallback; already quoted or maximum-length queries
are not rewritten. Both attempts retain the request timeout, response-size and
result-count bounds. No film year, new provider or download is inferred.

The user confirms canonical title, year and optional edition before submission.
Every managed torrent has an exact normalized v1 info hash, category, unique tag
and owned `incoming/.scene-recall-managed/<id>/data` save path; an ownership
marker lives outside the downloaded data. Unrelated torrents are never adopted.
Managed storage is excluded from manual discovery/import. Bounded descriptors,
safe relative file inventories, link/junction and executable guards, disk-space
checks and conservative feature/subtitle selection precede import. Every file
in the bounded qBittorrent inventory must be complete with its expected size,
and the client must be stopped before validation and import proceed.

ADR-0057 restricts automatic feature selection to a unique non-extra video
associated with the requested title and compatible year evidence. An explicit
filename identity takes precedence; a generic filename can use the nearest
matching film ancestor without borrowing the identity of a differently named,
explicitly dated film. A largest-file or size-ratio rule cannot choose among
collection members. Additional CD/disc/part or episode markers require review;
markers already belonging to the requested title do not by themselves imply a
split release. Extra classification likewise excludes the requested title
before checking sample, trailer, interview and related descriptors.

Supported source containers are MKV, MP4, AVI, MOV, M4V and WebM, at the root or
in nested directories. There is no automatic archive extraction, disc-image or
DVD/Blu-ray folder ingestion, or multipart assembly. A release without a
supported video fails with an actionable format message while retaining its
files. Ambiguous video and subtitle choices pause for review; automatic
subtitle mode can use the ingestion fallback without guessing between tracks.
Metadata probing and three sampled decoded frames verify basic media usability,
not film identity or full-duration quality. Media probes cannot open network
protocols.

ADR-0049 makes stopping completed downloads a per-torrent client policy as well
as a monitor responsibility. New submissions and recovery of duplicate owned
submissions set `ratioLimit=0`, `seedingTimeLimit=0`,
`inactiveSeedingTimeLimit=-1`, and `shareLimitAction=Stop`. Reapply these settings
before each app-controlled client start, including a retry that resumes a
stopped download. Verify torrent ownership before applying the policy to an
existing torrent; duplicate add reconciliation also verifies the exact save
path, and monitor-controlled retries retain the existing path ownership check.
These settings prevent sustained seeding after
completion even while the acquisition monitor is offline, without changing
global client preferences or suppressing uploads during an unfinished download.
Client limit checks permit a brief completion transition; this is not a promise
of zero uploaded bytes. Manually force-starting managed torrents is unsupported
because it can bypass their automatic stopping policy. Neither client limits
nor monitor completion handling may delete downloaded files. Explicit user
cancellation has the separate staging-discard boundary specified below.

After complete files and stopped client state are verified, the monitor records
the import plan, detaches the torrent with file deletion disabled, confirms
detachment, and performs a same-volume canonical move without overwriting files.
Source identity and selected subtitle hashes let an interrupted import resume.
The imported file then joins the existing ingestion FIFO; downloads never occupy
the inference worker. Job reconciliation excludes prior acquisitions' path
history. Failed or interrupted inference requires explicit retry.

Successful publication permits the monitor to move its entire leftover owned
directory intact to `incoming_dir.parent/evidence/managed-releases/<id>`.
Canonical source identity, publication, ownership and every remaining file are
checked before archival and when recovering an interrupted archive move. No raw
subtitle, extra or marker is deleted during successful completion. Unexpected
changes stop archival. This managed-only archival exception supersedes ADR-0023's
manual-only clause without changing the manual-release archive boundary, the
flat canonical `films` input, saved-scene state or derived model profiles.

ADR-0080 replaces retention on explicit managed-acquisition cancellation. The
API/CLI durably enters `cancelling` with pending cleanup, including for failed
items. In-flight monitor updates may journal their side effects but cannot erase
cancellation intent. The same monitor reconciles imported canonical paths and
linked or unjournaled ingestion jobs, waits for those jobs to become terminal,
stops the owned torrent, detaches it with client deletion disabled and confirms
absence on a later tick. A submitted add with uncertain acceptance must first be
observed/reconciled; absence alone does not authorize clearing its directory.

Only then may it discard `incoming/.scene-recall-managed/<id>` after verifying
the exact marker, configured-path containment, bounded tree and absence of links
or junctions. Preflight precedes deletion; per-entry identity checks and removal
of the marker last preserve recovery through partial deletion or locked files.
A durable directory identity covers interruption between marker removal and
removing the empty wrapper. Cleanup failures remain visible and retry after
30 seconds; only completed cleanup becomes terminal `cancelled`. No separate
cleanup service is introduced. Unrelated incoming files, canonical films and
subtitles, shared film assets, prior archives and saved app state are preserved.
Small private source descriptors and cancellation history remain durable.

A retry after discarded pre-import staging starts a fresh download and clears
stale import/submission/cleanup journals. After import it reuses the canonical
source, retains the discard receipt, and can complete publication without an
archive for the intentionally deleted staging. Historical cancelled records are
not bulk-purged. Cancelling manual ingestion does not delete its supplied source.

The Films queue presents active work and actionable failures without a completed
or cancelled History dropdown; acquisition records remain durable. A separate
read-only `GET /library/storage` reports deduplicated logical file sizes by
category and volume. Its scope is configured Films, incoming, assets and state
roots, exact registered source paths, the conventional sibling evidence archive,
and bounded cache directories for configured models. Archive measurement does
not make archives ingestion inputs. Whole-drive discovery, unrelated development
files and unrelated model caches are excluded. It does not follow filesystem
links or count shared file identities twice; unavailable roots and unreadable
entries make the snapshot explicitly incomplete.

Storage scanning runs in a background thread behind a five-minute cache, with
concurrent refreshes coalesced and the previous snapshot available during a
refresh. This endpoint is independent of library and acquisition polling and
never loads models, downloads files, mutates evidence or creates a durable job.
The header displays total and per-drive sizes, distinguishing films from
supporting data, and exposes measurement age and breakdown without expanding
the queue. Logical file lengths are distinct from filesystem allocation and
whole-volume capacity/free space.

The existing non-destructive minimum-content floor remains unchanged at
canonical dialogue resolution. It rejects unparseable, oversized, trivial and
promo-only sidecars; the stronger intake validator does not retroactively
reclassify existing canonical external evidence or invalidate its dialogue cache.
A preview
is transient interface data, not durable evidence or a derived dialogue source.

ADR-0056 also restricts embedded fallback selection to convertible text streams
with a valid absolute stream index and an `en` or `eng` language tag. Forced or
commentary dispositions and titles identifying forced, commentary, signs or
songs tracks exclude a candidate. Prefer ordinary English over accessibility
variants such as SDH/CC/HOH; within the preferred pool choose its sole candidate
or its unique default. An absent or unresolved candidate uses the existing
Whisper fallback. Under ADR-0079, ingestion then checks the extracted SRT with
the shared bounded structure, English-language, density and coverage validator
against the film duration. Only an automatically eligible track supplies
dialogue; a rejected track remains preserved while the existing local Whisper
fallback transcribes audio. Neither path certifies exact synchronization or
translation accuracy. Canonical sidecar precedence and eligibility remain
unchanged.

A versioned embedded-validation receipt binds the film identity, selected
stream, duration, validator profile and full extracted SRT hash. The dialogue
manifest records the actual accepted embedded source or Whisper source with
the rejected embedded dependency. Missing, malformed or changed evidence
invalidates that decision; unchanged fallback evidence/model settings reuse
the completed transcript. This embedded policy does not bump the global
dialogue contract or invalidate unrelated canonical-sidecar and Whisper
caches. Older embedded caches are reconsidered on the next explicit ingest;
no automatic library backfill is introduced.

Dialogue derivation records a contract-versioned
manifest containing the selected sidecar content hash, embedded stream
identity, or Whisper model and transcription profile. The subtitle derivation
profile (sidecar, embedded and downloaded alike) removes known promotional cues
and hearing-impaired sound cues (`[ALARM BEEPS]`, `(sighs)`, speaker tags) from
parsed dialogue without changing the raw file. An accepted synced download is
adopted into a new film's published dialogue by the post-ingest refresh, before
the understanding pass. The Whisper fallback uses VAD and
source-language transcription. A canonical `en` or `eng` tag on the primary
audio stream supplies an English language hint; missing, `und`, and all other
tags retain automatic majority voting over up to five voiced 30-second
language-detection windows. The manifest records the exact resolved language
option and the primary-audio-tag evidence when used, along with the remaining
options and faster-whisper package version. Previous-window text conditioning
is disabled. A versioned Whisper-only structural gate rejects
high-confidence exact repetition loops before dialogue publication. Rejection
stores empty dialogue for that profile and continues through visual and hosted
caption evidence; external and embedded subtitle text bypasses the heuristic.
Changing or rejecting a source, transcription profile, or gate invalidates only
dialogue and its downstream derivations; it does not mutate the immutable film
identity or rejected raw evidence.

Publication spans separate LanceDB transactions. Units are the visibility
boundary for a new film. Replacing an existing film can briefly expose new
frames beside old unit metadata; removing that legacy window requires
generation-tagged schemas or cross-table transaction support.

### Text retrieval

Normal text search (ADR-0094) independently ranks:

- PE text-to-frame visual matches (each shot by its best frame);
- the active semantic text profile;
- native full-text matches over `searchable_text` when the broad query is
  compound or explicitly quoted;
- quotes: subtitle lines from `dialogue_lines`, matched by phrase and terms and
  re-scored by ordered token overlap over a line and its neighbouring cues.

An unquoted broad query with exactly one word token omits full-text as an
independent fusion vote when a visual or semantic channel is active; quoting
the term restores explicit word evidence. The quote channel's weight follows
the best line match: strong (≥ 0.8) makes it the leading vote, weak matches add
little. The focused Words facet fuses quotes with the dialogue and OCR views.

The semantic profile uses Qwen3-Embedding-0.6B with independent non-empty views
(view contract 3): `caption`, `dialogue`, `ocr`, `facets` (framing, setting,
time, energy, measured camera movement, mood, palette, subjects), `mood`
(emotion, scene tone and sound from the understanding pass, else stored mood
labels and energy), `story` (action, characters, iconic note, setting) and
`scene` (scene title, summary, story context, tone; shared by its shots). Each
view ranks on its own and votes by weighted reciprocal rank, because distances
are not comparable across document styles; the view where a shot ranked best
is returned as evidence. Identical documents tie (a scene summary shared by its
shots gives them one rank), so the shots' own views decide between them.
Stills-guessed camera movement is not searchable.

Semantic queries use the neutral instruction `Retrieve scene descriptions
matching the query.` under `scene-recall-semantic-query-v2` (ADR-0053); stored
documents remain uninstructed under embedding contract 1. Manifests record
producer provenance and must match the model, revision, dimension, embedding
and view contracts and the exact table generations; otherwise the whole text
channel falls back to the legacy PE text vector.

Channels are fused by weighted reciprocal rank. From there each candidate
carries one relevance score through ordering (ADR-0097), starting from its
fused score relative to the best candidate's; later stages scale that score by
bounded factors rather than shifting ranks, so they settle near-ties but cannot
overrule a clearly stronger match.

When `retrieval.rerank_shortlist` is positive, Qwen3-Reranker-0.6B judges the
fused shortlist plus the three best matches of every channel and of each precise
text view (caption, story, dialogue, scene; a document shared by several shots
sends its three best-evidenced shots). It reads the query with each shot's
evidence (film, scene, action, known moment, visual caption, dialogue) and
returns log-odds. The verdict is read on a fixed scale linear in log-odds
(saturating at p 0.001 and 0.999), because probabilities hide the difference
between near-certain judgements, and is blended 0.6/0.4 with the fused score.
Unjudged candidates keep only their fused evidence. The judge has a one-second
budget. It is skipped while the GPU is nearly full (another process such as
ingest measurement), and abandoned between batches once over budget; two
overruns in a row or a full GPU rest it for a minute. Fused evidence alone
orders results meanwhile.

Query signals (`pipeline.search.signals`) then scale relevance by bounded
factors when the query names a character, actor or film, a shot scale, a camera
move, a time of day or a colour that a candidate's evidence satisfies or
contradicts; unknown evidence is neutral.

Deterministic filtering handles unrequested credits, logos, title cards, blank
frames and static artifacts using the visual caption (ADR-0054, ADR-0087). Scene
titles from the understanding pass add one category: a scene titled purely as
studio or distributor logos (optionally with title cards or credits) is
dropped from search, highlights and film browsing unless the query asks for
logos (ADR-0112).
Visual deduplication then folds a near-identical shot of the same film (a reverse angle, a
recurring set-up) into the card it resembles, among its other matching shots, and drops
near-identical shots of other films (ADR-0101).

Priors apply after relevance (`pipeline.search.priors`): a shot's relevance
score is multiplied by a bounded factor from its fame (library-scaled) and craft
under the selected preset — `balanced` (default), `famous` or `gems` (demotes
iconic shots and weak craft). Between equally good matches the prior decides; it
cannot overturn a clearly stronger one. Recipe results, which carry no relevance
score, apply the same factor to a rank-derived relevance. Priors reorder inside
the relevant pool and never add candidates; quote-like queries keep
half-strength priors; shots without evidence are neutral. Shots of the same dramatic scene fold into one
card whose other matches are listed as `scene_alternatives`.

Ordinary unscoped streams then apply the 30-second defer-only temporal spread
and the bounded film repeat-rank policy
(`original_rank + strength * repeats / (repeats + 1)`, default strength 32).
Films the query names by title, character or actor are exempt: a named film is
an implicit scope.
Normal unscoped broad search retrieves three times the per-channel depth and
keeps a one-page cross-film reserve. Explicit film scopes preserve strict
relevance order. Unscoped mandatory visual recipes keep their page-wise per-film
preference; movie-scoped mandatory visual recipes keep the 90-second reference
spread.

Results carry `keyframe_url`/`keyframe_index` (the exact indexed frame used when
a result becomes a search source) and a display-only `thumbnail_url`: the hero
frame, unless the visual channel is what found the shot. They also carry badges
(`iconic`, `gem`), story action and characters, peak time, the focus span
(`focus_start`/`focus_end`: the picture holding the peak, never across a
dissolve), scene context and the matched subtitle line with exact times. The
hover preview is the ingest clip around the shot's midpoint unless that strays
outside the focus span; then `/media/preview` serves the evidence clip around
the peak, and `preview_url` carries the span as its cache key. The player opens
at the matched line or frame, else at the focus span.

Text views, frames and unit metadata are searched from resident copies of the
request's pinned snapshot (`pipeline.search.resident`): float16 matrices (GPU
when there is room, otherwise CPU) and an Arrow metadata table, keyed by table
and version. A table version holds exactly one vector space, so resident search
cannot mix profiles; any load failure falls back to Lance.

### Reference and Framing retrieval

Reference-image search retrieves PE frame candidates and spatially reranks a
bounded shortlist using a learned 6x6 feature grid. An unscoped uploaded image
first reads a fixed 7,200-row frame metadata projection, collapses it to the
best frame per unit, and hydrates only the ordinary 200-unit base plus at most
one unit for each of 12 films absent from that base. The projection is a
bounded measured prototype, not a library-size-scaled guarantee; explicit
movie scope keeps the former three-frames-per-candidate depth. The global frame
rank survives selective hydration so Look evidence and recipe fusion do not
overstate a deep reserve hit. Framing keeps a bounded spatial work set: it
scores the normal 96-row spatial base plus at most 12 cross-film additions from
the post-base pool, including eligible reserve rows. Remaining candidates stay
semantic backfill. The query image is encoded once. ADR-0082's source-hashed
cache reuses valid entries and encodes misses with the same float16 numerical
contract; legacy complete caches remain supported during migration. This is the
baseline candidate policy. The separately gated compact composition challenger
is described under Activation and fallback. Framing represents coarse
composition and subject position, not pose or motion.

Spatially added reserve rows are fully scored, while reserve rows left in the
semantic backfill retain only their global evidence; neither path has a
calibrated relevance floor. The existing page-wise preference can surface a
deep reserve row ahead of same-film backfill. Treat this as a prototype
candidate-recall experiment, not a
relevance-safe guarantee. Before changing its depth or claiming it improves
visual discovery, human-grade the deep rows' original global rank, distance,
relevance, and displaced repeats across roughly 10-15 image references.

The Framing cache is an optional, independently backfillable acceleration of
the existing scorer, not a candidate vector space or a new retrieval mode. One
profile table stores float16 grids by stable frame identity and is scoped by
the resolved immutable PE checkpoint revision, extraction contract, grid and
feature dimensions, row schema, storage dtype, and relevant OpenCLIP, timm,
Torch, Torchvision, and Pillow versions. Backfill and compatible-cache queries
load that exact checkpoint revision rather than resolving mutable `main`
independently. Its manifest proves exact coverage of one published `frames`
generation, including the frame-identity digest and profile-table generation.
The idempotent `index-framing` command derives it from retained keyframes
without raw-film decoding or hosted inference. Search reads the source-hashed
partial cache (ADR-0082) whenever one exists, so `index-framing` then refuses.

The frontend exposes one result action for all five modular facets rather than
a separate Framing shortcut. Choosing Framing, or dragging the same result onto
the Framing tile, adds the same composition source clause to the active recipe;
existing clauses remain within the three-clause limit. Its source-film
exclusion is applied server-side before candidate generation, so source style
cannot consume the bounded reference shortlist. Explicit film scope still
controls the allowed library, and a scope containing only the source film takes
precedence rather than becoming an empty cross-film search.

Text and reference clauses can be combined. The reference result set remains
mandatory; text reranks only those candidates and cannot introduce a visually
unrelated result. The exact matched reference frame is preserved, and matched
text evidence is attached when available.

Search responses are authoritative bounded prefixes. Every search surface
accepts an optional result limit: omission uses the configured 48-result
default, while an explicit request may deepen the same ranking up to the
configured maximum of 200. Below that maximum the backend probes one
additional eligible row and returns `has_more` and `next_limit`; clients never
infer exhaustion from a full page alone. A deeper request returns the complete
prefix and replaces the earlier one, preserving one application of ranking,
deduplication, temporal spread, and film-diversity policy. The frontend may
reveal a prefix in viewport-sized batches, but it does not regroup that prefix
into a separate Best per movie result mode. This bounded contract requires no
cursor or server-side search-session state.

### Match Cut in ordinary search (gated)

Ordinary search offers Framing only. A match-cut option there still needs
ADR-0008's gate: on an owner-reviewed reference set it must beat Framing, and
it needs acceptable latency and complete coverage (now the moment index,
ADR-0099). `pipeline/eval/match_cut.py` scores candidates against that gate.
The offline dense-geometry adapter (`pipeline/experiments/dense_geometry.py`)
is frozen, recorded in ADR-0026.

### Modular recipe retrieval

`POST /search/recipe` composes one to three explicit JSON clauses without
adding a router, query LLM, model, index, or ingestion dependency. The
multipart `POST /search/recipe/image` variant carries exactly one bounded
uploaded still assigned to either `look` or `composition`, alone or with at
most two text or indexed-scene refinements. The complete recipe therefore
remains bounded to one to three total clauses. Clause IDs and typed-refinement
facets must be unique. Text clauses support broad `all` search plus `scene`,
`words`, `look`, and `mood`; indexed-scene sources support those same focused
facets except `all`, and also support `composition`. Uploaded-image clauses
support only `look` and `composition`; unsupported image facets fail request
validation rather than silently changing meaning. Both recipe variants accept
the same optional bounded result-prefix limit as the standalone search
surfaces.

The adapters deliberately expose evidence already present in the current
system:

- `all` uses normal hybrid text retrieval;
- `scene` searches caption semantic views;
- `words` searches dialogue and OCR semantic views;
- `look` uses the paired PE text/frame space without spatial reranking;
- `composition` uses the indexed source frame and existing spatial reranker;
- `mood` searches only the dedicated mood-and-energy semantic view.

Both uploaded-image adapters embed the still once through the existing PE
image tower. Uploaded `look` retrieves bounded candidates from the global PE
frame space. Uploaded `composition` retrieves the same kind of global
candidates and applies the existing 6x6 spatial-layout reranker. The two modes
are explicit alternatives, not independent fusion votes. Either uploaded-image
set is a mandatory recipe gate, so text or indexed-scene refinements may rerank
it but cannot introduce visually unrelated units. The upload has no source
film to exclude, so explicit movie scope and the normal unscoped diversity
preference apply. Uploaded images are query-bound inputs: they are validated
and decoded for that request, never persisted as library evidence, and
introduce no new vector space or backfill. Multipart recipes reserve the
existing bounded image-work capacity before decoding and hold it through the
serialized model work, so rejected concurrent requests cannot allocate decoded
image buffers first.

The frontend accepts at most one uploaded image. Its picker and an image dropped
on the open workspace assign the upload to Look by default; a direct drop on
Look or Framing assigns that facet immediately. The image is rendered as a
source card inside its tile and can be moved between those two categories or
removed using the same visible modular state. Moving replaces, rather than
copies, the image clause and any target clue. Up to two optional category or
main-text refinements remain visible beside it and use only backend-owned
recipe ranking. Scene, Words, and Mood never pretend to interpret arbitrary
image content; enabling those destinations requires a separately versioned
query-time caption, OCR, or mood adapter with explicit privacy, latency, and
cost boundaries. The standalone image endpoint remains API-compatible, and
the product never maintains a separate browser-fused reference result state.

Source clauses address an indexed unit and, for `look` or `composition`, an
exact frame index. The server resolves the corresponding unit, film, vector,
and file path from the active index; browser-supplied paths or vectors are
never trusted. A scene used as a `mood` source derives its query from the same
labeled mood-and-energy serialization as the indexed view. Every source and
its facet-required evidence is validated before an empty composition target
scope can return no results. Every referenced source unit is removed from the
result set.
Composition retains the ADR-0005 cross-film default and is a mandatory
candidate gate. Its effective result-film scope is resolved before any bounded
clause retrieval and shared by every clause, so source-film hits cannot consume
an auxiliary clause's candidate window. Other clauses may rerank composition
candidates but cannot introduce composition-unrelated results. The uploaded
Look or composition clause is likewise a mandatory gate; global retrieval and,
for composition, spatial reranking remain internal to that one clause.

A recipe containing only one broad `all` text clause delegates to normal text
search, preserving its complete bounded candidate union and diversity behavior,
then adds the recipe match evidence. All other facet adapters return bounded
raw rankings; recipes with multiple clauses combine them using equal
reciprocal-rank fusion inside any mandatory candidate gate. An uploaded image
contributes one ranking regardless of its internal stages. Existing
junk suppression and visual deduplication are applied once. Ordinary unscoped
recipes then use the 30-second defer-only spread; recipes with an uploaded image
or indexed composition source instead retain the established 90-second
reference spread as their sole temporal preference. Final film diversity runs
afterward: unscoped recipes without either mandatory visual gate use the
bounded, saturating repeat-rank policy, while mandatory visual recipes retain
the page-wise, relevance-backfilled preference when unscoped. Explicit movie
scopes preserve strict relevance order for ordinary non-image-gated recipes;
scoped mandatory visual recipes retain the 90-second reference spread but skip
film balancing. Each returned product result includes
stable `keyframe_index` evidence and a `matches` entry for every clause that
retrieved it, including the contributing rank and matched text or frame when
available.

The recipe response describes each resolved source input separately from
result-match evidence, deriving that description from the same resolved
in-memory source input passed to its ranking adapter. Caption, dialogue/OCR,
and mood sources expose the exact effective text used by their adapter. Look and
composition sources expose a global-visual or spatial-visual frame mode and
never fabricate an English description for a vector or learned grid. An
upload produces one source-evidence record explicitly labeled as query-bound
image input and reports `pe_global` for Look or `pe_global+spatial_6x6` for
composition; it likewise never receives generated text.

Focused `scene`, `words`, and `mood` clauses require a complete active
semantic-text profile. They fail explicitly when it is unavailable instead of
silently using the inseparable legacy combined text vector. Broad `all` search
retains the established safe fallback.

## Activation and fallback

The semantic-text table identity includes its model revision, dimensions, and
embedding contract. Its manifest additionally records the complete ordered
view-projection contract. Its manifest must exactly cover the current units
generation before the profile becomes active. A view-contract change
invalidates an older manifest, while unchanged rows in the same compatible
Qwen vector space may be reused by reconciliation.

If the profile or manifest is missing, stale, partial, corrupt, unreadable, or
unavailable at query time, the complete dense-text channel falls back to the
legacy PE `units.txt_vec` representation. Partial generations are never mixed.

Existing films build or repair this profile with the idempotent `index-text`
command documented in `README.md` (`python -m pipeline.evidence compile` runs it
after compiling evidence). New ingestion attempts the same derivation after
publication; failure leaves the film searchable through the fallback. A film
ingest, or an evidence refresh holding the ingest lock, also reconciles other
films' missing or stale views when the library-wide gap is at most 25,000
views, then activates the profile. Larger gaps (migrations) stay an explicit
`index-text` run (ADR-0111).

Evidence-v2 search inputs activate per film: compiled rows exist only for films
with artifacts, and every consumer treats a missing row as "no evidence"
(neutral prior, no story/scene view, keyframe thumbnail). Understanding and
measurement profiles are read at their configured producer identity only;
artifacts from other profiles stay on disk and are ignored. Resident vectors and
metadata are accelerators: they load from the pinned snapshot's table versions
and any failure returns the request to the Lance path. The cross-encoder rerank
is optional (`retrieval.rerank_shortlist: 0` disables it); an unavailable model
leaves the fused order unchanged.

ADR-0082 separates acceleration from retrieval evidence. The new
`source-hashed-spatial-cache-v2` table validates each requested frame against
its current source bytes, frame identity, file metadata, extraction profile
and descriptor checksum. Valid entries are reused; only misses are inferred.
Live and cached candidate/query grids use the identical float16 round trip.
Cache occupancy changes neither candidate membership nor numeric scoring.
Legacy complete caches retain ADR-0009's manifest validation during migration.

Optional preparation runs as durable jobs in the existing ingest worker: one
job per film/profile/source generation, yielding every 32 frames behind
foreground work; storage or lock pressure pauses it and explicit retry resumes
the cursor. It never delays canonical film readiness, and source and profile
changes remain backfillable without re-ingesting films. New ingests no longer
enqueue it (see below).

ADR-0092 adds an explicit operator hold for currently pending optional
preparation/fitting jobs. Drain active work first; `search-features pause`
preserves descriptors and cursors in the existing ledger. Worker restart and
duplicate enqueue cannot lift that hold; `search-features resume` releases only
operator-held jobs. Ingestion no longer queues this preparation for new films:
the framing representation pilot is frozen until measured subject layout
(Framing v2) replaces it.

Before further bulk preparation, an isolated 524-frame representation pilot
compares current PE final features, one frozen intermediate layer and an
accessible small spatial encoder. All arms search the same independent
cross-film candidate pool, without an appearance shortlist. Model/checkpoint,
code, source, preprocessing and output hashes stay distinct; no pilot artifact
is published into serving indexes. Its 90-minute compute and 2 GiB artifact
caps, 32-frame checkpoints and existing ingest lock bound local work. The
pilot provides screening evidence only; unjudged results cannot promote a
model or bypass the existing coverage and human-review gates.

The experimental compact composition route uses a frozen uncentered projection
of existing PE cells, with separate 32/64-dimensional profiles and at most
65,536 film-balanced sampled cells. Matrix and sample checksums are immutable
profile identity. Corresponding screen positions survive projection and vector
normalization. Exact composition retrieval searches all eligible retained
frames independently of appearance. The two rankings each contribute half of
96 detailed frames; RRF fills overlaps and at most twelve absent films receive
additional candidates. Best frame per unit is selected after spatial scoring;
65% global / 35% spatial weights initially remain. This is a Framing challenger,
not a pose, temporal action or narrative context representation.

Complete source coverage is mandatory for composition retrieval. A partial,
stale or corrupt profile explicitly falls back to baseline Framing for the
whole request. Selection requires a human review receipt and an explicit
`retrieval.composition_profile`; the default is null and null provides rollback.
Promotion requires twelve distinct references, at least eight preference wins,
median nDCG@10 relative improvement of 20%, at most two regressing cases,
known-positive retention, complete coverage, warm p95 <=5 seconds and actual
optional storage <=64 GiB. A Framing receipt does not activate a new Lab matcher.

Optional storage admission counts physical indexes, cached/compact derivations,
retained versions and scratch against `retrieval.optional_storage_gib` (64 by
default). Cache eviction and native version pruning require the established
idle reader/writer guards; measure actual reclaimed bytes. Queries never clean
storage. An isolated IVF_FLAT benchmark cannot install a production index.
Every evaluated ANN route/scope must retain >=99% of exact candidate units,
all known positives and improve retrieval p95 >=30% before a separate rollout.
No retrieval route may use `fast_search` to omit unindexed rows.

The PE visual tables are a frozen legacy exception. Frame rows contain only a
coarse encoder name and unit rows lack exact visual-model lineage. The runtime
loader now resolves one immutable upstream checkpoint per model operation, but
those legacy row and table contracts still do not record or activate that
revision. Search and publication reject a configured encoder-name mismatch,
but this baseline must not be rebuilt piecemeal after upstream weights change.

Any future visual or multimodal replacement must use a separate versioned table
with exact revision lineage and its own activation manifest.

Match cuts follow the same rule. The moment index is its own versioned
derivation: its manifest records every film's producer profiles and the
index contract. If match cuts ever enter ordinary search (ADR-0008), a
missing, stale or partial index disables them as a whole, and Framing is
never a silent fallback.

## Known limitations

- Film, frame, and unit writes are not cross-table atomic.
- The legacy PE baseline lacks immutable checkpoint lineage.
- Hosted annotations record the requested model identifier, not a
  provider-resolved immutable revision.
- Ordinary search matches up to three indexed keyframes per shot; only Match
  Cuts (ADR-0099) sees every 4 fps instant.
- Framing grids are cached only for the frames of the 24 films the partial
  cache (ADR-0082) covers; Framing encodes other candidates live.
- Match cuts are not offered in ordinary search. Framing cannot reliably match
  pose, temporal direction, brief action or camera movement.
- Semantic dialogue is embedded at shot level; utterance rows serve the quote
  channel through full text, not embeddings.
- Full text covers visual captions and dialogue, not the understanding pass's
  story and scene text, so a shot described precisely only by its story line
  can rank below hundreds of loosely similar shots and never reach the judge
  ("a face slowly rising from the dark basement stairs" ranks 228th in its view).
- OCR comes from the general annotator rather than a dedicated OCR pass.
- Story, scene and in-context mood exist only for films with an understanding
  artifact; fame and craft are model ratings calibrated per film, not audience
  measurements of individual shots.
- Measured camera labels are unreliable on chaotic handheld, water, smoke and
  very dark shots (they stay `unknown`); dolly versus zoom is not separated.
- Hidden cuts are detected and recorded but units are not yet split.
- Dissolves and fades are located only as finely as the three keyframes per
  shot: a focus span ends at the last keyframe that still shows the picture, so
  it can trim a little usable footage, and a change between two similar-looking
  pictures goes unseen. TransNetV2's gradual-transition output misses slow
  dissolves, so a denser detector needs its own evaluation first (ADR-0098).
- There are no film clip/audio, router or RAG indexes. Imported music has
  bounded per-passage derivations, not a film-audio search index.
- Saved scenes are local to one configured state database; named collections
  and account synchronization are not implemented.

These are boundaries, not an automatic backlog. Retained source evidence makes
targeted future derivations possible without predicting every metadata field.

## Decision gates

Add a representation only when a concrete workflow or repeatable failure shows
that current retrieval lacks necessary evidence.

For a material retrieval change:

1. Determine whether the problem is missing candidates, poor ordering, or a
   missing workflow.
2. Choose the smallest representation that addresses that failure.
3. Build it as a separate versioned profile on a representative subset.
4. Compare old and new top results on roughly 10-15 real queries.
5. Activate it only after complete coverage and explicit selection.
6. Retain the compatible fallback until removal is separately justified.

Prefer local models for deterministic, high-volume candidate generation.
Hosted models are appropriate for bounded annotation or shortlist processing
when they add evidence or quality unavailable locally.

A reranker is justified only when recall is adequate and ordering is the
problem. Temporal retrieval is justified only when action or camera-motion
failures recur. RAG is justified only for grounded reasoning, comparison, or
reel-building above retrieved evidence.

Match cuts run over the library-wide moment index in the Lab and the editor
(ADR-0099). Offering them in ordinary search needs ADR-0008's reference-set
gate.

Paid library-wide processing, global model activation, and removal of a working
fallback require the small comparison above. Structural versioning, cache
preservation, and safe backfills do not require a permanent golden corpus.

## Evaluation and expansion gates

Review the integrated search and Lab workflows at desktop and narrow widths,
then judge 10–15 real discovery queries and three real music passages. Record
retrieval misses separately from planning, timing and editing failures. The
synthetic media and contract tests establish mechanics, not editorial quality.
Study a varied creator-published reference set before expanding the editor.

Match cuts are judged by the owner playing cuts on a reference set drawn from
the edits they like. Weights change only from those notes (ADR-0099). A query
router or LLM decomposition layer cannot replace missing
visual or temporal evidence and must not become an always-on dependency without
its own demonstrated need.

Film-audio retrieval, always-on routing, scene graphs, speed ramps and
speculative metadata remain deferred until their decision gates. ADR-0045
admits full-song generation up to ten minutes through bounded sections;
it does not change those wider retrieval boundaries. Active implementation
and verification work is tracked in [current-work.md](current-work.md).

Historical rationale is recorded in
[`docs/decisions/`](decisions/README.md).

## Moment-level Match Cuts (ADR-0099)

Match Cuts matches instants, not shots, across the whole library. It stays a
Lab workspace and an editor transition score. Ordinary search does not route
into it (ADR-0008's gate still holds for a "find match cuts" search option).

**Evidence.** The `moments` producer (`match` v1) decodes each film once,
skipping non-reference frames on NVDEC. It describes every instant on the
film's 4 fps grid inside every shot, at least 0.04 s from its cuts, in content
coordinates (the film's letterbox bars, from `measure`, are cropped away):

- RF-DETR segmentation (COCO): at most six objects above 0.35, by area x
  score, each with a box and a 16x16 silhouette inside it;
- RF-DETR keypoints (COCO 17) for the four largest people (by keypoint extent,
  person probability 0.5), on instants whose segmentation found a person;
- a 32x18 luma thumbnail, a 16x9 doubled-angle edge field with edge energy,
  8x5 mean colour, sharpness and brightness.

Arrays go in `<profile>.npz` beside the JSON artifact, which records their
SHA-256. Readers reject a mismatched pair.

**Index.** `pipeline.matching.moments.index` compiles every film's serving
`moments` profile (the newest earlier profile while a new one backfills),
joined with `measure` (camera motion averaged within 0.25 s, hidden cuts) and
`hero` pictures (ADR-0098). The result goes into one memory-mapped directory,
`assets_dir/matching/moments/<id>`, published atomically with `current.json`.
It is derived and rebuildable, and the manifest records every film's profiles.
A per-film evidence refresh (after ingest) runs `moments` after `measure` and
rebuilds the index when a moments pass ran. Readers memory-map every column,
including the coarse matrix, so a process that never searches pays nothing.

- An instant is a usable cut point when:
  - something in it is lit (a thumbnail cell reaches luma 0.1), so a satellite
    in space counts and a fade does not;
  - it sits at least 0.3 s from a hidden cut;
  - it falls inside one of its shot's pictures.
- Main-subject travel comes from the neighbouring instants of the same shot.
- Coarse vectors cover every other instant (0.5 s). They hold separately
  normalized, PCA-reduced parts: layout, light, lines, main silhouette, main
  pose, colour and motion.
- `calibration.json` holds each reward's p50/p90/p99/p99.9 over random pairs
  of usable instants.

**Scoring** (`pipeline.matching.moments.score`). A pair is an outgoing and an
incoming instant, each seen through a crop in content fractions; everything is
compared in output coordinates.

- **Rewards**, each calibrated to 0 at chance and 0.4, 0.8 and 1 at p90, p99
  and p99.9:
  - `subject`: soft instance IoU after greedy assignment of the prominent
    groups (a quarter of the largest instance's area x score, at most four);
    class families discount (person-animal 0.8, others 0.55-0.7);
  - `eyes`: the eye-trace point. A person's eyes come from keypoints (else
    nose, else ears); a person with no face shown has none. Other subjects use
    the box centre;
  - `pose`: object keypoint similarity over prominent people, with parts shown
    in either frame as the denominator;
  - `shape`: silhouettes inside their own boxes, times aspect agreement;
  - `light`: Pearson correlation of blurred luma;
  - `lines`: cosine of the edge fields;
  - `colour`: the colour layout;
  - `motion`: continuation of camera, push and subject velocity, with still
    into still neutral.
- **Penalties**: a brightness jump and 0.12 per unit of zoom.
- **Weights** follow a focus (auto, subject, shape, motion, composition,
  colour). A flat outgoing picture moves its light and lines weight to the
  subject, or to motion when it has no subject.
- **Outputs** are 16:9 (the whole picture, letterboxed), 9:16 or 1:1. The last
  two crop a full-height window of their shape around the subject.
  - Reframing zooms the incoming crop up to `zoom_max` (default 1.5) and moves
    it so its eye point meets the outgoing one.
  - Scale follows height, or width when either subject is cut off at the
    bottom.
  - Crops never leave the picture.
  - A chain keeps its outgoing clip's crop while the format stays the same.

**Search** (`pipeline.matching.moments.find`):

- The reference is the usable instant nearest the requested time.
- One pass over the float16 coarse matrix yields the top 6,000 instants and
  the best 360 shots, keeping at most three hits per shot. The pass is a GPU
  product when the matrix fits with 2 GB to spare, loaded once per index, else
  float32 CPU blocks.
- Every usable instant within 0.8 s of a hit is scored exactly, if it leaves
  `min_seconds` (default 1) of footage after the cut (`next`) or before it
  (`previous`).
- Soft frames lose up to 0.04 against their film's median sharpness.
- Results keep one instant per shot, one shot per scene and three per film,
  backfilling capped films last.
- The source film is excluded by default; including it still skips the
  reference's scene. Chains exclude their own shots.

**API** (`/matching/moments`), synchronous and projectless:
- `GET /status`;
- `GET /shot` (bounds and grid instants);
- `POST /search`;
- `GET /frame`: content-cropped JPEG stills, decoded on demand and cached under
  `assets_dir/matching/frames`.

**Workspace** (`/match`, a session per ADR-0051):
- The cut-point scrubber snaps to usable instants. Format, focus, reframing and
  same-film settings live in the URL.
- A results grid shows each incoming frame through its crop, with an
  onion-skin overlay of the outgoing frame on hover.
- The audition plays the outgoing 1.5 s and incoming 2 s in the browser. Two
  alternating video elements through CSS crops each seek ahead to their next
  window, with frame nudges and an overlay view.
- **Add to chain & continue** appends the shot and searches from its
  out-point. A chain lives in the session and plays back to back.
- **Vision** draws what the index stored for the nearest analysed instant
  (`GET /matching/moments/vision`): objects with silhouettes, poses, the
  scorer's eye point, lines, light, colour and motion, over the Cut from
  frame. Each match reason names the layers that show it, so a hovered cut
  and the audition's overlay draw one reason at a time on both frames, with
  the reasons as bars. Layers are described by kind in content fractions and
  name the film's
  moments profile (and, for the current producer, its models), so a new
  producer version changes the data, not the overlay.

**Editor** (harness v2, `pipeline.lab.harness.matchcuts`):
- With a built index, the assembly scores each transition from the previous
  placement's last frame into the option's first frame. It uses the
  identity-crop form of the same components, weights and calibration, with
  features cached per instant and scored per (state, span) batch.
- `planner_settings.match_cuts` (`off`, `some` default, `many`) sets the
  weight: 0.35 or 1.0 in beam-score units. The score replaces the shot-level
  eye-trace term; screen direction, scene, film and grade terms stay.
- Reasons say when a shot cuts in on a matching frame. The v2 diagnostics
  count matched cuts (at least 0.6).

**Limits.**
- Cut points are exact to the grid (+-0.125 s), plus up to two frames from
  skipped decoding. Renders use exact source times; the browser audition can be
  a frame off.
- Conceptual rhymes and non-COCO shapes rest on light and lines.
- Detector classes flicker.
- Native-frame refinement, a dense semantic channel, editor pool injection and
  an ordinary-search option wait for played-cut evidence (ADR-0099).

The former scene-based search (ADR-0040/0046/0048) no longer backs `/match`.
The prepared-cohort finder (ADR-0027, ADR-0038) is removed with its editor,
endpoints, job kinds and tests (ADR-0116, ADR-0117); `pipeline/matching/` is
`moments/` alone.

