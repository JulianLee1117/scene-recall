# scene-recall

Semantic search over your film library: describe a scene in plain English and get the matching shots back.

The measured search baseline and staged multimodal target architecture are
documented in [docs/search-architecture.md](docs/search-architecture.md).

## Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/) (package manager)
- CUDA 12.8 (GPU recommended; CPU falls back automatically)
- [ffmpeg](https://ffmpeg.org/) on your `PATH`
- [Ollama](https://ollama.com/) running locally
- Node.js 20+

## Setup

```bash
# Install Python dependencies
uv sync --dev

# Configure the annotation provider
cp .env.example .env
# Add OPENAI_API_KEY to .env (or GEMINI_API_KEY if using Gemini)

# Configure the web frontend
cp web/.env.local.example web/.env.local
# Edit web/.env.local if your API runs on a non-default port

# Edit config.yaml with your paths and model preferences
```

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
removed, rename the file as `Title (Year) [Edition].ext`, move it into `films`
(an instant same-drive move), and ingest that final path. Omit `[Edition]` when
there is no confirmed cut or release variant. Use ` - ` in place of a colon,
which Windows filenames do not allow.

Do not rename or move a source after ingestion. If relocation is necessary,
use `relink-film` below. `films` is scanned for unindexed source discovery;
published library records come from the database. The database, vectors,
keyframes, and previews remain in the internal-drive `assets_dir` for fast
interactive search.

```bash
uv run python -m pipeline.cli ingest "V:/scene-recall/films/Film (2001).mkv"
```

The pipeline runs: probe → dialogue → shots → keyframes → embed → annotate →
shot index → frame index. Completed hosted annotations are cached per shot under
that film's asset directory. If an ingest is interrupted, re-running the same
file reuses matching dialogue, media, and annotation artifacts instead of
paying for those annotation calls again. Changing the annotation model, prompt,
settings, or keyframe content intentionally invalidates the relevant cache.

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

Once more than one film is indexed, use the compact **All movies** control next
to **Debug** to search one movie, several movies, or the whole library. The
picker gains title search automatically as the library grows.

For a small composition-matching experiment, click the image icon in the
search field and choose a JPEG, PNG, or WebP still. You can also hover any
result and click **Similar** to use that exact keyframe. This searches the
existing local frame index, then compares a learned 6x6 spatial feature grid;
it does not call OpenAI or Gemini and does not require re-ingestion. Treat the
current result as framing/position similarity, not yet as exact skeletal pose
or motion matching.

## Eval

```bash
python -m pipeline.eval.experiment run \
  --queries pipeline/eval/fallen_angels_queries.yaml \
  --variants pipeline/eval/variants.yaml \
  --output pipeline/eval/runs/fa-001.yaml
python -m pipeline.eval.experiment score pipeline/eval/runs/fa-001.yaml
```

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
