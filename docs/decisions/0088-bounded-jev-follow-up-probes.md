# ADR-0088: Isolated Jev source-constraint and category probes

- Status: Accepted for experiment only
- Date: 2026-09-20

## Context and decision

The first frozen comparison found mixed framing results, evidence-source mistakes,
and limitations from missing or incorrect stored descriptions. The user requested
another experiment. Extend ADR-0086 with two separately reported diagnostic probes:
an unchanged-evidence prompt/scoring ablation and query-only independent category
judgments. The [predeclared protocol](../experiments/jev-round-two.md) owns the
fixed cases, scoring, agent-label limitations and bounded call/cost admission.

The new probe executor reuses the existing Decisions HTTP transport but records
its own versioned plan, exact question schema, pre-transport attempt journal,
responses, actual model, cost and transport timing. It defaults to dry execution,
uses exclusive new output directories and never retries automatically. Unknown
cost stops further hosted requests. Expected labels and baseline ranks are not
provider inputs. No provider call is made by normal search.

The original ADR-0086 policy and receipts retain their identity. Alternative
scoring is isolated to the probe and cannot alter the candidate set, source
evidence or tail. Category labels never become hard filters, change confirmed
movie scope or activate a production route. Explicit contradictions and unknown
evidence remain different states; a correct classification of a false caption
does not verify its source image.

## Promotion gate

Report agent textual agreement separately from source relevance and human
preferences. This targeted sample cannot justify automatic routing or a default
reranker. Held-out relevance, failure behavior and interaction evidence plus a
separate architecture decision remain required for production activation.
