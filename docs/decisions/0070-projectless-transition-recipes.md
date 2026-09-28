# ADR-0070: Projectless transition recipes and manual AI handoff

- Status: Accepted
- Date: 2026-09-16
- Extends: ADR-0024, ADR-0025, ADR-0051 and ADR-0068
- Preserves: ADR-0067's independent experimental entry boundary
- Supersedes: None

## Context

The editor renders source-backed cuts but has no place to compare more expressive
joins before making them an editing capability. Transition quality depends on the
chosen moving clips and precise timing; a preset name or attractive still cannot
establish a useful result. Local compositing and generated bridges also have
different provenance, repeatability and cost boundaries. The user requested a
separate lab to experiment with these capabilities before editor integration.

## Decision

Register Transitions as a session at `/lab/transitions`, entered through Labs and
using the shared workspace header. Do not create edit projects for source
selection, controls or renders. Explicit renders enqueue projectless jobs in the
existing durable ledger and run on its editor worker. Each job freezes one pair
of indexed film windows and a parameterized recipe; prior variants remain
available for restoration and comparison.

Admit a separate local renderer for bounded two-clip compositing: whip pan, soft
wipe, luma reveal, light flash, dip to black and cross dissolve, with a hard cut
for comparison. Restrict source windows to at most twelve seconds each and
transition overlap to at most two seconds, shorter than either window. Version
the renderer contract and retain its source/time anchors, recipe parameters and
output settings in a downloadable manifest. Resolve source paths only on the
server and revalidate frozen source identity before rendering. No search model,
embedding profile or shared evidence derivation changes.

Use the existing UUID job render namespace. Retain completed MP4s, manifests and
outgoing/incoming endpoint JPEGs with their live jobs; remove explicit temporary
clips after renderer teardown. Cancellation and interrupted-job handling reuse
the existing worker contract. Original films remain unchanged and no new cleanup
namespace or background service is introduced.

Support manual AI experimentation by downloading source endpoint frames and
preparing a bridge prompt with provider/model notes. The user performs any
external generation separately. A returned local video may be auditioned in the
browser, without uploading it or persisting it as a verified generated artifact.
The application makes no hosted generation requests. This is not a provider
integration or a claim that a particular model can honor both endpoints.

Keep the existing music project schema, cut renderer and editing capability
catalog unchanged. Promoting local or generated transitions into the editor
requires a separate decision supported by played comparisons. Evaluate visual
continuity and creative usefulness independently of successful encoding; for
hosted generation, also establish provider input/output contracts, explicit cost
bounds and durable source/model/request provenance before execution is added.

## Consequences

- Transition experiments can be tuned and replayed on exact source windows
  without creating or changing an edit project.
- Durable local variants share the established worker, cancellation and artifact
  lifecycle. Unrendered browser settings and returned external videos are not
  durable artifacts.
- Parameterized local effects are reproducible under their recorded renderer
  profile. Generated bridges remain external experiments with weaker provenance.
- The lab adds no paid calls, automatic editorial decisions, provider fallback,
  library-wide processing or new vector space. Editor integration remains gated
  on observed useful results.
