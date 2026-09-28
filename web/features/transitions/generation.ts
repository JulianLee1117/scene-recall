export type GenerationResolution = "480p" | "720p" | "768p" | "1080p";
export type PromptExpansion = "disabled" | "balanced" | "quality";
export interface GenerationDraft { parent_render_id: string; model?: string; prompt: string; duration: number; resolution: GenerationResolution; seed?: number | null; prompt_expansion_mode?: PromptExpansion | null; }
export type QuoteRequest = Omit<GenerationDraft, "seed">;
export interface GenerationModel {
  id: string; label: string; max_prompt_length: number;
  defaults: { duration: number; resolution: GenerationResolution; prompt_expansion_mode?: PromptExpansion };
  capabilities: { first_last_frames: boolean; min_seconds: number; max_seconds: number; durations: number[]; resolutions: GenerationResolution[]; seed: boolean; prompt_expansion_modes: PromptExpansion[] };
  pricing?: { version: string; credits_per_second: Record<string, number>; min_credits: number; max_request_credits: number };
  input_dimensions?: Record<string, Record<string, string>>; documentation_url?: string;
}
export interface GenerationProvider {
  provider: string; model: string; configured: boolean; generation_enabled: boolean; live_verified: boolean;
  default_model?: string; models?: GenerationModel[];
  credential_environment: string; pricing?: { version?: string; max_request_credits: number };
}
export interface GenerationQuote {
  provider: string; model: string; request: QuoteRequest; estimated_credits: number; estimated_usd: number;
  price_version: string; parent_manifest_sha256: string; configured: boolean; generation_enabled: boolean;
  within_request_ceiling: boolean; aspect: string; ratio: string | null;
  input_dimensions?: { width: number; height: number }; output_shape_guaranteed?: boolean;
}
export interface GenerationReview { key: string; request_id: string; quote: GenerationQuote; }
export interface GenerationRequest extends GenerationDraft {
  request_id: string; confirm_spend: true; max_credits: number; price_version: string; parent_manifest_sha256: string;
}
export interface GenerationJob {
  id: string; status: "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted";
  request: GenerationRequest; progress?: string | null; error?: string | null; cancel_requested?: boolean; receipt_url?: string | null; original_url?: string | null;
  storage?: { output_bytes: number; retained_bytes: number; original_bytes: number; scratch_bytes: number } | null;
  result?: { preview_url: string; manifest_url: string; original_url: string; receipt_url?: string;
    duration: number; transition_start: number; transition_end: number; provider?: string; model?: string;
    task_id?: string; estimated_credits?: number; provenance_status?: string; } | null;
}

export const generationPending = (job: GenerationJob) => job.status === "queued" || job.status === "running";
/** URLs alone are not evidence that an interrupted download retained a file. */
export function retainedGenerationOriginal(job: GenerationJob): string | null {
  if (job.cancel_requested || !["completed", "failed", "interrupted"].includes(job.status)) return null;
  const bytes = job.storage?.original_bytes;
  return typeof bytes === "number" && Number.isFinite(bytes) && bytes > 0
    ? job.original_url || job.result?.original_url || null : null;
}
export const generationKey = (draft: GenerationDraft) => JSON.stringify([draft.parent_render_id, draft.model ?? "seedance2_5", draft.prompt, draft.duration, draft.resolution, draft.seed, draft.prompt_expansion_mode ?? null]);
export const dollars = (value: number) => `$${value.toFixed(2)}`;

// Compatibility for a server or saved request from before the model registry was added.
export const DEFAULT_GENERATION_MODEL: GenerationModel = { id: "seedance2_5", label: "Seedance 2.5", max_prompt_length: 15000,
  defaults: { duration: 4, resolution: "720p" }, capabilities: { first_last_frames: true, min_seconds: 4, max_seconds: 8,
    durations: [4, 5, 6, 7, 8], resolutions: ["480p", "720p", "1080p"], seed: true, prompt_expansion_modes: [] } };
export const generationModels = (provider: GenerationProvider | null) => provider?.models?.length ? provider.models : [DEFAULT_GENERATION_MODEL];
export const generationModelTitle = (model?: string, models: GenerationModel[] = []) => models.find((item) => item.id === model)?.label
  ?? ({ seedance2_5: "Seedance 2.5", h3_max: "MiniMax H3 Max", wan3: "Wan 3" }[model ?? "seedance2_5"] ?? model);

export function quoteRequest(draft: GenerationDraft, selected?: GenerationModel): QuoteRequest {
  const model = selected ?? DEFAULT_GENERATION_MODEL;
  if ((draft.model ?? "seedance2_5") !== model.id) throw new Error("Refresh the available models before quoting this request.");
  if (!draft.parent_render_id) throw new Error("Render a source pair before requesting a generation quote.");
  if (!draft.prompt.trim() || draft.prompt.length > model.max_prompt_length) throw new Error(`Add a transition direction of 1–${model.max_prompt_length.toLocaleString()} characters for ${model.label}.`);
  if (!model.capabilities.durations.includes(draft.duration)) throw new Error("Choose a duration supported by this model.");
  if (!model.capabilities.resolutions.includes(draft.resolution)) throw new Error("Choose a resolution supported by this model.");
  if (!model.capabilities.seed && draft.seed != null) throw new Error("This model does not support a seed.");
  if (draft.seed != null && (!Number.isInteger(draft.seed) || draft.seed < 0 || draft.seed > 4294967295)) throw new Error("Seed must be a whole number from 0 to 4,294,967,295.");
  if (draft.prompt_expansion_mode != null && !model.capabilities.prompt_expansion_modes.includes(draft.prompt_expansion_mode)) throw new Error("This model does not support that prompt expansion setting.");
  const { seed: _seed, ...request } = draft;
  return request;
}

export function generationRequest(draft: GenerationDraft, review: GenerationReview, model?: GenerationModel): GenerationRequest {
  const request = quoteRequest(draft, model), quote = review.quote;
  if (review.key !== generationKey(draft) || (quote.model ?? "seedance2_5") !== (draft.model ?? "seedance2_5")
    || (quote.request.model ?? "seedance2_5") !== (draft.model ?? "seedance2_5")
    || (quote.request.prompt_expansion_mode ?? null) !== (draft.prompt_expansion_mode ?? null)
    || Object.keys(request).some((key) => key !== "model" && key !== "prompt_expansion_mode" && request[key as keyof QuoteRequest] !== quote.request[key as keyof QuoteRequest]))
    throw new Error("Settings changed. Request a fresh quote before generating.");
  if (!quote.within_request_ceiling || !Number.isInteger(quote.estimated_credits) || quote.estimated_credits < 1 || quote.estimated_credits > 300
    || !Number.isFinite(quote.estimated_usd) || quote.estimated_usd <= 0 || quote.estimated_usd > 3)
    throw new Error("This quote exceeds the $3.00 request ceiling. Choose a shorter duration or lower resolution.");
  if (!quote.configured || !quote.generation_enabled) throw new Error("Configure the Runway connection before generating.");
  if (!quote.price_version || !/^[a-f0-9]{64}$/i.test(quote.parent_manifest_sha256) || !/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(review.request_id))
    throw new Error("This quote is incomplete. Request a fresh quote before generating.");
  return { ...draft, request_id: review.request_id, max_credits: quote.estimated_credits, price_version: quote.price_version,
    parent_manifest_sha256: quote.parent_manifest_sha256, confirm_spend: true };
}

/** These responses reject the request before a generation job is accepted. Network/5xx failures remain uncertain. */
export function generationRejected(reason: unknown): boolean {
  return !!reason && typeof reason === "object" && "status" in reason && [400, 401, 403, 404, 409, 413, 422].includes(Number(reason.status));
}
