# ADR-0054: Caption-backed visual junk filtering

- Status: Accepted
- Date: 2026-09-14
- Supersedes: None
- Refined by: [ADR-0087](0087-conservative-whole-frame-blank-filter.md) for blank-frame detection only

## Context

An audit following ADR-0053 found a separate candidate-loss failure. The visual
junk classifier scanned caption, combined searchable text, and dialogue for
credit, logo, title-card, blank-frame, and static-artifact patterns. As a result,
spoken references to a "credit card" or a letter "written by" someone could
silently discard an otherwise ordinary scene.

The published library contained concrete examples: Her units ending `_0380`
and `_0381` discuss a credit card company; `_0466` and `_0467` discuss a letter
written by someone. Their captions describe people in ordinary interiors,
including a red shirt and a coral-red wall. Didi `_0512` and `_0513` discuss
E.T.'s end credits while showing a conversation. These were wrongly classified
as visual credits independently of query relevance.

## Decision

Use the stored visual caption alone to establish visual junk categories in the
shared result filter. Dialogue and `searchable_text`, which includes dialogue,
remain searchable evidence but cannot establish that the picture itself is a
credit roll, logo, title card, blank frame, or static artifact. Missing visual
caption evidence does not justify a visual-junk classification.

Preserve the existing patterns and explicit query-category overrides, with two
narrow caption forms added after checking the changed eligible rows: credit
overlays and title graphics. All About Lily Chou-Chou `_0120` explicitly
describes a centered Japanese credit overlay, and Cure `_0619` describes
Japanese title graphics. Both previously depended on subtitle credit text for
suppression. Recognize these visible-caption forms directly, and keep explicit
requests for credit overlays or title graphics available while respecting their
negation. Do not restore dialogue as visual classification evidence.

Apply the same boundary through the existing shared filter for ordinary, recipe, and
reference search. Keep retrieval channels, source resolution, lexical evidence,
ranking, deduplication, and diversity unchanged.

This is a query-time evidence correction. It adds no model, index, derivation,
backfill, or changes to raw films or timestamped evidence.

## Validation and limits

Regression tests cover the real Her and Didi dialogue triggers, including
duplicated dialogue in combined searchable text. A hybrid-search regression
keeps a red-shirt result while still suppressing an actual captioned credit
roll. Existing tests retain explicit credit-query overrides and hyphenated
credit-caption recognition.
Real caption regressions cover singular and plural credit overlays and title
graphics, explicit requests and negated requests, and the same wording appearing
only in dialogue over an ordinary scene.

This removes the demonstrated misuse of spoken evidence. The caption-based
regex filter remains an imperfect classifier: an incomplete or mistaken visual
caption can still miss or misclassify content. Raw subtitle wording is not a
safe substitute for that missing visual evidence.
