# Cinema Search — Technical Specification

*Semantic search engine for movie scenes. "Pinterest for cinema."*
*Spec date: June 2026. Solo dev, local-first, RTX 5070 Ti (16GB), corpus of ~13 films growing to hundreds.*

---

## 1. System overview

```
                         INGESTION (offline, per-film)
 film.mkv ─► demux ─► dialogue extraction ─► shot detection ─► scene grouping
                                                  │
                          ┌───────────────────────┤
                          ▼                       ▼
                  keyframes + previews      sub-segmentation (long takes)
                          │
                          ▼
              visual embeddings (PE core)
                          │
                          ▼
              within-scene dedup (clustering)
                          │
                          ▼
        motion features (optical flow + CamReasoner/Qwen3-VL)
                          │
                          ▼
     two-pass VLM annotation (scene narrative ► shot detail)
                          │
                          ▼
                  LanceDB index (vectors + FTS + metadata)

                         QUERY (online)
 query ─► router (intent + filters) ─► multi-channel retrieval ─► RRF fusion
              │                              │
              │                    [dense: annotation emb]
              │                    [dense: PE text→image emb]
              │                    [lexical: BM25 dialogue+annotations]
              ▼                              │
        image-as-query ──────────────► optional rerank ─► cluster-aware grid
```

Two processes: a **Python ingestion/retrieval service** (FastAPI) and a **Next.js frontend**. All state in LanceDB + flat files. No external infra.

---

## 2. Component specs

Each component: **primary choice → rationale → fallback → swap trigger** (the observable failure that justifies switching).

### 2.1 Source handling & demux

**Primary:** ffmpeg. Probe with `ffprobe` (streams, fps, duration, embedded subs). Extract audio to 16kHz mono WAV for ASR. Extract embedded subtitle streams if present (`-map 0:s`).

Source files are immutable. Everything derived lives in a per-film asset directory keyed by a content hash of the file, so re-ingesting the same film is idempotent and a replaced encode (different rip of the same film) is detected.

**Note on timestamps:** all downstream timestamps in *seconds relative to the source file*, stored as float64. Never frame numbers (VFR sources exist) — convert at the edges.

### 2.2 Dialogue: subtitles + ASR

**Primary:** embedded SRT/ASS extraction when available (most rips have them; they're human-made and more accurate than ASR for film audio with score/effects). Normalize to per-line records with start/end times.

**Fallback when no subs:** faster-whisper `large-v3` (or `distil-large-v3` for 2–3x speed) on the 5070 Ti, with word-level timestamps via WhisperX-style alignment. Film audio is adversarial for ASR (music beds, whispering, accents — *Hiroshima Mon Amour* is French, *Yi Yi* Mandarin/Hokkien, *Lily Chou-Chou* Japanese); prefer real subs whenever they exist, and prefer **English subs of foreign films** over transcribing the original language, since queries will be in English. Store original-language text too when available — you speak Mandarin/Hokkien and may want to search Yi Yi's dialogue natively.

**Swap trigger:** if ASR word error visibly poisons dialogue search on a no-subs film, just go find subs (OpenSubtitles) instead of fighting the model — alignment of external SRTs to your encode via audio fingerprint offset (e.g., `ffsubsync`) is a solved problem.

### 2.3 Shot boundary detection

**Primary:** TransNetV2 (PyTorch reimplementation, `transnetv2-pytorch`). Still the practical standard in 2026; fast, well-validated on cinematic content, runs at far faster than realtime on the 5070 Ti.

**Known limits to engineer around (don't wait to hit them):**
- F1 on hard benchmarks is ~0.75–0.82 with imprecise transition localization, especially dissolves. **Pad clip extraction by ±0.25s** and snap previews inward by ~3 frames so boundary slop never shows a flash of the neighboring shot.
- **Flash/strobe false positives** (*Enter the Void* will be a worst case): post-filter cuts where the "new shot" re-matches the previous shot's embedding within <1s — merge them back.
- **Long takes** (*Mirror*, *2001*, *Before Sunrise* walking shots): a single 3-minute take is one "shot" but many searchable moments. **Sub-segment any shot >20s** at motion/content change points: compute per-second PE embeddings within the shot, split where cosine distance between adjacent windows exceeds a threshold. Sub-segments share a `shot_id` parent so the UI can show them as one shot with multiple entry points.

**Fallbacks:** AutoShot (CVPR'23, +4.2% F1 over TransNetV2 on short-form; less relevant to cinema but available) or OmniShotCut (2026 shot-query transformer — stronger transition localization, but young; adopt only if weights + inference are turnkey).

**Swap trigger:** spot-check 2 films by skimming detected boundaries against the timeline. If you're hand-fixing >5% of cuts, try AutoShot; if dissolve-heavy films (*Wild Strawberries* dream sequences, *Hiroshima Mon Amour* montage) are systematically butchered, that's the OmniShotCut signal.

### 2.4 Scene grouping

Shots → scenes, so narrative annotation has the right scope. This is the mushiest component; aim for "good enough," not correct.

**Primary (heuristic, no ML):** greedy merge of adjacent shots when ANY of:
1. visual continuity — min pairwise PE-embedding similarity between shot keyframes above threshold (shot/reverse-shot pairs are near-identical alternations),
2. dialogue continuity — subtitle lines flow across the boundary with <2.5s gap,
3. audio continuity — same music cue spanning the cut (RMS/chroma similarity; optional, add only if 1+2 underperform).

Cap scenes at ~5 min; force a break at large silent visual discontinuities.

**Fallback:** LLM-assisted — feed Gemini the subtitle transcript with timestamps and ask it to segment into scenes with one-line summaries. Surprisingly strong, costs cents, and you need the per-scene summary anyway (§2.8). Honest expectation: you may end up running the heuristic first and the LLM pass as refinement.

**Swap trigger:** scene-context annotations that reference the wrong scene (visible as narrative annotations that don't match the shot). If >10% of spot-checked shots have wrong narrative context, scene grouping is the culprit before the annotator is.

### 2.5 Keyframes & previews

Per shot (or sub-segment):
- **Keyframes:** 1 frame for shots <2s, 3 (25%/50%/75%) otherwise. Save as WebP q=82, max 1280px wide. The 50% frame is the canonical thumbnail.
- **Hover preview:** 2–4s WebM (VP9, no audio, 480px, ~24fps, CRF 35), centered on the most motion-dense second of the shot. Generated at ingest; this is the single biggest contributor to the UI feeling alive.
- **No pre-cut clip library.** Clips are extracted on demand (§2.12).

Storage budget: ~2,000 shots/film → after dedup ~400 indexed units → ~25–60MB of previews + thumbs per film. Hundreds of films fits on one SSD comfortably.

### 2.6 Visual embeddings

**Primary: Meta Perception Encoder — PE core L/14 (or B/16 if VRAM pressure).**
Rationale: PE core outperforms SigLIP-2 at comparable scales on zero-shot retrieval, and its video-caption-aligned fine-tuning produced large gains on video retrieval *while remaining a frame-level encoder* — which is exactly this architecture (embed keyframes, no temporal-attention serving infra). Apache-ish licensing via `facebookresearch/perception_models`; checkpoints on HF.

**Fallback: SigLIP-2 So400M (`google/siglip2-so400m-patch14-384`).** Slightly behind on retrieval but the HF ecosystem support is frictionless (`Siglip2Model` in transformers, battle-tested), and it's known-good on stylized/animated content. If PE integration costs you more than an evening, take the 2% hit and move on — the embedding model is *not* where this project wins or loses (annotation quality is).

**Mechanics:** embed all keyframes per shot, store each, and also store the mean vector as the shot representative. Text-side queries use the paired text encoder of whichever model you pick (this is what makes paste-an-image search free — same space). Normalize everything; cosine metric.

**Future (do not build now):** PE-AV (Meta, Dec 2025) embeds audio+video+text in one joint space — the path to "hum a vibe / paste a song section, get scenes." Park it; the text+image space covers v1–v2.

**Swap trigger:** run the gold-query eval (§5) with both encoders on the first 3 films — it's a config flag, ~30 min of compute. Keep the winner, write down the delta (free content for the post).

### 2.7 Dedup & curation scoring

**Dedup (within-scene):** agglomerative clustering on shot-representative embeddings within each scene, cosine distance threshold ~0.10–0.15 (tune on Yi Yi — dialogue-heavy, worst case for alternating heads). Each cluster gets one **representative** (longest duration wins ties); members stay in the DB linked by `cluster_id`, excluded from default retrieval but reachable from the shot-detail "other takes" view. Expect 2,000 shots → 300–500 indexed units; *this* is the answer to the 5%-searchable problem, not deletion.

**Curation scoring (rank, never filter):** per indexed unit store
- `duration_score` (very short shots downweighted),
- `motion_score` (mean optical-flow magnitude — both extremes are informative, not bad),
- `aesthetic_score` — **deferred, with a warning:** LAION-style aesthetic predictors are photo-trained and behave erratically on animation (*Spirited Away*) and B&W (*Mirror*, *Wild Strawberries*, *Hiroshima Mon Amour*) — a third of your corpus. If you add one later, normalize per-film, never globally. The VLM annotation pass can output a 1–5 "frame-worthiness" instead, which is calibrated to *your ontology* rather than LAION's Instagram prior.

Final ranking multiplies retrieval score by a mild prior from these (configurable weights, default nearly flat). Filtering happens only via explicit user filters.

### 2.8 Annotation (the heart of the system)

**Two-pass scheme, Gemini API, Batch mode (50% discount), context-cached system prompt (cache reads = 10% of input price).**

**Pass A — scene narrative (per scene, ~80–150/film):**
Input: scene's subtitle window + the aligned chunk of the film's plot summary (Wikipedia; align by act position and dialogue overlap — fuzzy is fine) + film metadata (title, director, year).
Output (structured JSON): `scene_summary`, `narrative_beat` ("the moment she realizes…", "first meeting", "confrontation"), `emotional_arc`, `characters_present`.

**Pass B — shot detail (per indexed unit, ~300–500/film):**
Input: 3-keyframe strip **or the actual shot clip** (see model note below) + Pass A scene context + dialogue lines within the shot.
Output (structured JSON, the ontology):

```json
{
  "literal_description": "…",
  "searchable_caption": "dense free-text written for embedding: visual + mood + situation in one paragraph",
  "mood": ["melancholy", "longing"],
  "lighting": "practical neon, low-key, heavy color contrast",
  "color_palette": ["teal", "magenta", "sodium orange"],
  "shot_size": "medium close-up",
  "composition": "subject frame-left, negative space right, shallow focus",
  "camera_movement": "slow push-in",
  "setting": "city street at night, rain",
  "time_of_day": "night",
  "subjects": ["woman, 30s, alone"],
  "narrative_context": "inherited + refined from Pass A",
  "directorial_register": ["Wong Kar-wai longing", "urban isolation"],
  "frame_worthiness": 4
}
```

The `searchable_caption` is the primary dense-retrieval document; the structured fields power filters and the secondary lexical channel. `directorial_register` is the field that makes vibe queries work — the prompt must explicitly license the model to use film-critical vocabulary and director references, with a dozen few-shot examples in the cached system prompt. **The few-shot examples are the highest-leverage hand-labeling you will do in this project** — write ~15 of them yourself, well.

**Model choice (June 2026 lineup):**
- **Primary: Gemini 3 Flash** ($0.50/$3.00 per M tokens) — best quality/cost for nuanced aesthetic register.
- **Cost floor: Gemini 3.1 Flash-Lite** ($0.25/$1.50) or 2.5 Flash ($0.15/$0.60) — run the A/B on 50 shots before assuming you need the bigger model.
- The lineup churns every few months; the pipeline must treat the annotator as a config string, and the eval (§5) is how every model swap gets adjudicated. Don't hardcode anything model-specific beyond the prompt.

**Frame strips vs. video-native:** Gemini ingests video natively (roughly ~258 tokens per sampled frame at default 1fps; a low-resolution media mode cuts this further — verify current rates at implementation time). For shots averaging 4s that's ~1K tokens of video vs ~800 for a 3-frame strip — basically a wash on cost, and the clip lets the model *see* the camera movement instead of inferring it. **Recommendation: frame strips for v1 (simpler, deterministic), video-native as the first quality experiment after the eval harness exists**, because it likely improves `camera_movement` and motion-dependent mood for near-zero cost.

**Cost estimate per film (Pass A + B, Gemini 3 Flash, batch):** ~500 units × ~1.2K input + 350 output tokens ≈ $1.20–2.50/film. At 2.5 Flash-Lite it's pennies. Non-issue.

### 2.9 Camera movement & motion features

The landscape changed in late 2025/2026 — plan accordingly:

- **CameraBench** (NeurIPS 2025 D&B) defined the cinematography-grounded taxonomy: translation (dolly/pedestal/truck), rotation (pan/tilt/roll), zoom, static, plus steadiness. **Adopt this taxonomy verbatim as the `camera_movement` vocabulary** — don't invent your own.
- **CamReasoner-7B** (2026, Qwen2.5-VL backbone): 78.4% accuracy, beats GPT-4o by ~12 points, fits on the 5070 Ti. Even it confuses dolly-in vs zoom-in, so treat labels as ~80%-reliable signal, good for filters and ranking, not ground truth.

**v1:** let Gemini's Pass B fill `camera_movement` from the frame strip (mediocre — it can't see motion well from 3 frames) plus cheap optical-flow stats (Farneback or RAFT-small): mean magnitude, direction histogram, global-vs-local motion ratio (proxy for camera-move vs subject-move), shakiness. Store the raw stats — they're reusable forever.

**v1.5 (first deep experiment):** run CamReasoner-7B (or Qwen3-VL-8B prompted with the CameraBench taxonomy) locally over all shot clips as a batch job; overwrite the `camera_movement` field. This is also where the **open-contribution opportunity** now lives: nobody has published camera-motion-aware *retrieval* — "find slow push-ins on a lone character" as a working search. The model exists; the index integration doesn't.

### 2.10 Index & storage

**Primary: LanceDB** (embedded, single directory, no server). One main table, `units`:

| field | type | notes |
|---|---|---|
| `unit_id` | str | shot or sub-segment id |
| `film_id`, `scene_id`, `shot_id`, `cluster_id` | str | hierarchy + dedup linkage |
| `t_start`, `t_end` | f64 | seconds in source |
| `is_representative` | bool | dedup gate for default retrieval |
| `img_vec` | vector | PE/SigLIP2 mean keyframe embedding |
| `txt_vec` | vector | text-embedding of `searchable_caption` + narrative |
| `annotation` | json/struct | full ontology (§2.8) |
| `dialogue` | str | concatenated lines (FTS-indexed) |
| `caption_fts` | str | searchable_caption + flattened tags (FTS-indexed) |
| `motion` | struct | flow stats + camera_movement |
| `scores` | struct | duration/motion/frame_worthiness |
| flat filter cols | str/bool | film, year, director, is_bw, aspect_ratio, time_of_day, shot_size |

Plus `films`, `scenes` tables. LanceDB gives vector search + native BM25 FTS + SQL-ish filtering in one library, versioned datasets (free re-index rollback), and zero infra. At hundreds of films (~100–250K representative units) brute-force cosine is still fast; add IVF-PQ only if p95 query >150ms.

**Text embedder for `txt_vec`:** local **Qwen3-Embedding-0.6B** (free, strong, runs alongside everything else on the GPU) — or a hosted embedder if you'd rather not manage it; cost either way is trivial. Whichever you pick, freeze it; changing it means re-embedding everything (cheap, but remember to).

**Swap trigger:** you almost certainly never swap this. If multi-user hosting happens someday, that's Qdrant/pgvector territory — a different product.

### 2.11 Query path

**Router:** every query passes through a small LLM emitting structured output:

```json
{
  "intent": "vibe | dialogue_quote | plot_moment | visual_specific | hybrid",
  "filters": {"film": null, "is_bw": null, "time_of_day": "night", "camera_movement": null},
  "dense_query": "rewritten for embedding search",
  "lexical_query": "keywords for BM25",
  "weights": {"img": 0.4, "txt": 0.4, "lex": 0.2}
}
```

**Primary:** local Qwen3-class 4B–8B instruct via Ollama/vLLM — ~50ms, free, offline (the local product should work on a plane). **Fallback:** Gemini Flash-Lite if local routing quality annoys you. Router failure mode must be graceful: on any parse error, fall back to flat hybrid search with default weights.

**Channels, run in parallel:**
1. `dense_query` → text-embedder → kNN over `txt_vec` (annotation space — carries vibe, narrative, register),
2. `dense_query` → PE/SigLIP2 *text* encoder → kNN over `img_vec` (raw visual space — carries composition, color, literal content),
3. `lexical_query` → BM25 over `dialogue` + `caption_fts` (exact phrases, names, quoted lines).

**Fusion:** reciprocal rank fusion with router weights, multiplied by the mild curation prior (§2.7). **Cluster-aware diversification:** max 2 results per `scene_id` and 4 per `film_id` in the top 30, so one neon-drenched film doesn't monopolize a neon query.

**Rerank (off in v1, behind a flag):** Qwen3-VL-8B local, given the query + top-30 thumbnails, asked to score relevance 1–10. Adds ~1–3s. Turn on only if the eval shows fusion ordering is the bottleneck (predict: it mostly won't be; recall/annotation will be).

**Image-as-query:** paste/drop a still → PE image embedding → kNN over `img_vec`, skip router. Also powers "more like this" from any result. Free, magical, demo gold.

### 2.12 Clip extraction & playback

- **Jump-to-timestamp:** in-app `<video>` over the source file (FastAPI serves with HTTP range support), seek to `t_start − 1s`. For codecs browsers refuse (some 10-bit HEVC rips), lazily generate a per-film 1080p H.264 proxy on first playback attempt and remember it.
- **Extract clip:** ffmpeg stream copy snapped to keyframes with ±0.25s pad (instant, but cut points land on GOP boundaries) and an "exact cut" option that re-encodes just that range (NVENC on the 5070 Ti, ~1–2s for a 10s clip). Output lands in a per-board export folder — drag straight into Resolve.
- **Board export:** later, one button that extracts every clip on a board → folder (+ optionally an EDL/OTIO file). This is the bridge from "search toy" to "editing tool."

### 2.13 Frontend

**Stack:** Next.js (App Router) + Tailwind, talking to FastAPI. Local-first; Tauri wrapper is a cosmetic decision for later, the web app is the product.

**Screens:**
1. **Search/home** — the product. Centered oversized search bar on a near-black canvas; results as a justified/masonry grid of stills preserving native aspect ratios (scope-vs-academy variety in your corpus is itself beautiful — lean into it). Hover → silent WebM preview + film title/timestamp overlay. Sub-100ms search-to-grid is the polish metric that matters most. Filters as quiet pill toggles (film, B&W, night, movement), not a sidebar of dropdowns.
2. **Shot detail** (modal over grid, not navigation) — large preview loop, annotation rendered as elegant tags + one-line narrative context (never raw JSON), dialogue snippet, actions: *jump to timestamp · extract clip · more like this · save to board*, and an "other takes" strip (dedup cluster members).
3. **Boards** — Pinterest mechanic. Grid of saved shots per board, board-level export. Every save is logged as taste data (query, shown set, chosen unit) for the future reranker.
4. **Library** — drag a film in, watch pipeline stages stream (probe → subs → shots → embed → dedup → annotate → index) with per-stage timing. Re-run any stage per film (annotation will be re-run often as prompts improve — make this one click).

**Design notes:** the Arc/Linear/Are.na bar means restraint — one accent color, generous whitespace, film stills provide all the color; system-ui or a single grotesque (Inter/Söhne-alike); no cards-with-shadows, the image *is* the card. Loading states everywhere (annotation streaming in, previews lazy-loading) — perceived speed is the aesthetic.

---

### 2.14 Configuration

`config.yaml` is the single source of truth for every tunable value. No model names, paths, or thresholds are hardcoded anywhere in the pipeline.

```yaml
paths:
  films_dir: D:/films             # source files (immutable, any drive)
  assets_dir: D:/cinema-assets    # derived: keyframes/, previews/, proxies/, db/

models:
  visual_encoder: pe_core_l14     # pe_core_l14 | siglip2_so400m
  text_encoder: qwen3-embedding-0.6b
  annotator: gemini-3-flash       # gemini-3-flash | gemini-3.1-flash-lite | gemini-2.5-flash
  router: qwen3:8b                # ollama model tag; qwen3:4b if VRAM is tight

thresholds:
  shot_dedup_cosine: 0.12         # within-scene cluster merge distance
  scene_visual_sim: 0.75          # adjacent-shot embedding similarity for merge
  scene_dialogue_gap: 2.5         # seconds; subtitle continuity merge
  scene_max_duration: 300         # seconds; force scene break
  subsegment_min_duration: 20     # seconds; shots longer than this get sub-segmented

retrieval:
  weights: {img: 0.4, txt: 0.4, lex: 0.2}
  diversity: {max_per_scene: 2, max_per_film: 4}
  rerank_enabled: false

scoring:
  duration_weight: 0.05
  motion_weight: 0.05
  frame_worthiness_weight: 0.10
```

All pipeline stages load config once at startup via a `Config` dataclass. Adding a new tunable = one line in `config.yaml` + one reference in code.

---

## 3. Repo structure

```
cinema-search/
├── pipeline/                    # Python — ingestion & retrieval service
│   ├── ingest/
│   │   ├── probe.py             # ffprobe, hashing, asset dirs
│   │   ├── dialogue.py          # sub extraction / faster-whisper / sync
│   │   ├── shots.py             # TransNetV2 + flash-merge + sub-segmentation
│   │   ├── scenes.py            # grouping heuristics (+ LLM refine)
│   │   ├── media.py             # keyframes, hover previews (ffmpeg)
│   │   ├── embed.py             # PE core / SigLIP2 (flag-switchable)
│   │   ├── dedup.py             # within-scene clustering
│   │   ├── motion.py            # optical flow stats; later CamReasoner
│   │   └── annotate.py          # Gemini 2-pass, batch, cached prompts
│   ├── index/
│   │   ├── schema.py            # LanceDB tables
│   │   └── writer.py
│   ├── search/
│   │   ├── router.py            # local LLM structured routing
│   │   ├── retrieve.py          # 3 channels + RRF + diversification
│   │   └── rerank.py            # flag-gated VLM rerank
│   ├── api/                     # FastAPI: /search /unit /clip /ingest /boards
│   ├── eval/
│   │   ├── gold_queries.yaml    # the eval set — versioned, grows forever
│   │   └── run_eval.py          # hit@5, hit@10, MRR; per-intent breakdown
│   ├── tests/
│   │   ├── fixtures/            # gitignored; short test clip lives here
│   │   └── test_<stage>.py      # one test file per ingest stage
│   └── cli.py                   # `cinema ingest film.mkv`, `cinema eval`, …
├── web/                         # Next.js
│   └── app/ components/ lib/
├── config.yaml                  # model choices, thresholds, weights — everything tunable
└── assets/                      # per-film: keyframes/ previews/ proxies/ (gitignored)
```

Principle: **every model and threshold is a config value**, and `cinema eval` is the referee for every change. That's what "iteration at an abstract pace" needs structurally.

---

## 4. Phases (unordered time, ordered dependencies)

**Phase 1 — vertical slice + baseline eval.**
3 films (Yi Yi, 2001, Grand Budapest — dialogue-heavy / long-take / stylized). Probe→subs→TransNetV2→keyframes→PE embeddings→LanceDB. *Naive* one-pass captions (deliberately — you need the baseline number). Bare Next.js grid + hover + click-to-timestamp. Write 20 gold queries with known answers; record hit@5. **Exit criterion: you can type a vibe and watch what happens, and you have a number.**

**Phase 2 — annotation quality sprint.** Two-pass annotation with full ontology + few-shot examples, scene grouping, plot-summary injection, dedup, sub-segmentation. Re-run eval; the delta is the headline result. Run the PE-vs-SigLIP2 and Flash-tier A/Bs here. Ingest the remaining ~10 films; expand gold queries to ~60 spanning all five intents.

**Phase 3 — query intelligence.** Router, three-channel fusion, diversification, image-as-query, filters. Eval gets per-intent breakdown (dialogue queries should be near-perfect; vibe is the fight).

**Phase 4 — product.** Design pass on search/detail, boards, clip extraction, library/ingest UX, taste logging. Record the demo video.

**Phase 5 — depth (pick by interest, all optional):**
- camera-movement index (CamReasoner batch pass + movement-aware queries) — the novel public artifact;
- annotation distillation → Qwen3-VL-8B local (free ingestion; required for any public bring-your-own-films release);
- taste reranker from board-save logs;
- video-native annotation A/B;
- PE-AV audio-reference search;
- publish the eval set + methodology ("CineBench"-shaped post).

---

## 5. Eval harness (non-optional, built in Phase 1)

`gold_queries.yaml`: each entry = query text, intent label, acceptable answers as `(film, t_start..t_end)` ranges (multiple OK). Metrics: hit@5, hit@10, MRR, per-intent. Rules: never delete a query (mark deprecated); every pipeline change ships with a before/after eval line in the commit message. ~20 queries Phase 1 → ~60 Phase 2 → grows with every miss you notice in real usage ("that should have worked" → new gold query, immediately).

This is the instrument that converts "iterate and adjust depending on how well things work" from vibes into measurements — and it doubles as publishable content.

---

## 6. Decision-point summary

| Component | v1 choice | Fallback | Swap trigger |
|---|---|---|---|
| Shot detection | TransNetV2 | AutoShot / OmniShotCut | >5% hand-fixed cuts; dissolve butchery |
| Visual embedding | PE core L/14 | SigLIP-2 So400M | head-to-head eval on 3 films (cheap — run it) |
| Text embedding | Qwen3-Embedding-0.6B local | hosted embedder | quality annoyance only |
| Annotator | Gemini 3 Flash (batch) | 3.1 Flash-Lite / 2.5 Flash | 50-shot A/B + eval delta per model |
| Annotation input | 3-frame strips | video-native clips | camera_movement / motion-mood quality |
| Scene grouping | heuristic merge | LLM segmentation of transcript | >10% wrong narrative context |
| Router | local Qwen3 4–8B | Gemini Flash-Lite | routing quality annoyance |
| Rerank | off | Qwen3-VL-8B local | eval shows fusion ordering is bottleneck |
| Camera movement | flow stats + Gemini guess | CamReasoner-7B batch pass | Phase 5 by design |
| Index | LanceDB brute-force | + IVF-PQ | p95 query >150ms |

## 7. Budget reality check

Annotation: ~$1.50–2.50/film at Gemini 3 Flash batch → 13 films ≈ $30; 200 films ≈ $400 (or ~$40 at Flash-Lite, or ~$0 post-distillation). Everything else (embeddings, ASR, routing, motion, previews) runs free on the 5070 Ti. Storage: ~50MB derived assets/film + sources. Comfortably inside a few hundred dollars through v2.

---

## 8. Dev environment (Windows, RTX 5070 Ti)

**Python — uv:**
```bash
winget install astral-sh.uv
uv init cinema-search && cd cinema-search
# PyTorch with CUDA 12.8 (Blackwell / sm_100 support for RTX 5070 Ti)
uv add torch torchvision --index https://download.pytorch.org/whl/cu128
uv add fastapi uvicorn lancedb faster-whisper transformers
# remaining deps added per stage
```
Use Python 3.12. `uv.lock` is committed; teammates (or future-you) get a reproducible env with `uv sync`.

**CUDA:** Install CUDA Toolkit 12.8 + cuDNN 9.x from nvidia.com. Verify after:
```bash
python -c "import torch; print(torch.cuda.get_device_name(0))"
# → NVIDIA GeForce RTX 5070 Ti
```

**ffmpeg:** `winget install Gyan.FFmpeg` (the gyan.dev full build — includes NVENC for hardware H.264 clip export and all demuxers you'll need for film rips). Restart shell so it's on PATH.

**Ollama (local router):** `winget install Ollama.Ollama` → `ollama pull qwen3:8b`. GPU offloading is automatic on Windows with CUDA.

**Env vars** (set in user profile or `.env` at project root, never committed):
```
GEMINI_API_KEY=...
CINEMA_CONFIG=D:/cinema-assets/config.yaml   # optional override
```

**First-run check — write this before anything else:**
```bash
python -m pipeline.check_env
# asserts: CUDA visible, ffmpeg on PATH, Ollama responsive, Gemini key set, assets_dir writable
```
One script, catches 90% of setup problems. Especially useful after CUDA driver updates.

**Pipeline regression tests:**
```bash
uv run pytest pipeline/tests/ -x
```
Keep a ~30s test clip at `pipeline/tests/fixtures/test_clip.mkv` (gitignored — generate it once with ffmpeg from any film you have). Each ingest stage gets one test asserting expected output shape: shot count within ±2, embedding dimensions, LanceDB row count. Catches code bugs; the eval harness (§5) covers quality.
