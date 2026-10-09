import type { ProjectInfo } from "./types";

// Implementation defaults, maintained with the guide and architecture contract.
// These describe code choices, not loaded weights or per-film provenance.
export const GUIDE_MODELS = {
  understanding: "Gemini 3.8 Flash",
  reranker: "Qwen3-Reranker-0.6B",
  shots: "TransNetV2",
  flow: "RAFT-small",
  detector: "RF-DETR Small",
  segmentation: "RF-DETR Seg Small",
  pose: "RF-DETR Keypoint Preview XL",
  speech: "Silero VAD",
  beats: "Beat This!",
} as const;

export function buildModelGroups(config: ProjectInfo | null) {
  const unavailable = "Settings unavailable";
  return [
    { title: "Search", models: [
      { role: "Visual", name: config?.models.visual_encoder === "pe_core_l14" ? "PE-Core L/14" : config?.models.visual_encoder ?? unavailable },
      { role: "Semantic text", name: config?.models.text_encoder === "qwen3-embedding-0.6b" ? "Qwen3-Embedding-0.6B" : config?.models.text_encoder ?? unavailable },
      { role: "Reranking · optional", name: GUIDE_MODELS.reranker },
    ] },
    { title: "Film evidence", models: [
      { role: "Vision annotation", name: config?.models.annotator ?? unavailable },
      { role: "Understanding, highlights & optional critique", name: `${GUIDE_MODELS.understanding} · default` },
      { role: "Shot detection", name: GUIDE_MODELS.shots },
    ] },
    { title: "Picture measurement", models: [
      { role: "Camera flow · c_t_v2 checkpoint", name: GUIDE_MODELS.flow },
      { role: "Subject detection", name: GUIDE_MODELS.detector },
      { role: "Segmentation", name: GUIDE_MODELS.segmentation },
      { role: "Pose & keypoints", name: GUIDE_MODELS.pose },
    ] },
    { title: "Audio & editing", models: [
      { role: "Speech transcription", name: config ? `Whisper ${config.models.whisper}` : unavailable },
      { role: "Speech activity", name: GUIDE_MODELS.speech },
      { role: "Music understanding", name: config?.lab.music_model ?? unavailable },
      { role: "Edit planning", name: config?.lab.planner_model ?? unavailable },
      { role: "Beat timing · optional", name: `${GUIDE_MODELS.beats} · ${config?.lab.beat_checkpoint_configured ? "custom checkpoint" : "final0 default"}` },
    ] },
  ];
}
