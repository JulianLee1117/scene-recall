# scene-recall

Semantic search over your film library: describe a scene in plain English and get the matching shots back.

The implemented system, architectural boundaries, and deliberately deferred
directions are documented in
[docs/search-architecture.md](docs/search-architecture.md). Historical reasons
for material choices are indexed in
[docs/decisions/](docs/decisions/README.md).

## Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/) (package manager)
- CUDA 12.8 (GPU recommended; CPU falls back automatically)
- [ffmpeg](https://ffmpeg.org/) on your `PATH`
- Node.js 20.9+

## Setup

```bash
# Install Python dependencies
uv sync --dev

# Configure the annotation provider
cp .env.example .env
# Add OPENAI_API_KEY to .env (or GEMINI_API_KEY if using Gemini)

# Configure the web frontend
cd web
npm install
cd ..
cp web/.env.local.example web/.env.local
# Edit web/.env.local if your API runs on a non-default port

# Edit config.yaml with your paths and model preferences
```

`paths.state_dir` stores durable user-authored state such as saved scenes. Keep
it outside the replaceable `assets_dir` and include it in normal backups. Older
configs default it to a `state` directory beside `films`.

The default annotator uses OpenAI `gpt-5.6-luna`. Annotation sends up to three
derived keyframes per shot with response storage disabled. Set
`models.annotator_provider: gemini` and a Gemini model name in `config.yaml`
to use Gemini instead. The OpenAI API project must have active billing or
credits; a ChatGPT subscription does not supply API quota.

## Run

Start the API server and the Next.js dev server in separate terminals:

```bash
# Terminal 1 — FastAPI backend
uv run uvicorn pipeline.api.main:app --reload

# Terminal 2 — Next.js frontend
cd web && npm run dev
```

On Windows PowerShell, use `npm.cmd run dev` if the execution policy blocks
`npm.ps1`.

Open [http://localhost:3000](http://localhost:3000) in your browser.

The API accepts browser requests from `http://localhost:3000` and
`http://127.0.0.1:3000` by default. If the frontend runs elsewhere, set the
comma-separated `SCENE_RECALL_ALLOWED_ORIGINS` value in `.env`.

## Ingest a film

### Raw source workflow

Keep active downloads separate from finalized source files:

- `V:/scene-recall/incoming/` — downloading or seeding; never ingest here.
- `V:/scene-recall/films/` — completed, canonically named, immutable sources.

In Windows Disk Management, reserve `V:` for this drive and confirm it is
mounted before starting the API or an ingest. Create `incoming` once and set it
as the torrent client's download directory. Keep each finalized movie file
directly inside `films` because source discovery is not recursive.

Download and seed in `incoming`. After seeding is finished and the torrent is
removed, open **Films** in the frontend. **Review & add** suggests the main
video in each release (the largest supported file), a title, year, and final
`Title (Year) [Edition].ext` filename. Confirm that downloading/seeding has
finished, then add it to the library and optionally queue ingestion. The move
is instant because `incoming` and `films` are on the same drive. Other videos
inside that release folder are left in place and are not offered as films.

Ingestion is FIFO and runs one film at a time in an isolated, low-priority
child process. The Films screen polls only while work is active; completed
jobs remain visible until the API restarts. A separate CLI ingest fails with a
clear message while another ingest owns the shared resource lock.

Do not rename or move a source after ingestion. If relocation is necessary,
use `relink-film` below. `films` is scanned for unindexed source discovery;
published library records come from the database. The database, vectors,
keyframes, and previews remain in the internal-drive `assets_dir` for fast
interactive search.

```bash
uv run python -m pipeline.cli ingest "V:/scene-recall/films/Film (2001).mkv"
```

The CLI remains available as a fallback for an already finalized file placed
directly inside `films`. Omit `[Edition]` when there is no confirmed cut or
release variant, and use ` - ` in place of a colon.

The pipeline runs: probe → dialogue → shots → keyframes → visual embed →
annotate → publish shot/frame indexes → semantic-text derivation. Completed
hosted annotations are cached by annotation profile under that film's asset
directory. If an ingest is interrupted, re-running the same file reuses
matching dialogue, media, and annotation artifacts instead of paying for those
annotation calls again. Changing the annotation model, prompt, settings, or
keyframe content selects a new cache profile without overwriting the old one.
The final text derivation is local and non-blocking; a safely published film
remains searchable through the legacy baseline if that optional step fails.

Hosted annotation requests run concurrently within a film; tune
`ingest.annotation_concurrency` in `config.yaml` (default 8).

To ingest a whole directory, skipping films that are already fully indexed:

```bash
uv run python -m pipeline.cli ingest-batch path/to/films/
```

Films are processed one at a time (the local GPU stages don't benefit from
overlapping films). A failed film is reported and the batch continues; pass
`--force` to re-ingest films that are already indexed.

If all film data is saved but the final search-index refresh fails, do not
force a re-ingest. Repair only that derived index:

```bash
uv run python -m pipeline.cli repair-search-index
```

### Relocate an indexed source film

Copy the raw movie to its final path and keep the original until verification
finishes. `relink-film` performs a full SHA-256 comparison, updates only the
stored source path and relocation-sensitive cache identities, and leaves the
existing units, frames, vectors, annotations, and derived media untouched.
It is a dry run unless `--apply` is supplied and never moves or deletes either
raw file. Apply mode writes a per-film recovery journal before changing cache
metadata; rerunning the same command completes or rolls back an interrupted
relink before doing anything new.

```powershell
# Validate the copy and show the planned metadata changes.
uv run python -m pipeline.cli relink-film "V:/scene-recall/films/Film (2001).mkv" --title-from-filename

# Commit after the dry run passes.
uv run python -m pipeline.cli relink-film "V:/scene-recall/films/Film (2001).mkv" --title-from-filename --apply
```

If apply is interrupted and either movie path is temporarily unavailable,
recover directly from the durable journal with
`uv run python -m pipeline.cli recover-relink FILM_ID`.

The old indexed source must still exist so the command can prove that both
files are byte-for-byte identical. Delete it only after library, playback, and
search checks pass. For a copied film that was never indexed, compare the full
SHA-256 hashes of the old and new files before deleting the old copy. Deleting
the old source is migration-safe after these checks, but it leaves only one raw
copy unless the film also exists in a separate backup. New ingests use the
final filename stem as their display title, rather than unreliable embedded
container-title tags.

For an index created before frame-level search was added, build the derived
frame table once from the keyframes already on disk:

```bash
uv run python -m pipeline.cli index-frames
```

This step is local, idempotent, and does not call OpenAI or Gemini. New ingests
build the frame index automatically.

Build or repair the independent Qwen semantic-text profile from already
published captions, dialogue, OCR, and facets with:

```bash
uv run python -m pipeline.cli index-text
```

This command is also local and idempotent. Its model-versioned profile becomes
active only when its manifest exactly covers the current units generation;
partial or stale data falls back as a whole to the compatible legacy index.

Once more than one film is indexed, use the compact **All movies** control to
search one movie, several movies, or the whole library. The picker gains title
search automatically as the library grows. The results toolbar switches between
**All scenes** and **One per movie** for the movies already represented in the
returned window; it does not force an irrelevant result from every indexed
movie. Search uses bounded ranked candidate and result windows, not a
minimum-similarity cutoff, so a globally uncompetitive movie may be absent;
select that movie in **All movies** when complete movie-specific recall matters.
The responsive grid initially shows three complete rows and can reveal more of
the 48-result production window.

The main bar remains a broad scene search. **Match by** adds up to three total
search parts across the main bar and the explicit **Scene**, **Words**, **Look**,
**Composition**, and **Mood** facets. Type into a facet, drag a result onto one,
or use a result's **Use in search** menu. The result-card **Composition** action
remains a one-click shortcut. Composition supplies the mandatory candidate
set; other active parts can rerank it but cannot introduce visually unrelated
shots. Result-source composition search keeps the cross-film discovery default
to prevent the source movie's style from consuming the bounded shortlist.

The image icon remains a separate uploaded-reference workflow for JPEG, PNG,
or WebP stills and can take the main bar as a text constraint. Its active chip
replaces the facet rail; clear it, or deliberately use one of its results as a
new facet source, to return to modular search. Uploading a reference clears
facet clauses, so the interface does not imply an unsupported combination.
Look and composition use the existing local frame index; composition adds a
learned 6x6 spatial feature grid. Neither calls OpenAI or Gemini or requires
re-ingestion. Treat composition as coarse framing and position similarity, not
exact subject-relation, skeletal-pose, or motion matching.

Hover a result and use its bookmark action to save that source moment. Saved
scenes persist in `paths.state_dir` independently of search-index repair or a
compatible reingest; the same action is available in the player, and
unavailable scenes remain listed until explicitly removed.

## Optional retrieval comparison

```bash
uv run python -m pipeline.eval.experiment run \
  --queries pipeline/eval/fallen_angels_queries.yaml \
  --variants pipeline/eval/variants.yaml \
  --output pipeline/eval/runs/fa-001.yaml
uv run python -m pipeline.eval.experiment score pipeline/eval/runs/fa-001.yaml
```

Use `pipeline/eval/compositional_queries.yaml` with the same command to compare
paired left/right, foreground/background, subject-relation, and negative-space
prompts across the whole indexed library.

The experiment runner records code/config/corpus/index provenance, separates
warmup from measured latency, runs true hybrid/image/text/lexical ablations,
and pools their candidates with blank 0–3 human grades. It never invents
relevance labels: quality metrics remain unavailable until a complete pooled
query is human-judged. Pass `--judgments-from` on later runs to preserve only
judgments whose query, unit, film, and source timestamps still match.
Machine-owned snapshot evidence is checksummed while grade/flags/note remain
editable. Baseline runs require a clean Git worktree; `--allow-dirty` exists
only for explicitly non-reproducible diagnostics. Local run snapshots live in
the ignored `pipeline/eval/runs/` directory; later snapshots record the hash of
their `--judgments-from` input without treating human grading as a code change.
Use `--limit 100` for candidate-recall experiments; this deeper evaluation
window is independent from the 48-result production window and 12-result UI
display batch.
