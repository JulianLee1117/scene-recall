import type { GlossaryGroup } from "./types";

// Plain-language definitions of the words this project uses in a specific way.
// Keep alongside guide.ts and docs/search-architecture.md when a boundary changes.
export const GLOSSARY: GlossaryGroup[] = [
  {
    id: "footage", title: "Footage", summary: "How a film is divided, from the whole file down to one instant.",
    terms: [
      { term: "Film", definition: "One source file, identified by a content-derived film ID (a hash of the file). Renaming it does not create a new film." },
      { term: "Scene", definition: "A dramatic scene: consecutive shots in the same place, time and action, grouped by the story-understanding pass. It is often many shots, not just one location, and search folds its shots into one card." },
      { term: "Shot", definition: "One continuous take between two cuts, found by the shot detector." },
      { term: "Unit", definition: "What search stores and returns. Usually one shot; a shot longer than 20 seconds is split into equal pieces, and each piece is a unit that remembers its parent shot." },
      { term: "Keyframe", definition: "A frame saved to represent a unit, up to three of them. Captions, embeddings and ordinary search see these, not every frame." },
      { term: "Frame", definition: "A single still image from the video, about 24 a second. The pipeline looks at only a few of them." },
      { term: "Moment", definition: "One instant on the 4 frames-per-second grid inside a shot. Match cuts compare moments, not shots." },
      { term: "Picture", definition: "One distinct image inside a shot. A dissolve holds two pictures; an ordinary shot holds one." },
      { term: "Hidden cut", definition: "A cut the shot detector missed, found later by the measurement pass. It is recorded but does not split the unit yet." },
      { term: "Dark span", definition: "A near-black stretch of a shot, excluded when choosing thumbnails and cut points." },
    ],
  },
  {
    id: "display", title: "How a shot is shown", summary: "What you see on a result card, and where it comes from.",
    terms: [
      { term: "Peak", definition: "The most important instant in a shot, as marked by the story-understanding pass." },
      { term: "Focus span", definition: "The picture that holds the peak. Thumbnails and previews stay inside it, so they never show the wrong half of a dissolve." },
      { term: "Hero frame", definition: "The best-scoring still inside the focus span, used as the thumbnail. It favours sharpness, good exposure and nearness to the peak." },
      { term: "Hover preview", definition: "A four-second clip around the peak, played when you hover a card." },
      { term: "Facet", definition: "A short label with a fixed vocabulary, such as framing: close-up or time of day: night, filled in by the vision annotator. Anything outside the vocabulary becomes unknown." },
    ],
  },
  {
    id: "evidence", title: "Evidence & ratings", summary: "Facts learned about a film, and how they are kept honest.",
    terms: [
      { term: "Evidence", definition: "Facts about a film produced by a pass, such as story, camera movement and fame, stored as immutable artifacts beside the film." },
      { term: "Pass", definition: "One evidence-making step: metadata, subtitles, understanding, highlights, measurement, hero frames, moments or priors. Also called a producer." },
      { term: "Profile", definition: "A fingerprint of everything that shaped a result: model, prompt, schema and settings. If it changes, older results are recognised as stale." },
      { term: "Derivation", definition: "Anything computed from the film that can be thrown away and rebuilt: captions, embeddings, indexes and evidence." },
      { term: "Backfill", definition: "Computing a newer kind of derivation for films that were ingested before it existed." },
      { term: "Fame and craft", definition: "The story-understanding pass’s 0–3 ratings of how widely recognised a moment is and how strong it looks. They are model judgements, not audience data." },
      { term: "Iconic", definition: "A shot flagged as one of its film’s most famous moments." },
      { term: "Hidden gem", definition: "A well-made, little-known shot, picked per film for high craft and low library-wide fame." },
    ],
  },
  {
    id: "search", title: "Search", summary: "The words behind finding and ranking a moment.",
    terms: [
      { term: "Embedding", definition: "A list of numbers that represents the meaning of an image or some text. Similar things get close lists. Also called a vector." },
      { term: "Vector space", definition: "The set of vectors made by one model. Spaces from different models are never compared or mixed." },
      { term: "Channel", definition: "One way of finding candidates: visual, semantic, full-text or quotes." },
      { term: "View", definition: "One kind of text document stored for each unit in the semantic channel: caption, dialogue, on-screen text, facets, mood, story or scene." },
      { term: "Rank fusion", definition: "Combining channels by each candidate’s position in each list, because their raw scores are not on the same scale." },
      { term: "Bi-encoder", definition: "A model that turns the query and each document into vectors separately, then compares them. Fast, so it finds the candidates." },
      { term: "Cross-encoder", definition: "A model that reads the query and one candidate together and scores how well they match. Slower and more accurate, so it only judges a shortlist. Here it is the search judge." },
      { term: "Relevance score", definition: "The single score each candidate carries through ordering. Signals and priors scale it by bounded factors instead of reshuffling ranks." },
      { term: "Signal", definition: "A bounded boost or penalty when the query names something exact, such as a film, character, shot scale, camera move, time of day or colour, that a candidate’s evidence satisfies or contradicts. Unknown evidence is neutral." },
      { term: "Prior", definition: "A bounded factor from fame and craft that settles near-ties but cannot overrule a clearly stronger match." },
      { term: "Preset", definition: "Balanced, Famous or Hidden gems: which way the priors lean." },
      { term: "Card", definition: "One search result. It stands for a whole dramatic scene, with other matching shots listed as alternatives." },
      { term: "Snapshot", definition: "A consistent version of the searchable tables. Search keeps serving the last complete one while a new one is built." },
      { term: "Fallback", definition: "The known-safe baseline search uses when a newer feature is incomplete." },
    ],
  },
  {
    id: "editing", title: "Editing", summary: "The words behind building an edit to a song.",
    terms: [
      { term: "Lab", definition: "The part of the app for editing and experiments, as opposed to ordinary search." },
      { term: "Project", definition: "A saved edit with its timeline and revisions." },
      { term: "Music map", definition: "Measured beats, downbeats, accents and loudness for the chosen part of a song." },
      { term: "Section and act", definition: "A section is a stretch of the song. An act is the plan for it: an intent, search queries, a fame target and a pace." },
      { term: "Cast", definition: "The step where the planner chooses specific shots to carry each act." },
      { term: "Assembly", definition: "The deterministic search that places the cast shots on beats." },
      { term: "Hold and flash", definition: "A hold keeps one shot across a stretch. A flash is a burst of short shots on a steady pulse." },
      { term: "Match cut", definition: "A cut where the outgoing and incoming pictures share a shape, position or motion." },
      { term: "Treatment", definition: "A per-edit choice of idea, pace, footage fame and what to avoid." },
      { term: "Harness", definition: "The editor pipeline of music map, concept, casting and assembly. Version 2 is the current default." },
    ],
  },
  {
    id: "system", title: "System", summary: "The words behind how the app keeps itself running.",
    terms: [
      { term: "Ingest", definition: "Turning a film file into a searchable library entry, followed by the evidence passes." },
      { term: "Worker", definition: "A background process that claims jobs. The ingest worker does library and GPU work; the editor worker does edits and renders." },
      { term: "Job", definition: "A unit of queued work stored in the database with a status: queued, running, done or error. Jobs survive restarts." },
      { term: "Lock", definition: "A rule that only one process may do a particular kind of work at a time, such as ingesting or publishing a film’s data." },
      { term: "Foreground work", definition: "Work someone is waiting on, such as a search, an ingest or a render, as opposed to optional maintenance that runs in spare time." },
      { term: "ADR", definition: "Architecture Decision Record: a numbered note that explains why a design choice was made. It is history, not specification." },
      { term: "Contract", definition: "The architecture document that describes the current system boundaries. It is kept current-only." },
    ],
  },
];

// The way footage is divided, from the whole film down to the frames search reads.
export const FOOTAGE_LADDER = ["Film", "Scene", "Shot", "Unit", "Keyframe"] as const;
