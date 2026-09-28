# ADR-0087: Require whole-frame evidence before suppressing blank footage

- Status: Accepted
- Date: 2026-09-20
- Refines: ADR-0054's blank-frame detection; its caption-only boundary remains

## Context

The frozen search comparison missed the Matrix shot whose monitor visibly
reads “Wake up, Neo…”. A bounded follow-up found the exact indexed unit first
in native full-text retrieval for the scoped literal query. Its visual caption
describes the green words against a black screen. The shared junk filter matched
“black screen” anywhere in the caption and rejected the unit for queries that
did not explicitly ask for blank footage. No surviving captured candidate crossed
the visual duplicate threshold for that exact unit.

This is a demonstrated filtering error, independent of hosted reranking. The
color of an object or background does not establish that the picture is empty.

## Decision

Make blank detection conservative: require caption evidence that the picture
itself is blank. Do not classify a shot as blank merely because a caption
mentions a black/white screen, describes dark imagery, or includes a fade within
otherwise meaningful content. Ambiguous content remains eligible.

Keep the shared caption-only classification boundary. Dialogue and combined
searchable text cannot establish visual junk. Preserve the other junk
categories, explicit query overrides and negation behavior. Credits, logos and
title cards retain their independent checks; a dark background does not exempt
them. A bounded stored-caption check exposed two explicit card descriptions
previously suppressed incidentally as blank: full-screen dense credit typography
with a cast list, and centered title text in an opening-card composition.
Recognize these narrow forms in their actual credit/title categories, preserving
their explicit and negated query behavior. Ordinary monitors, printed objects
and dialogue-only mentions are not evidence for those forms.

Use one shared correction across ordinary, recipe and reference search,
without title-specific exceptions or a new model.

Candidate generation, fusion, diversity, visual deduplication, film scope and
source authority remain unchanged. This is a query-time correction requiring no
re-ingestion, index rebuild or annotation rewrite. Existing long-lived API
processes need their next normal restart to load it; verification does not
authorize interrupting other active work.

## Verification and limits

Cover the real Matrix caption and ordinary visible content against dark/light
backgrounds, actual blank footage, mixed fades, explicit and negated blank
requests, and independent credits/title/logo exclusions. Verify the shared
retrieval path as well as the classifier. Inspect changed eligibility against
stored captions and retain the bounded diagnostic evidence.

The filter still relies on generated captions. Conservative rules can let some
unhelpful footage through; eliminating uncertain matches is not justification
for hiding meaningful scenes. The frozen comparison remains a record of its
original behavior; a later serving evaluation must have separate receipts.

The booth investigation found a separate limitation: retrieved neighboring
shots have high visual similarity to its inspected anchor, while the original
prompt has poor lexical evidence. This record does not change deduplication or
claim that the exact dense-candidate loss has been traced.
