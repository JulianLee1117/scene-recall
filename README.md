# scene-recall

Semantic search over your film library: describe a scene in plain English and get the matching shots back.

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

# Configure the web frontend
cp web/.env.local.example web/.env.local
# Edit web/.env.local if your API runs on a non-default port

# Edit config.yaml with your paths and model preferences
```

## Run

Start the API server and the Next.js dev server in separate terminals:

```bash
# Terminal 1 — FastAPI backend
uv run uvicorn pipeline.api.main:app --reload

# Terminal 2 — Next.js frontend
cd web && npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

## Ingest a film

```bash
python -m pipeline.cli ingest path/to/film.mkv
```

The pipeline runs: probe → dialogue → shots → keyframes → embed → annotate → index.
Re-running on the same file skips cached stages automatically.

## Eval

```bash
python -m pipeline.cli eval
```

Runs the retrieval evaluation suite and prints MRR / Recall@k metrics.
