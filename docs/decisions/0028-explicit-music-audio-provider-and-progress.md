# ADR-0028: Explicit Music Sketch audio provider, progress and fade-in

- Status: Accepted
- Date: 2026-09-11
- Supersedes: [ADR-0024](0024-source-backed-lab-and-music-sketch.md) only for the default music provider and audio fade contract
- Superseded by: [ADR-0077](0077-independent-source-dialogue-clips.md) for the fade-in-only audio boundary and render profile

## Context

Repeated Gemini high-demand failures blocked listening, while the interface
gave little evidence of ongoing work. The user requested an OpenAI option and
a simpler music workflow with a basic adjustable fade-in. The original
source-backed project, bounded passage and grounded planning boundaries remain
appropriate; this failure does not justify an always-on search interpreter or
a larger orchestration system.

## Decision

Use explicit `lab.music_provider`, `lab.music_model` and `lab.planner_model`
settings independently of film annotation. Listening defaults to OpenAI
`gpt-audio-1.5`, supplying the actual
selected PCM audio to Chat Completions with text output. It is not a
transcription substitution. Keep Gemini available through an explicit
configuration; older configurations naming a Gemini model infer that provider.
Never change providers because of errors or available keys.

Use an explicit text planning model for the second, source-selection stage:
`lab.planner_model: gpt-5.6-terra`. It uses text-only Chat Completions with low
reasoning effort and strict JSON-schema output, omitting audio-specific
request options. The live integration rejected the all-text audio-model draft,
despite broad modality listings; a separate text model is a tested operation
boundary. Gemini planning inherits its selected music model when no planner
model is configured. Record planner model, contract and settings independently;
changing this stage must not require another audio interpretation.

The audio model's documentation does not advertise Structured Outputs. Prompt
for JSON and validate locally with the strict interpretation schema and
complete chronological passage coverage before applying a revision. Ground
every subsequent planned clip in the offered source ranges as before.
Interpretation caches and request receipts include provider, requested model,
hosted adapter contract, prompt/schema/settings and source inputs. Do not
claim immutable provider-resolved model lineage.

Disable response storage and SDK retries. Persist meaningful preparation,
rhythm, listening, validation, retrieval and planning stages. Streamed OpenAI
text supplies a writing/activity stage without displaying incomplete JSON as
editable results or presenting invented progress percentages. Use bounded
timeouts and retain cancellation/revision guards. Cancellation during a
hosted operation can wait for response events or timeout. Failed attempts
produce readable errors and receipts; they never automatically replay or
fall back to another provider.

Add backward-compatible `audio_fade_in_seconds` to project documents, default
zero. Require a finite duration between zero and the selected passage length.
The `decoded-reel-audio-fade-v2` render manifest records the value. Apply a
linear gain ramp after source trimming and timestamp reset, starting at the
passage's zero. Preview and download use the same rendered file; original
audio remains unchanged. This is one fade-in, not a general audio-effects or
mixing framework.

## Consequences

- Existing source-backed projects, locked clips and revisions remain valid.
  Old interpretation caches remain preserved but are not confused with the
  new provider contract.
- A single authorized 12-second synthetic melody request completed through
  OpenAI in 5.48 seconds and returned three validated segments. This verifies
  live audio input and output mechanics, not real-music interpretation quality.
- Single-attempt transport tests cover busy/rate-limit/auth/model failures;
  real render tests check the fade envelope at a nonzero source passage start
  and preserve the original hash.
- A live one-slot text-only request through `gpt-5.6-terra` returned a valid
  offered source choice in 3.25 seconds. An initial text-model request rejected
  the unnecessary `modalities` parameter; the text adapter now omits it.
  Safe failure receipts retain HTTP status, error code/parameter and request
  ID without saving raw provider bodies, prompts, audio or credentials.
- Human review of three real music passages and the source-grounded draft
  remains required before claiming editorial usefulness. Ordinary search and
  production matching activation remain unchanged.

## Provider references

- [OpenAI audio in Chat Completions](https://developers.openai.com/api/docs/guides/audio-chat-completions)
- [GPT-Audio-1.5 model contract](https://developers.openai.com/api/docs/models/gpt-audio-1.5)
- [GPT-5.6 Terra model contract](https://developers.openai.com/api/docs/models/gpt-5.6-terra)
