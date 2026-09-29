import type { GuideSection, ProjectInfo } from "./types";

// Explanations live separately from presentation and runtime configuration.
// Update alongside docs/search-architecture.md when a processing boundary changes.
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
      id: "ingestion", title: "Ingestion", summary: "Turn a film into timed shots, descriptions and searchable vectors.",
      introduction: "Ingestion prepares each film before search; compatible work is reused when it runs again. The original stays intact, with versioned results stored separately. A search result is a shot or a subdivision of a long take, not necessarily a complete dramatic scene.",
      steps: [
        {
          id: "intake", title: "Acquire & validate", summary: "Check the media files and confirm the film’s library title.",
          detail: ["Managed downloads use qBittorrent, with optional release search through Prowlarr. A separate monitor allows two downloads at a time and waits for complete files and a stopped torrent before import. Manual intake uses the same canonical naming and subtitle rules.", "Path, ownership, executable-file, space and layout checks precede import. Ambiguous films or subtitles pause for review. Three decoded frames check basic media usability; they do not certify the whole film or subtitle synchronization."],
          method: "Deterministic file validation, ffprobe and sampled FFmpeg decoding; no language model.",
          output: "Canonical film, optional English SRT and a durable acquisition/import record.",
          sources: ["pipeline/acquisition/engine.py", "pipeline/intake.py"],
        },
        {
          id: "dialogue", title: "Probe & dialogue", summary: "Identify the source and prepare timed spoken text.",
          detail: ["ffprobe reads streams, frame rate and duration. A content-derived film identity ties caches to the source. Dialogue prefers a usable canonical English SRT, then an eligible embedded English text track, then local audio transcription.", "Extracted embedded text is checked for structure, English dialogue, density and temporal coverage. Partial tracks remain as evidence but fall back to transcription. Cache receipts record the actual subtitle or model source, so changing it invalidates the affected dialogue."],
          model: whisper,
          method: "Bounded SRT validation; voice-activity detection and a trusted English audio-language hint for transcription when available.",
          output: "Film metadata, timed dialogue.json and source/validation manifests.",
          sources: ["pipeline/ingest/probe.py", "pipeline/ingest/dialogue.py", "pipeline/ingest/subtitles.py"],
        },
        {
          id: "shots", title: "Detect shots", summary: "Locate cuts and divide long takes into retrieval units.",
          detail: ["TransNetV2 estimates cut probabilities over decoded video frames. Boundaries become source-time intervals. Very short flash/strobe fragments are merged, while long shots are divided into contiguous units that retain their parent-shot identity.", "Cut detection tells the system where shots change. It does not identify when a person has finished an action or when a dramatic scene resolves."],
          model: "TransNetV2 · local shot-boundary detector",
          method: `Flash threshold: ${value(config?.thresholds.flash_min_duration)} s. Long-shot subdivision threshold: ${value(config?.thresholds.subsegment_min_duration)} s.`,
          output: "shots.json: stable unit IDs, start/end times, parent shots and keyframe targets.",
          sources: ["pipeline/ingest/shots.py"],
        },
        {
          id: "media", title: "Prepare frames", summary: "Make sparse visual evidence and playable previews.",
          detail: ["Longer units sample frames at 25%, 50% and 75%. Short shots use distinct native frames near the beginning, middle and end, preserving decoded presentation timestamps. These samples feed search and annotation.", "FFmpeg prepares images and previews. Browser playback can use a separate compatible audio copy with copied video and AAC audio. This repair does not alter the original film, and its failure need not block searchable publication."],
          method: `Timestamp-aware FFmpeg extraction. Short-shot sampling threshold: ${value(config?.thresholds.keyframe_short_shot_s)} s.`,
          output: "Keyframes, preview media, recorded frame times and optional playback derivatives.",
          sources: ["pipeline/ingest/media.py", "pipeline/ingest/playback.py"],
        },
        {
          id: "describe", title: "Encode & describe", summary: "Create visual vectors and descriptions of sampled frames.",
          detail: ["The paired visual encoder maps sampled images into normalized vectors; pooled vectors also describe each unit. A hosted vision annotator sees the keyframes to produce structured scene descriptions, visible text and descriptive facets. Overlapping dialogue is attached locally to the searchable record.", `Annotation results are cached by their evidence and model/prompt contract. Up to ${value(config?.ingest.annotation_concurrency)} requests run concurrently per film. These are descriptions inferred from sparse stills, not continuous video understanding.`],
          model: `${visual}. Annotation: ${annotator}.`,
          method: "Image embeddings and normalized pooling; structured vision annotation; paired-encoder text embeddings for the base search index.",
          output: "Per-frame and unit visual vectors, captions, OCR, mood/facet labels and cached annotation records.",
          sources: ["pipeline/ingest/embed.py", "pipeline/ingest/annotate.py", "pipeline/ingest/pipeline.py"],
        },
        {
          id: "publish", title: "Publish & derive", summary: "Make completed evidence searchable, then add semantic text views.",
          detail: ["The film’s prepared units and frame records are published to LanceDB with source timestamps and model contracts. Publication happens after the required local and hosted results are ready.", "Independent semantic text derivation follows publication. Caption, dialogue, OCR, descriptive-facet and mood documents get their own vectors. The Qwen profile activates only when compatible and complete for the current searchable generation; broad search retains its whole-channel legacy fallback if this derivation is unavailable."],
          model: text,
          method: "Generation-aware index publication; independently backfillable text documents and normalized embeddings.",
          output: "Searchable unit/frame tables, full-text index and versioned semantic-text feature tables.",
          sources: ["pipeline/index/writer.py", "pipeline/index/text_features.py", "pipeline/ingest/text_embed.py"],
        },
      ],
      note: "A configured model is not proof that every old film has been rebuilt with it. Compatibility manifests and feature coverage determine what search can actually use.",
    },
    {
      id: "search", title: "Search", summary: "Combine visual, semantic and lexical evidence, then rank and diversify.",
      introduction: "Queries search prepared evidence locally. Ordinary search does not send each query to a hosted language model or rewatch the films. Film filters constrain the candidates before ranking.",
      steps: [
        {
          id: "clues", title: "Resolve clues", summary: "Translate the main query and categories into explicit retrieval routes.",
          detail: ["The main description can combine subjects, actions, shot types, appearance and mood. Type @ and a movie name for a compact dropdown near the caret. The first match is highlighted; arrows navigate, Enter or Tab confirms the active option, and clicking away dismisses without applying. Escape removes the active @ and returns to plain text until a fresh @ or an empty field. Complete titles in ordinary prose start unselected, so plain Enter still searches those words. Confirmed @Title (year) mentions stay inline in the sentence; validated ranges compile separately into the description and union of film IDs. The input scrolls without growing the bar. Backspace immediately after a mention or Delete immediately before it removes the whole mention; editing inside it returns its words to literal text. The picker shares this scope. With only movie mentions selected, browse a bounded prefix in source order without model inference.", "Scene searches caption meaning; Words searches dialogue and OCR meaning; Mood searches stored mood and energy labels. Look compares global image appearance. Framing uses spatial evidence from a reference image. A category can search by itself with the main description empty.", "Up to three explicit clues can form a recipe. A dragged scene supplies its stored evidence or selected frame, and its source unit is excluded. Indexed-source Framing excludes the source film unless explicitly searching only that source film."],
          method: "Typed recipe dispatch and evidence resolution; no invented reference description.",
          output: "One or more retrieval requests with explicit facets, reference evidence and film scope.",
          sources: ["pipeline/search/recipe.py", "pipeline/search/capabilities.py", "pipeline/search/browse.py"],
        },
        {
          id: "candidates", title: "Retrieve candidates", summary: "Search three complementary indexes.",
          detail: ["Visual retrieval embeds a text query with the paired encoder’s text tower and compares it to frame image vectors by cosine similarity. The best matching frame gives a unit one vote. Semantic retrieval compares the query with separate stored text documents, again taking the best document per unit.", "Lexical retrieval uses the full-text index with English stemming, case/ASCII folding and stop-word removal. A single unquoted word has no separate lexical vote when dense channels are active. Quotes allow lexical participation, but do not guarantee an exact phrase match."],
          model: `${visual}. Semantic text: ${text}.`,
          channels: [
            { title: "Visual", description: "Query text → paired encoder → frame vectors" },
            { title: "Semantic", description: "Query text → text encoder → evidence documents" },
            { title: "Lexical", description: "Query terms → full-text index, when eligible" },
          ],
          method: `Cosine vector search + native full-text search. Candidate depth: ${depth}.`,
          output: "Ranked visual, semantic-text and lexical candidate lists.",
          sources: ["pipeline/search/retrieve.py", "pipeline/index/text_features.py", "pipeline/index/writer.py"],
        },
        {
          id: "fusion", title: "Combine ranks", summary: "Give strong evidence across channels a shared ranking.",
          detail: ["Weighted reciprocal-rank fusion adds weight / (60 + rank) for each available channel. It combines positions rather than pretending different vector or text scores share one confidence scale.", "Recipes combine their clue rankings with equal reciprocal-rank fusion. Required uploaded visual clues and indexed-source Framing constrain the eligible candidates; other clues rerank inside that visual pool. A match is not a guarantee that every natural-language condition is true."],
          method: config ? `Broad-channel weights: visual ${config.retrieval.weights.img}, semantic text ${config.retrieval.weights.txt}, lexical ${config.retrieval.weights.lex}. Recipe clues use equal weights.` : "Configurable visual / semantic / lexical weights; equal weights between recipe clues.",
          output: "One ranked candidate pool with per-clue evidence.",
          sources: ["pipeline/search/retrieve.py", "pipeline/search/recipe.py"],
        },
        {
          id: "refine", title: "Refine & diversify", summary: "Reduce duplicate imagery and avoid a single film dominating discovery.",
          detail: ["Caption checks suppress likely credits, title cards, logos and blank/static artifacts unless requested. Hard visual deduplication suppresses near-identical images: cosine similarity at least 0.92, or 0.90 for nearby units in the same film. Broader discovery softly defers nearby scenes and repeated films; explicit film scope skips those broad preferences, and a film the query names (by title, character or actor) is never deferred.", "Framing uses a PE 6×6 spatial feature grid, comparing corresponding image cells: 65% global similarity plus 35% spatial similarity. Source-validated cached grids are reused; missing or changed entries are encoded locally with the same numerical contract. A separately gated composition experiment retrieves layouts independently of appearance; it remains off until complete coverage and human review pass. Reference search has its own temporal and page-diversity preferences; it is not a reliable pose or motion detector."],
          method: config ? `Broad film-repeat penalty: original rank + ${config.retrieval.diversity.film_repeat_rank_strength} × repeats / (repeats + 1). Reference pages: ${config.retrieval.diversity.page_size}, soft target ${config.retrieval.diversity.film_results_per_page_target} results per film, with relevance backfill.` : "Visual deduplication, bounded temporal/film preferences and optional spatial reranking.",
          output: "A ranked, less repetitive set; soft diversity is not a film blacklist or hard quota.",
          sources: ["pipeline/search/retrieve.py", "pipeline/index/framing_cache.py", "pipeline/search/composition.py"],
        },
        {
          id: "results", title: "Return moments", summary: "Show a representative frame linked to its original source interval.",
          detail: ["Results carry the original film and unit IDs, timestamps, descriptive evidence and a representative matching frame. Playback and saved scenes retain that identity; moving the player does not silently change the indexed search reference.", "Broad text search can fall back as one whole channel from Qwen to compatible paired-encoder text vectors. Focused Scene, Words and Mood instead report unavailable features clearly. Incompatible vector spaces are never mixed."],
          method: config ? `Stable ranked prefixes: initially up to ${config.retrieval.result_window} results, with deeper retrieval up to ${config.retrieval.max_result_limit}.` : "Bounded, progressively deeper ranked prefixes.",
          output: "Search cards, source playback and bookmarks referring to existing indexed units.",
          sources: ["pipeline/api/main.py", "pipeline/search/retrieve.py"],
        },
      ],
      note: "Sparse frames can miss a gesture or its best instant. Ordinary search has no plot index, film-audio index or verified action-completion representation. Scores are ranking signals, not confidence percentages.",
    },
    {
      id: "editing", title: "Editing & optional context", summary: "Reuse the library to choose footage, plan timing and render a source-backed edit.",
      introduction: "The editor is a separate consumer of the same source library. It stores its own music evidence, plans and project revisions; it does not change search embeddings to suit a song.",
      steps: [
        {
          id: "music", title: "Understand music", summary: "Prepare timing evidence and an interpretation of the chosen passage.",
          detail: ["Local audio analysis supplies timing and energy evidence. The configured audio model listens to the actual selected passage to describe its structure, feeling and possible editorial direction. These interpretations are kept separate from measured timing.", "Optional beat/downbeat preparation is separately configured and cached. Selecting a checkpoint does not prove that a passage has already been analyzed with it."],
          model: config ? `${config.lab.music_model} · ${config.lab.music_provider}. Beat This! checkpoint override: ${config.lab.beat_checkpoint_configured ? "configured" : "none; a prepared local profile may still be available"}.` : "Configured music-audio model; optional Beat This! profile.",
          method: "Local waveform/RMS amplitude analysis, estimated beats/downbeats from a prepared Beat This! profile, plus bounded hosted audio interpretation.",
          output: "Music evidence, passage boundaries and editable direction/timing plans.",
          sources: ["pipeline/lab/music.py", "pipeline/lab/music_evidence.py", "pipeline/lab/prepare_music.py"],
        },
        {
          id: "editor-selection", title: "Find & select footage", summary: "Retrieve real candidates, then choose among valid source windows.",
          detail: ["The planner turns music and user direction into bounded searches using the existing search adapters. Selection uses offered candidates and validated source intervals rather than inventing arbitrary film timestamps.", "Optional narrative context is a separate cached layer of cited observations and claims. The editor may interpret symbolism against the song, but those interpretations are not saved as canonical film facts. This layer does not add plot retrieval to ordinary search."],
          model: config ? `${config.lab.planner_model}. Context profile: ${config.lab.context_profile ?? "disabled"}. Bounded footage-inspection pilot: ${config.lab.footage_inspection ? "enabled in configuration" : "disabled"}.` : "Configured planner; context and footage-inspection settings unavailable.",
          method: "Search-backed candidate selection, structured model output, source-window validation and optional cached context after retrieval.",
          output: "Selected source candidates, reasons and an inspectable selection record.",
          sources: ["pipeline/lab/generation.py", "pipeline/context/build.py", "pipeline/context/editor.py"],
        },
        {
          id: "timing", title: "Plan cuts", summary: "Fit footage to song timing while respecting available source bounds.",
          detail: ["New edits and explicit regeneration can propose cuts on the renderer’s frame grid. After retrieval, bounded timing choices are fitted to the selected source lengths. Filling an existing edit keeps its cut positions; manual timing changes need no model call.", "Source length proves that footage can fit a slot, not that an action has finished. Reliable action completion remains a limitation. Editable trims and source playback let you inspect the actual result."],
          method: "Bounded dynamic programming fits offered cut boundaries to source duration, minimizing displacement from preferred timing; musical and source constraints remain explicit.",
          output: "A timeline with explicit in/out points, durations and editable cut decisions.",
          sources: ["pipeline/lab/timing_planner.py", "pipeline/lab/source_timing.py", "pipeline/lab/rhythm_timing.py"],
        },
        {
          id: "render", title: "Preview & render", summary: "Play the proposed edit and save deliberate project revisions.",
          detail: ["The editor keeps the source timeline and saved project state separate from generated previews. Long-running tasks use durable jobs with progress and cancellation; a completed proposal is validated before it changes a project.", "FFmpeg handles local media assembly and audio mixing. Transitions and hosted video experiments live in separate Lab workflows with their own provenance and opt-in generation steps."],
          method: "Source-backed rendering, recorded project revisions, explicit job ownership and cleanup of disposable intermediates.",
          output: "Preview/export media, editable project state and recoverable job history.",
          sources: ["pipeline/lab/worker.py", "pipeline/lab/store.py", "pipeline/lab/audio_mix.py"],
        },
      ],
      note: "Context and footage inspection are optional pilots. Their configuration can be enabled without every candidate having coverage; missing evidence stays explicit. Metaphorical readings are editorial suggestions, not verified plot facts.",
    },
    {
      id: "storage", title: "Storage & services", summary: "Keep originals, replaceable features and user work independent.",
      introduction: "These are separate ownership layers, not extra ingestion stages. Keeping them apart lets models and indexes evolve without rewriting source films or losing saved work.",
      steps: [
        {
          id: "originals", title: "Originals & evidence", summary: "The film and its timestamps remain the source of truth.",
          detail: ["Canonical films and selected subtitles are preserved as source material. Successful managed acquisitions archive their leftover release evidence intact after searchable publication.", "Cancel stops owned work, detaches the torrent and removes that acquisition’s staging files. Imported library films, canonical subtitles, existing evidence archives and unrelated incoming files stay preserved. Cleanup failures remain visible and retry."],
          method: "Verified ownership, source identity, immutable canonical input and explicit cancellation boundaries.",
          output: "Source films, raw subtitles, release evidence and import/cancellation journals.",
          sources: ["pipeline/intake.py", "pipeline/acquisition/engine.py", "pipeline/acquisition/cleanup.py"],
        },
        {
          id: "derivations", title: "Replaceable features", summary: "Rebuild one representation without replacing the film.",
          detail: ["Keyframes, previews, annotations, embeddings, spatial caches and optional context artifacts have their own evidence/model/version dependencies. Independent backfills can replace a derived representation while retaining source identity.", "LanceDB stores the searchable unit, frame and feature tables. Publication and compatibility checks prevent partial generations or mismatched vector spaces from silently becoming one index."],
          method: "Versioned manifests, model-scoped features, source checks, publication locks and completeness gates.",
          output: "Rebuildable media/features and a compatible searchable generation.",
          sources: ["pipeline/index/writer.py", "pipeline/index/text_features.py", "pipeline/index/framing_features.py"],
        },
        {
          id: "processes", title: "App state & workers", summary: "Keep the interface responsive while durable queues do the work.",
          detail: ["The Next.js interface calls the FastAPI service. SQLite-backed state keeps bookmarks, saved projects, job records and acquisition intent outside replaceable index data.", "The acquisition monitor handles downloads independently. Ingestion and editor workers claim their own job roles, with shared resource/publication locks where needed. Source preparation, interactive retrieval and editing remain distinct responsibilities."],
          method: "Next.js → FastAPI → durable state; separate acquisition, ingestion and editor processes.",
          output: "Persistent user work and restartable queues, independent of derived media and model caches.",
          sources: ["pipeline/api/main.py", "pipeline/acquisition/worker.py", "pipeline/lab/worker.py", "pipeline/lab/store.py"],
        },
      ],
      note: "This guide reads only an allowlisted configuration snapshot. It never loads a model, scans your films or starts an acquisition, ingest, backfill or editing job.",
    },
  ];
}
