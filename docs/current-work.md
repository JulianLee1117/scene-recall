# Current work

Temporary execution plan, re-planned 2026-09-27. README and the architecture
contract stay authoritative until each phase lands with its own ADR/contract
update (see AGENTS.md). The previous plan, including its operational notes, is
archived verbatim in
[history/current-work-until-2026-09-27.md](history/current-work-until-2026-09-27.md).
Remove this file and its AGENTS.md pointer when the plan is complete.

## Progress (2026-09-28)

Landed:
- ADR-0093 evidence v2;
- ADR-0094 search v2;
- ADR-0095 last complete generation;
- ADR-0096 editor harness v2 (opt-in).

- **Evidence v2**, all passes: open metadata, synced OpenSubtitles, Gemini
  understanding, local measurement, hero frames and priors. New films run every
  pass after ingest. Compilation serves the previous profile while a new one
  backfills.
  - Clips that Gemini's filter refuses get a terminal receipt instead of looping.
    `understand --retry-refused` recovers them in quarter-size pieces.
  - Proxies skip non-reference frames (about 1.8x cheaper NVDEC).
  - Near-black stretches (`dark_spans`) are compiled from measured brightness.
- **Search v2**:
  - evidence views, a quote channel, priors and presets;
  - scene cards, hero thumbnails, resident vectors and a cross-encoder rerank;
  - query signals, per-film Highlights and Hidden gems browsing;
  - interaction logging.

  Search keeps its last complete snapshot while evidence publishes. The rerank
  has a 1 s budget and rests while the GPU is full.
  - Eval (30 known items): MRR 0.97, hit@1 29.
  - Latency: about 0.7 s without the rerank under GPU contention, about 0.9 s
    with it on an idle GPU.
- **Editor harness v2** (`lab.harness: v2`, opt-in):
  - music map, concept acts, evidence pools;
  - beat-lattice assembly, where action peaks land on accents and variety covers
    scenes, films, looks and recent similarity;
  - a sequence review;
  - an optional critique loop (`lab.harness_critique`) in which Gemini watches a
    rough cut and the edit is re-assembled once.

  Fill gaps also runs through v2, keeping cuts and placed shots. The Footage
  setting (recognizable ↔ fresh) steers editor searches and v2 act fame targets.

  OTIO export ("Resolve timeline") works for any saved edit.
  - Blind Gemini judging of fresh regenerations, each render scored alone at
    4 fps (overall):

    | Project | v1 | v2 |
    |---|---|---|
    | `everything` | 6.0 | 6.0 |
    | `In My Head` | 6.0 | 6.5 |
    | `101` | 6.0 | 6.0 |

    v2 is 2–4x faster. The critique loop showed no measurable gain and stays
    off. Renders are in `assets_dir/lab/renders/eval-*`.
  - The side-by-side judge is position-biased, so it is not used.
- **Cleanup**: Jev/intent experiments and the exact scanner removed; ingest no
  longer queues frozen framing preparation.

Running (started 2026-09-28):
- the library understanding batch (`understand --batch run`, about $100);
- library measurement.

`.tmp/finish-library-v4.sh` waits for them, then runs `understand --retry-refused`,
`highlights`, `synthesize`, `compile` (the new `dark_spans` column migrates in
place), `pipeline.eval.searchset`, then `hero` and a final `compile --no-text`
(log: `.tmp/finish-library.log`).

Afterwards:

- Restart the API and ingest worker so they run the new code. The API then
  holds resident vectors (about 3 GB, GPU when free) and the reranker.
- `python -m pipeline.evidence understand --retry-refused` for any remaining
  refused clips, then `highlights`, `synthesize` and `compile` for those films.
- `python -m pipeline.evidence prune --apply` drops superseded profiles.
- Read the library eval and discovery report.
- Keep subtitle downloads going daily (`subtitles --max-downloads 20`, then
  `refresh-dialogue`) until no film is left.
- Owner: compare harness v2 on your own projects (set `lab.harness: v2`, then
  Regenerate edit; History restores the previous edit). Switch the default once
  it wins.

Next:

- Phase 3 remainder:
  - lyric timing (LRCLIB) for literal lyric treatment;
  - the critique loop's evaluation;
- **Phase 1 boundary audit**, measured on the pilot: hidden cuts in 0.7% of
  shots. Cut times stay evidence; assembly windows never straddle them.
  Splitting canonical units is deferred until a concrete failure.
- Match-cut planning after that, per the owner. Measured subject layout, screen
  direction and camera segments now exist for it.

## Goal

1. Find the moment you remember (description, dialogue, character, event).
2. Surface great footage for an idea — famous *and* forgotten — without junk.
3. Turn a song plus direction into a strong, adjustable edit, finished in Resolve.

The storage/versioning foundation and hybrid retrieval plumbing are sound. The
bottlenecks are evidence (what the index knows about each shot) and ordering
(what comes first). Order of work: evidence, then ranking, then the editor
harness built on the new evidence.

## What was measured (2026-09-27)

Probe scripts and outputs: `pipeline/eval/runs/probes-2026-09-27/` (git-ignored).

- **Evidence.** Every shot was captioned in isolation from 3 low-detail stills by
  `gpt-5.6-luna`, with no dialogue, title or neighbours. "tense" is on 48% of
  shots; camera motion is static/unknown for 84%; no scenes, characters or fame
  signal; `frames.quality_score` is empty.
- **Ranking.** Pure similarity (weighted RRF). The Matrix bullet-dodge is indexed
  with a decent caption but misses the top 8 for a direct description;
  "you talkin' to me" misses Taxi Driver (its subtitle is at 1:06:40), even with
  the exact subtitle wording.
- **Dialogue.** 62 of 153 films use Whisper without word timestamps (6.8% of
  cues exceed 12 s); 15 were transcribed in their original language.
- **Camera motion** (260 shots, RAFT flow + robust affine fit vs stored labels).
  Stored "static" confirmed for 77%; "tracking"/"pan" match measured movement
  only ~30–50%; flow resolves ~64% of "unknown". Visual review: stills-based
  labels miss slow push-ins and read cuts as pans; flow is right when confident
  but fails on chaotic handheld, water/smoke and very dark shots. Several indexed
  "shots" contain a hidden cut.
- **Hosted video understanding** (`gemini-3.8-flash`, 7-minute Matrix chunk,
  2 fps, low media resolution, shot numbers burned into the frames):
  - all 187 shots came back keyed correctly (187/187 peak times inside their own
    shot), with correct characters, story context and 8 coherent scenes;
  - it flagged the bullet-dodge, "Dodge this", the falling shell casings and the
    Morpheus catch as iconic; fame (3 on 4/187) and craft ratings discriminate;
  - its camera labels confused subject motion with camera motion and missed
    subtle pushes;
  - cost about 65k input / 21k output tokens (~$0.13), 4.3 minutes.
- **Frames.** The displayed middle keyframe is rarely much worse than its
  siblings (7.6% under half their sharpness). The better frame is usually one
  that was never sampled.
- **Speed.** 2.5–3.9 s per search. Flat Lance scans take 0.6–0.9 s per channel;
  the same 559k vectors scan in ~2 ms on the GPU.

## Principles

1. Measure locally what is measurable: cuts, camera motion, subject positions,
   faces, audio, beats. Use models for what needs understanding: story,
   characters, fame, intent. Never ask a language model to guess a measurement.
2. Give models structure to key to: burned-in shot IDs, the shot table,
   subtitles and the cast list. Ask them to label known units rather than invent
   timestamps.
3. Keep axes separate: relevance, fame, craft, personal taste and novelty.
   Combine them at ranking time under user control.
4. Hierarchy: film → scene → shot → moment. Motion and action timing are time
   series within a shot, not one label per shot.
5. Keep the evidence/derivation split (ADR-0001). New layers are versioned and
   rebuildable; raw films and subtitles stay untouched.
6. Keep process light. Write an ADR per real pivot and keep contract updates
   concise. One small personal eval set replaces per-experiment frameworks.

## Balancing famous and forgotten footage

- **Two scores per shot.** *Fame* is how widely known the moment or image is.
  *Craft* is how strong it is visually and editorially, regardless of fame. A
  hidden gem is relevant, high-craft and low-fame; low fame alone is not a gem
  (most low-fame shots are coverage). Fame is calibrated within each film, then
  scaled by the film's popularity, so a famous shot from an obscure film can
  still count as a gem library-wide.
- **Search presets.**
  - *Balanced* (default): relevance first. For broad discovery queries, each
    page mixes in iconic shots and gems drawn only from the relevant pool
    (calibrated re-ranking, never pulling in unrelated shots). Specific recall
    queries (quotes, names, detailed descriptions) stay strictly
    relevance-first.
  - *Famous*: boosts fame.
  - *Hidden gems*: demotes iconic shots and requires a craft floor.
  - Badges ("Iconic", "Hidden gem") explain placements.
- **Editor.** A *Recognizable ↔ Fresh* control sets how many iconic anchors go
  on the song's structural peaks (drop, chorus entry, final hit), with gems
  elsewhere. Optionally prefer footage not used in your earlier projects.
- **Film level.** Low-popularity films (IMDb vote counts, Wikipedia pageviews)
  can be favoured in Hidden gems mode ("forgotten films").
- **Later.** Your saves, placements and replacements train a personal taste
  score as a separate axis.

## Phase 0 — Groundwork (days)

- Commit and push the working tree; nothing has been committed since 2026-09-01.
- Owner inputs:
  - OpenSubtitles (account done): create an API key under "API consumers" in
    the profile, then add `OPENSUBTITLES_API_KEY`, `OPENSUBTITLES_USERNAME` and
    `OPENSUBTITLES_PASSWORD` to `.env`. A logged-in free account allows 20
    downloads/day, so the 15 foreign-language films go first and the ~62
    Whisper films take about 4 days;
  - confirmation of the Gemini library spend (see Phase 1).
- Personal eval set:
  - ~30 real queries: remembered moments, looks, moods, quotes, and famous
    versus forgotten requests;
  - 3 songs: Wish, Starjunk and one new;
  - a simple before/after viewer (better / same / worse).
  This is the acceptance check for every phase.
- Apply the Frozen list below.

## Phase 1 — Evidence v2 (ingestion)

Per film, as versioned derivations. The library backfill runs once; new films
get the same passes automatically after ingest. Existing shots, keyframes,
captions and PE embeddings are reused; only the new passes run, plus
re-derivation of units the boundary audit splits. Pilot on 5 films you know
well (for example The Matrix, Taxi Driver, In the Mood for Love, Parasite,
Whiplash), check them against the eval set, then run the library.

1. **Film metadata** (no API keys needed).
   - Wikidata (CC0): IMDb/other IDs, cast with character roles where recorded,
     director, genres, dates.
   - IMDb non-commercial datasets (personal use, local copies allowed):
     principal cast with characters, and vote counts for film popularity.
   - Wikipedia plot (CC BY-SA), Wikiquote quotes, and Wikipedia pageviews as a
     second popularity signal.
   - TMDB is excluded: its API terms prohibit using its data or images "in
     connection with ... a machine learning (ML) or artificial intelligence (AI)
     based Application".
2. **Dialogue v2.**
   - English subtitles for the Whisper films, auto-synced to the audio with
     alass/ffsubsync and checked with the existing validator.
   - Whisper stays as fallback, with word timestamps.
   - Foreign films keep their original-language transcript as a separate view.
   - Assign dialogue to shots by actual cue/word overlap.
   - Index each subtitle line as its own row with exact times, so quote search
     returns the line itself and the editor can trim to it.
3. **Shot boundary audit.**
   - Detect hidden cuts (photometric/flow discontinuity plus a lower-threshold
     TransNetV2 check) and split the affected units.
   - Keep sub-0.5 s shots as flagged micro-shots instead of merging them.
   - Unchanged shots keep their IDs.
4. **Local measurement pass** (one streaming GPU pass per film; hardware decode
   is available).
   - Camera motion as a time series: RAFT flow with a robust fit that excludes
     subject regions, giving segments (static, pan, tilt, push, pull, roll,
     shake) with speed, direction and confidence. Low-confidence segments are
     marked uncertain; nothing is guessed. Validate on ~100 hand-checked strips
     (probe tooling exists).
   - Subject tracks: people and main objects (RF-DETR or similar) at 2–4 fps,
     with normalized position, size and screen direction.
   - Characters: faces are clustered per film, and clusters are named by voting
     against the understanding pass's per-shot character names (no cast photos
     needed).
   - Hero frame per shot, chosen from 2–4 fps candidates by sharpness,
     exposure, faces/eyes, an aesthetic model and distinctiveness. The hero
     frame, plus extra frames for long shots (about one per 2–3 s), joins the
     PE frame index.
   - Look measurements from pixels: dominant colours, brightness, saturation
     and contrast. Shot scale is measured from face/person size relative to the
     frame.
   - Technical quality, in-film distinctiveness, and audio features
     (speech/music/silence, loudness).
5. **Understanding pass** (hosted).
   - Setup: `gemini-3.8-flash` in batch mode, low media resolution, chunks
     bounded by shot count (about 150–200 shots), shot numbers burned in. Use
     2 fps where cutting is fast and 1 fps elsewhere.
   - Before the library run, compare Flash, 3.1 Pro and Flash-Lite on the same
     2 pilot chunks (about $1) and keep the cheapest one that matches on
     characters, story, fame and per-shot actions.
   - Inputs: shot table, subtitles, cast list, synopsis, and the previous
     chunk's scene summary.
   - Per scene: summary, setting, characters, story context, tone.
   - Per shot: characters, action with peak time, emotional beat, notable line
     with its speaker, a short audio cue, fame 0–3, craft 0–3, and a
     "contains a cut" flag that cross-checks the boundary audit. Keep fields
     short: output tokens are about 40% of the cost.
   - Camera labels from this pass are hints only.
   - Estimated cost for the library: ~$85–105 in batch mode at current Flash
     pricing, which doubles after 2026-12-31 (so run it before then). New films
     cost roughly $0.6–1.4 each.
6. **Film synthesis** (text model). Calibrate fame across the whole film from
   the pass output, the Wikipedia plot and quotes aligned to the subtitles.
   Also produce each film's highlights, hidden gems and motifs.
7. **Priors table** per shot: fame, craft, distinctiveness (taste later).

Supersedes ADR-0066's no-world-knowledge producer: story context becomes
first-class evidence rather than a post-retrieval packet. Stored per-shot
`camera_motion` and `mood` stop being primary facets.

## Phase 2 — Search v2

Can overlap with Phase 3 once the pilot evidence exists.

1. New text views (story, action, characters), plus character and film filters
   from trusted metadata.
2. Quotes: dialogue-line rows, phrase search on a position-aware full-text
   index, contraction/fuzzy normalization, and dialogue-first weighting for
   quote-like queries.
3. Query parsing into soft boosts backed by measured fields: character names,
   shot scale ("close-up"), camera movement ("tracking", "push in") and colour
   ("neon", "desaturated"). These are boosts, never hard filters, unless the
   query names a film or character explicitly.
4. Framing v2: match the measured subject layout (count, positions, sizes,
   screen direction), optionally with camera motion, plus visual similarity.
   This replaces the 6x6 embedding grids.
5. Priors step with calibrated famous/gems mixing and the three presets.
6. Optional visual re-rank of the top ~50 behind a toggle, kept only if it wins
   on the eval set. Options: Qwen3-VL-Reranker locally, or GPT-5.6/Gemini with
   a contact sheet.
7. Presentation:
   - hero-frame thumbnails, with the displayed frame chosen per query;
   - results grouped by scene ("more from this scene");
   - badges;
   - per-film Highlights and Hidden gems browsing.
8. Speed: resident vectors (GPU or RAM) for exact search; target under 500 ms.
9. Once the evidence exists, simplify the category composer: one box with
   auto-detected quote/character/film and optional chips.
10. Log plays, saves, placements and replacements for the later taste model.

Supersedes ADR-0004's restriction on priors and re-ranking; the ordering problem
is now demonstrated.

## Phase 3 — Editor harness v2

Replace the text-only generation chain. Keep projects, revisions, locks, the
renderer and the UI.

1. **Music map** (local).
   - Beat This! beats and downbeats, SongFormer sections, onsets/accents and
     energy.
   - Timed lyrics from LRCLIB, or vocal separation plus ASR.
   - The audio model (`gpt-audio-1.5` or Gemini audio) supplies meaning only.
2. **Concept and arc** (LLM with your direction): acts aligned to sections,
   visual intent, pacing, motifs and the fame/fresh budget.
3. **Pools.** Several diverse searches per act over the v2 index. Candidates
   carry action peaks, camera segments, subject tracks and hero frames.
4. **Assembly optimizer**: deterministic beam search/DP over bars, similar to
   BEAT's Bar-DP. It handles:
   - elastic shot spans and energy-adaptive cutting;
   - action or motion peaks landing on accents;
   - continuity (screen direction, subject position), with a match-cut bonus
     from the measured layout;
   - diversity across films, scenes and setups;
   - the fame budget, legal trims and locks.
5. **Visual check.** A model sees the assembled sequence as thumbnails and may
   swap among the offered alternatives.
6. **Critique loop.** Render a low-res preview; Gemini watches it with the audio
   and returns timestamped issues, which become optimizer constraints. At most
   2 rounds.
7. **Handoff.** OTIO (FCPXML if needed) export that references the original
   media, for finishing in Resolve.

Wish and Starjunk stay as regression passages. Supersedes LLM numeric cut timing
(ADR-0060), text-only selection and the footage-inspection slice (ADR-0061).

## Phase 4 — Later, only if the eval set shows a need

- Personal taste model, once Phase 2 logging has collected data.
- Challenger embeddings for motion/composition queries: Qwen3-VL-Embedding
  locally, or Gemini Embedding 2 hosted (~$35 batch for current keyframes).
- Depth (Depth Anything 3) to tell dolly from zoom and to read
  foreground/background composition.
- Sound-event search, trailer matching, and folding Match Cuts into the
  optimizer.

## Frozen (keep code and data; no new investment)

- Framing representation pilot and the 134 held preparation jobs. Do not resume
  them: measured subject layout replaces embedding grids for Framing. Reclaim
  their storage after Phase 1 layout works.
- Jev/intent-routing experiments (ADR-0086 to ADR-0091).
- Match Cuts cohorts (SAM/RAFT/DINOv3); their RAFT/SAM code is reused in
  Phase 1.
- Transitions lab and Runway generation; finishing moves to Resolve.
- Source-context pilot; superseded by the Phase 1 understanding pass.
- Targeted footage inspection; superseded by the Phase 3 critique loop.
- Flexible-assembly comparison runner. Its idea (discover footage before fixing
  timing) carries into Phase 3.

## Carried-over state

- 134 optional Framing jobs are on operator hold (`waiting_worker` with the
  pause marker); 18 completed. Leave the hold in place.
- Earlier pilot runs remain under `pipeline/eval/runs/` and `.tmp/`; the
  archived plan lists them.
- The last full backend run recorded in the archived plan passed; re-run the
  focused suites before each phase lands.

## Research basis

- Hosted video: [Gemini video understanding](https://ai.google.dev/gemini-api/docs/video-understanding) ·
  [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing). The OpenAI API
  still has no native video input (frames only).
- Temporal grounding limits: [TimeLens](https://arxiv.org/abs/2512.14698) — frontier
  models remain weak at exact timestamps, hence the burned-in shot keys.
- Camera motion: [CameraBench](https://linzhiqiu.github.io/papers/camerabench/),
  [CaMo](https://arxiv.org/abs/2605.20165) — VLMs lack camera-motion understanding;
  geometry excels on measurable motion.
- Detection/segmentation: [RF-DETR](https://github.com/roboflow/rf-detr) (Apache-2.0),
  [SAM 3/3.1](https://ai.meta.com/blog/segment-anything-model-3/).
- Scenes: [Scene-VLM](https://arxiv.org/abs/2512.21778).
- Aesthetics: [ArtiMuse](https://github.com/thunderbolt215/ArtiMuse),
  [aesthetic-predictor-v2.5](https://github.com/discus0434/aesthetic-predictor-v2-5).
- Balancing popularity: [popularity-aware re-ranking](https://arxiv.org/abs/1901.07555),
  [calibrated recommendations](https://dl.acm.org/doi/10.1145/3789266).
- Music-driven editing: [BEAT](https://arxiv.org/abs/2605.27067) (Bar-DP, VLM critic),
  [CutClaw](https://arxiv.org/html/2603.29664), [GLANCE](https://arxiv.org/html/2604.05076v1),
  [SongFormer](https://www.semanticscholar.org/paper/SongFormer:-Scaling-Music-Structure-Analysis-with-Hao-Yuan/3d8a9ab8ac816005bc360e35af7791c73b0fb99e),
  [LRCLIB](https://lrclib.net/docs).
- Data: [Wikidata licensing (CC0)](https://www.wikidata.org/wiki/Wikidata:Licensing),
  [IMDb non-commercial datasets](https://data.imdb.com/non-commercial-datasets/),
  [TMDB API terms](https://www.themoviedb.org/api-terms-of-use) (why TMDB is excluded),
  [OpenSubtitles API](https://opensubtitles.stoplight.io/docs/opensubtitles-api/e3750fd63a100-getting-started),
  [alass/ffsubsync via AutoSubSync](https://github.com/denizsafak/AutoSubSync).
- Handoff: [Resolve OTIO import](https://violetflare.ai/blog/davinci-resolve-otio-import/).
- Embedding/re-rank options: [Qwen3-VL-Embedding/Reranker](https://github.com/QwenLM/Qwen3-VL-Embedding).
