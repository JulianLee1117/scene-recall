# ADR-0118: Keep displayed, played and saved moments consistent

- Status: Accepted
- Date: 2026-10-10
- Extends: ADR-0006 (durable saves), ADR-0098 (focus spans), ADR-0101
  (folded matches), ADR-0117 (matched words and shot dialogue)

## Context

Using the previous hover iteration exposed three concrete failures:

- For `i am going to`, a Jaws result had only quote and dialogue-only lexical
  support. Its 0.7143 ordered overlap missed the 0.8 presentation gate, hiding
  its reason for appearing. Channel ranks are not comparable confidences.
- Folded matches were shown in relevance order under “in this scene”, although
  visual deduplication deliberately includes similar shots elsewhere in a film.
- For `learning to swim @Moonlight`, the card's hero at 1105 seconds showed
  Chiron floating, but opening and saving preferred an incidental indexed
  frame at 1114.999 seconds showing both characters upright.

This continues the small UX iteration recorded in ADR-0117: use and human
feedback revealed failures that a plausible first implementation did not catch.

## Decision

1. Keep one hover snippet. The backend also selects an admitted partial quote
   when spoken words are the only retrieval support. Explicit Words priority,
   strong quotes and selected semantic evidence retain their existing rules.
   The browser trusts that clause-owned selection. No rank comparison or
   retrieval change is involved. Quote and semantic Dialogue remain separate
   indexes over the same published source dialogue, serving different matching
   methods; they are not competing transcripts.
2. Select folded matches by relevance as before, but display their moments
   chronologically as **Matching shots**. Open on the best result, even when
   an earlier alternative sorts first. Each alternative retains its own scene
   and source metadata instead of borrowing the representative's context.
   Its facts and dialogue update on explicit selection and stay stable as
   playback advances.
3. Use one display-moment resolver for visual playback and Save. Keep a Saved
   timestamp, displayed hero/indexed frame, and indexed search reference
   distinct. Do not start visual playback a second earlier across a picture
   boundary. Timed spoken excerpts retain their short lead-in.
4. Preserve saved timestamps across hero repicks and compatible reingestion.
   Reuse an indexed still only when its actual time matches. Otherwise read a
   timestamped still from the source with bounded CPU decoding and a bounded
   fingerprint-aware memory cache. Keep the saved still on hover if the current
   focus span does not establish a matching preview. Do not rewrite old saves:
   their original user intent cannot be inferred retrospectively.

## Consequences

No new model, embedding space, film backfill or durable image cache is needed.
Saved thumbnails can require a small source decode; resource bounds prevent
unbounded CPU fan-out. Saved-list hydration batches projected metadata from one
pinned snapshot; live verification caught an expensive per-bookmark timestamp
scan, which was removed before shipping. Source timestamps remain the durable truth and indexed
frame IDs remain replaceable lookup hints. Regression tests cover the observed
partial-quote gate, mixed-picture shot, alternative ordering and metadata,
save round-trip, republished heroes and decode limits.
