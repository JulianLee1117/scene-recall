import type { GuideSection, ProjectInfo } from "./types";
import { GUIDE_MODELS } from "./models";

// Explanations live separately from presentation and runtime configuration.
// Update alongside docs/search-architecture.md when a processing boundary changes.
// Copy rule: one idea per paragraph, plain words first, no more than two paragraphs a step.
export function buildGuide(config: ProjectInfo | null): GuideSection[] {
  const value = (input: string | number | undefined, fallback = "configured value") => input === undefined ? fallback : String(input);
  const visual = config?.models.visual_encoder === "pe_core_l14"
    ? "PE-Core L/14 · timm/PE-Core-L-14-336 · 1,024 dimensions"
    : value(config?.models.visual_encoder, "Configured paired image/text encoder; live settings unavailable");
  const text = config?.models.text_encoder === "qwen3-embedding-0.6b"
    ? "Qwen3-Embedding-0.6B · 1,024 dimensions · pinned revision 97b0c61"
    : value(config?.models.text_encoder, "Configured semantic text encoder; live settings unavailable");
  const annotator = config ? `${config.models.annotator} · ${config.models.annotator_provider} · ${config.models.annotator_image_detail} image detail` : "Configured hosted vision annotator; live settings unavailable";
  const whisper = config ? `faster-whisper · ${config.models.whisper}` : "faster-whisper · configured model";
  const depth = config ? `${config.retrieval.candidate_limit} per channel; up to ${config.retrieval.candidate_limit * 3} for unscoped broad search` : "A bounded candidate pool per channel; broader when no film is selected";

  return [
    {
      id: "ingestion", title: "Ingestion", summary: "Turn a film file into shots you can search.",
      introduction: "The original film is never changed. Everything learned about it is stored separately and can be rebuilt. A film becomes searchable first; extra evidence about story, camera and picture is added afterwards.",
      steps: [
        {
          id: "intake", title: "Acquire & validate", summary: "Check the files and confirm the film’s library title.",
          detail: ["Downloads run through qBittorrent, with optional release search in Prowlarr, and a separate monitor imports finished files. Manual imports follow the same naming and subtitle rules.", "Checks cover paths, ownership, free space and basic decoding. An ambiguous film or subtitle waits for your review."],
          method: "File validation, ffprobe and sampled FFmpeg decoding. No language model.",
          output: "A canonical film, an optional English subtitle file and an import record.",
          sources: ["pipeline/acquisition/engine.py", "pipeline/intake.py"],
        },
        {
          id: "dialogue", title: "Probe & dialogue", summary: "Identify the source and prepare timed dialogue.",
          detail: ["Each film gets an identity based on its content, so every cache follows the file. Dialogue comes from a good English subtitle file, else an embedded text track, else local speech transcription.", "Every source is checked for structure and coverage, and the one actually used is recorded. Changing it refreshes only the dialogue."],
          model: whisper,
          method: "Subtitle validation, voice-activity detection and an English audio-language hint.",
          output: "Film metadata and timed dialogue with its source records.",
          sources: ["pipeline/ingest/probe.py", "pipeline/ingest/dialogue.py", "pipeline/ingest/subtitles.py"],
        },
        {
          id: "shots", title: "Detect shots", summary: "Find the cuts and divide long takes into search units.",
          detail: ["TransNetV2 finds where the picture cuts. Tiny flash fragments are merged, and long takes are divided into equal pieces that remember their parent shot.", "This tells search where shots change. It does not know when an action finishes or a scene ends."],
          model: `${GUIDE_MODELS.shots} · local shot-boundary detector`,
          method: `Flash threshold: ${value(config?.thresholds.flash_min_duration)} s. Long-shot subdivision threshold: ${value(config?.thresholds.subsegment_min_duration)} s.`,
          output: "Stable unit IDs with start and end times, parent shots and keyframe targets.",
          sources: ["pipeline/ingest/shots.py"],
        },
        {
          id: "media", title: "Prepare frames", summary: "Pick a few stills and make playable previews.",
          detail: ["Longer shots are sampled at 25%, 50% and 75%. Short shots use their first, middle and last frames. These stills feed search and captioning.", "FFmpeg also makes previews and, when needed, a browser-friendly audio copy. The original film is never altered."],
          method: `Timestamp-aware FFmpeg extraction. Short-shot sampling threshold: ${value(config?.thresholds.keyframe_short_shot_s)} s.`,
          output: "Keyframes, previews and recorded frame times.",
          sources: ["pipeline/ingest/media.py", "pipeline/ingest/playback.py"],
        },
        {
          id: "describe", title: "Encode & describe", summary: "Turn the stills into vectors and written descriptions.",
          detail: ["An image encoder turns each still into a vector. A vision model then writes a caption, mood, any visible text and labels such as framing and time of day. Overlapping dialogue is attached locally.", `Results are cached by their evidence and model. Up to ${value(config?.ingest.annotation_concurrency)} requests run at once per film. These are descriptions of sparse stills, not of continuous video.`],
          model: `${visual}. Annotation: ${annotator}.`,
          method: "Image embeddings with normalized pooling; structured vision annotation; paired-encoder text embeddings.",
          output: "Per-frame and per-unit vectors, captions, on-screen text and labels.",
          sources: ["pipeline/ingest/embed.py", "pipeline/ingest/annotate.py", "pipeline/ingest/pipeline.py"],
        },
        {
          id: "publish", title: "Publish & derive", summary: "Make the film searchable, then add meaning-based text views.",
          detail: ["Finished shots and frames are written to the search index with their source times and model versions. A second step builds the text views behind meaning-based search.", "Search uses that text profile only once it is complete; until then it falls back to an older whole-channel version. Each ingest also repairs small gaps left by other films."],
          model: text,
          method: "Generation-aware publication; independently rebuildable text views; bounded library-wide gap repair.",
          output: "Searchable unit and frame tables, a full-text index and versioned text-view tables.",
          sources: ["pipeline/index/writer.py", "pipeline/index/text_features.py", "pipeline/ingest/text_embed.py"],
        },
        {
          id: "evidence", title: "Add evidence", summary: "Layer story, measurement and presentation facts on top.",
          detail: ["Open data adds cast, plot and quotes. A hosted model watches low-resolution clips and returns scenes, per-shot action and characters, the peak moment, and fame and craft ratings. Hero frames, a 4 fps moment index and priors follow.", "Each pass saves its own versioned result and the inputs it used, so stale results are easy to spot. A failed pass never undoes publication."],
          model: `${GUIDE_MODELS.understanding} for understanding and highlights · ${GUIDE_MODELS.flow} and ${GUIDE_MODELS.detector} for local measurement · ${GUIDE_MODELS.speech} for subtitle sync.`,
          method: "Per-film passes with hashed profiles and resumable receipts. Facts that can be measured come from pixels; model estimates are only hints.",
          output: "Versioned evidence per film, compiled into rebuildable search tables.",
          sources: ["pipeline/evidence/pipeline.py", "pipeline/evidence/understanding.py", "pipeline/evidence/measure.py", "pipeline/evidence/hero.py", "pipeline/evidence/moments.py", "pipeline/evidence/synthesis.py", "pipeline/evidence/subsync.py"],
        },
        {
          id: "measure", title: "Measure camera & picture", summary: "Read real camera movement and subjects from the pixels.",
          detail: ["One GPU pass follows optical flow between frames and labels the camera as static, handheld, pushing in, pulling out, or panning and tilting. Slow creeping moves are caught by adding up drift. Shaky, watery or very dark shots stay unknown instead of guessed.", "The same pass finds hidden cuts, subjects and where they travel, plus brightness, colour and sharpness."],
          model: `${GUIDE_MODELS.flow} optical flow (c_t_v2 checkpoint) · ${GUIDE_MODELS.detector} detection · local GPU`,
          method: "Per-frame-pair flow, smoothing, shake detection and drift accumulation, each label with a reliability score.",
          output: "Camera segments, hidden cuts, subject tracks and look measurements for every shot.",
          sources: ["pipeline/evidence/measure.py", "pipeline/evidence/compile.py"],
        },
      ],
      note: "A configured model is not proof that every older film was rebuilt with it. A film whose evidence is not finished is still searchable and simply scored neutrally.",
    },
    {
      id: "search", title: "Search", summary: "Combine four kinds of evidence, then judge and rank.",
      introduction: "Search runs locally on prepared evidence. It does not send your query to a language model or re-watch films. Each result is one card for a whole dramatic scene.",
      steps: [
        {
          id: "clues", title: "Resolve clues", summary: "Turn what you typed into clear retrieval routes.",
          detail: ["Describe a moment, quote a line, add a reference image, or type @ to scope a search to a movie. Scene, Words, Mood, Look and Framing each search one kind of evidence, and any can stand alone.", "Up to three clues form a recipe. A dragged scene brings its stored evidence and is left out of its own results."],
          method: "Typed recipe dispatch and evidence resolution. No invented reference description.",
          output: "One or more retrieval requests with explicit evidence and film scope.",
          sources: ["pipeline/search/recipe.py", "pipeline/search/capabilities.py", "pipeline/search/browse.py"],
        },
        {
          id: "candidates", title: "Retrieve candidates", summary: "Search four channels at once.",
          detail: ["Visual search compares your words to frame images. Semantic search compares meaning across seven text views. Full text matches words, and quotes match subtitle lines and their neighbours.", "Each channel keeps the best match per shot. Quoting a word asks for exact wording but does not guarantee an exact phrase."],
          model: `${visual}. Semantic text: ${text}.`,
          channels: [
            { title: "Visual", description: "Query text → paired encoder → frame vectors" },
            { title: "Semantic", description: "Query text → text encoder → evidence documents" },
            { title: "Lexical", description: "Query terms → full-text index, when eligible" },
            { title: "Quotes", description: "Query words → subtitle lines and neighbouring cues" },
          ],
          method: `Cosine vector search + native full-text search. Candidate depth: ${depth}.`,
          output: "Ranked visual, semantic, lexical and quote candidates.",
          sources: ["pipeline/search/retrieve.py", "pipeline/search/quotes.py", "pipeline/index/text_features.py", "pipeline/index/writer.py"],
        },
        {
          id: "fusion", title: "Combine ranks", summary: "Let evidence from several channels add up.",
          detail: ["Channels are merged by rank position, not raw score, because their scores are not on one scale. A shot that several channels like rises to the top.", "Recipes fuse their clues equally. A required reference image limits the pool, and the other clues rerank inside it."],
          method: config ? `Broad-channel weights: visual ${config.retrieval.weights.img}, semantic text ${config.retrieval.weights.txt}, lexical ${config.retrieval.weights.lex}. Recipe clues use equal weights.` : "Configurable visual / semantic / lexical weights; equal weights between recipe clues.",
          output: "One ranked pool with per-clue evidence.",
          sources: ["pipeline/search/retrieve.py", "pipeline/search/recipe.py"],
        },
        {
          id: "judge", title: "Judge & score", summary: "Give every candidate one relevance score.",
          detail: ["A cross-encoder reads your query together with each shortlisted shot’s evidence and judges the match. Its verdict is blended with the retrieval evidence into one score. Named films and characters, shot scale, camera move, time of day and colour then nudge it up or down by small fixed amounts, and the Balanced, Famous and Hidden gems presets lean on fame and craft.", "These nudges settle near-ties and never overturn a clearly better match. The judge has a one-second budget and rests when the GPU is busy, so results simply keep their retrieval order."],
          model: `${GUIDE_MODELS.reranker} · local cross-encoder · optional`,
          method: "A weighted blend of rank evidence and the judge’s log-odds, then bounded multiplicative factors for signals and priors.",
          output: "One ordered pool with a relevance score and the evidence behind it.",
          sources: ["pipeline/search/rerank.py", "pipeline/search/signals.py", "pipeline/search/priors.py"],
        },
        {
          id: "refine", title: "Refine & diversify", summary: "Remove repeats and keep one film from dominating.",
          detail: ["Credits, logos and blank frames are filtered out, and near-identical images are folded together. Shots from one dramatic scene become a single card with the others listed beneath.", "Broad searches spread results across scenes and films, but a film you name is never pushed down. Framing compares layout: 65% overall look, 35% where things sit in the picture."],
          method: config ? `Broad film-repeat penalty: original rank + ${config.retrieval.diversity.film_repeat_rank_strength} × repeats / (repeats + 1). Reference pages: ${config.retrieval.diversity.page_size}, soft target ${config.retrieval.diversity.film_results_per_page_target} results per film, with relevance backfill.` : "Visual deduplication, bounded temporal and film preferences, and optional spatial reranking.",
          output: "A ranked, less repetitive set. Diversity is a soft preference, not a quota.",
          sources: ["pipeline/search/retrieve.py"],
        },
        {
          id: "results", title: "Return moments", summary: "Show the best frame, linked to its exact place in the film.",
          detail: ["Each card carries the film, the time, the best frame, a story line and the matched subtitle, and playback opens at the match. Thumbnails are hero frames, with iconic and hidden-gem badges.", "Search keeps serving a complete snapshot while new evidence is being built, and never mixes incompatible vector spaces."],
          method: config ? `Stable ranked prefixes: initially up to ${config.retrieval.result_window} results, with deeper retrieval up to ${config.retrieval.max_result_limit}.` : "Bounded, progressively deeper ranked prefixes.",
          output: "Cards, playback and bookmarks that point at indexed units.",
          sources: ["pipeline/api/main.py", "pipeline/search/retrieve.py", "pipeline/search/resident.py"],
        },
      ],
      note: "Sparse stills can miss a gesture or its best instant, and scores rank results rather than state confidence. Story and scene text exist only for films whose evidence passes have run.",
    },
    {
      id: "editing", title: "Editing & optional context", summary: "Turn a song and an idea into a source-backed edit.",
      introduction: "The editor reuses the same library. Language models plan the idea; the cuts themselves are measured against the song. Edits are saved separately and never change search.",
      steps: [
        {
          id: "music", title: "Understand music", summary: "Measure the song, then describe its feeling.",
          detail: ["Local analysis finds beats, downbeats, accents and loudness. An audio model listens to the chosen passage and describes its shape and feeling, kept separate from the measured timing.", "A song profile and a per-edit treatment set the idea, pace and footage taste."],
          model: config ? `${config.lab.music_model} · ${config.lab.music_provider}. Beat This! checkpoint override: ${config.lab.beat_checkpoint_configured ? "configured" : "none; a prepared local profile may still be available"}.` : "Configured music-audio model; optional Beat This! profile.",
          method: "Local waveform and beat analysis, plus a bounded hosted audio interpretation.",
          output: "A music map, passage boundaries and an editable direction.",
          sources: ["pipeline/lab/music.py", "pipeline/lab/music_evidence.py", "pipeline/lab/prepare_music.py"],
        },
        {
          id: "editor-selection", title: "Find & cast footage", summary: "Plan an act per section, then cast the shots.",
          detail: ["Each section of the song gets an act: an intent, a few searches, a fame target and a pace. The searches use ordinary search, and shots that read badly under music are left out.", "A casting step then picks the shots that carry each act, with one landing the biggest musical moment."],
          model: config ? `${config.lab.planner_model}. Context profile: ${config.lab.context_profile ?? "disabled"}. Bounded footage-inspection pilot: ${config.lab.footage_inspection ? "enabled in configuration" : "disabled"}.` : "Configured planner; context and footage-inspection settings unavailable.",
          method: "Search-backed candidates, structured model output and validated source windows.",
          output: "Cast shots with reasons and an inspectable selection record.",
          sources: ["pipeline/lab/harness/concept.py", "pipeline/lab/harness/pools.py", "pipeline/lab/harness/cast.py", "pipeline/lab/generation.py", "pipeline/context/build.py", "pipeline/context/editor.py"],
        },
        {
          id: "timing", title: "Assemble on the beat", summary: "Place the cast shots on the music.",
          detail: ["A deterministic search puts action peaks on accents and scores variety across scenes, films and looks. An act can hold one shot or flash a burst of short ones.", "A review checks the result, and an optional critique pass lets a model watch a rough cut once. Filling gaps keeps your cuts."],
          method: "Beam search over beats and accents within source-length limits.",
          output: "A timeline with explicit in and out points and editable cuts.",
          sources: ["pipeline/lab/harness/assemble.py", "pipeline/lab/harness/review.py", "pipeline/lab/timing_planner.py", "pipeline/lab/source_timing.py", "pipeline/lab/rhythm_timing.py"],
        },
        {
          id: "moments", title: "Match cuts", summary: "Cut between two shots that share a shape, position or motion.",
          detail: ["Four times a second, every instant is described by its shapes, people, light, lines, colour and motion. Search compares one moment with the whole library and scores the best candidates on what the eye carries across the cut.", "The editor can use the same score, and the Match Cuts workspace lets you browse and chain cuts. Ordinary search does not offer it."],
          model: `${GUIDE_MODELS.segmentation} segmentation · ${GUIDE_MODELS.pose} keypoints · local`,
          method: "Memory-mapped coarse retrieval, then calibrated exact scoring. Cut points are exact to the 4 fps grid; renders use true source times.",
          output: "Ranked matching moments with source times and a crop for the chosen format.",
          sources: ["pipeline/evidence/moments.py", "pipeline/matching/moments/index.py", "pipeline/matching/moments/score.py", "pipeline/matching/moments/find.py", "pipeline/lab/harness/matchcuts.py"],
        },
        {
          id: "render", title: "Preview & render", summary: "Play the edit and save deliberate revisions.",
          detail: ["Renders can add effects, TV screens and generated clips, each as its own kind of source. A newer render replaces an older cached one.", "Long jobs run durably with progress and cancel, and a result is validated before it changes a project."],
          method: "Source-backed rendering, recorded revisions and cleanup of disposable intermediates.",
          output: "Preview and export media, editable project state and job history.",
          sources: ["pipeline/lab/worker.py", "pipeline/lab/store.py", "pipeline/lab/audio_mix.py", "pipeline/lab/effects.py", "pipeline/lab/screens.py", "pipeline/lab/generated.py"],
        },
      ],
      note: "Context and footage inspection are optional pilots, and missing evidence stays explicit. Metaphorical readings are editorial suggestions, not verified plot facts. Match cuts rest on shape, light and motion, so conceptual rhymes are weaker.",
    },
    {
      id: "storage", title: "Storage & services", summary: "Keep originals, rebuildable data and your work apart.",
      introduction: "These are three separate layers, not extra ingestion stages. Keeping them apart lets models and indexes change without touching the films or losing saved work.",
      steps: [
        {
          id: "originals", title: "Originals & evidence", summary: "The film and its timestamps stay the source of truth.",
          detail: ["Films and chosen subtitles are kept as source material. A finished download archives its leftover release files once the film is searchable.", "Cancel stops owned work and removes only that download’s staging files. Library films, subtitles and existing evidence stay put, and cleanup failures remain visible."],
          method: "Verified ownership, content identity and explicit cancel boundaries.",
          output: "Source films, raw subtitles, release evidence and import journals.",
          sources: ["pipeline/intake.py", "pipeline/acquisition/engine.py", "pipeline/acquisition/cleanup.py"],
        },
        {
          id: "derivations", title: "Replaceable features", summary: "Rebuild any one representation without touching the film.",
          detail: ["Keyframes, annotations, evidence, embeddings and caches each record the model and version behind them. Evidence is saved per film and profile, and a newer profile takes over only once it is complete.", "The search index checks compatibility so a half-built generation or a mismatched vector space can never silently become the index."],
          method: "Versioned manifests, model-scoped features, source checks and completeness gates.",
          output: "Rebuildable media and features, and one compatible searchable generation.",
          sources: ["pipeline/index/writer.py", "pipeline/index/text_features.py", "pipeline/evidence/store.py", "pipeline/evidence/compile.py"],
        },
        {
          id: "processes", title: "App state & workers", summary: "Keep the app responsive while queues do the work.",
          detail: ["The web app talks to a FastAPI service. A SQLite database holds bookmarks, projects, jobs and download intent, apart from the rebuildable index. A local log notes your searches, plays and saves for future personal ranking; nothing ranks with it yet.", "Downloads, ingestion and editing run in separate processes. The ingest worker does library and GPU work, and the editor worker does edits and renders. A restarted worker marks only its own unfinished jobs interrupted, and failed ingests wait for an explicit retry."],
          method: "Next.js → FastAPI → durable state, with separate download, ingest and editor processes.",
          output: "Persistent user work and restartable queues, independent of derived data.",
          sources: ["pipeline/api/main.py", "pipeline/acquisition/worker.py", "pipeline/lab/worker.py", "pipeline/lab/store.py"],
        },
      ],
      note: "This guide reads only an allowlisted configuration snapshot. It never loads a model, scans your films or starts a job.",
    },
  ];
}
