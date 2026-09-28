# ADR-0056: Automated subtitle checks and conservative embedded fallback

- Status: Accepted
- Date: 2026-09-14
- Supersedes: ADR-0022's mandatory subtitle-choice boundary and ADR-0014's intake validation, automatic selection and embedded-stream eligibility rules
- Amends: ADR-0047's shared subtitle validation and review defaults
- Amended by: [ADR-0057](0057-conservative-release-layout-selection.md) for nested and single-video managed-root subtitle association
- Superseded by: [ADR-0079](0079-validate-extracted-embedded-dialogue.md) for metadata-only embedded acceptance and cache reuse; external subtitle rules remain in force

## Context

Manual and managed intake can pause on a complete English subtitle solely
because its filename lacks a language marker. A short excerpt asks the person
importing a film to assess language and quality without enough evidence. In the
opposite direction, an English-marked filename and a few parseable cues can
promote a mislabeled, malformed or partial track. The existing parser is
deliberately permissive and supplies a minimum content floor rather than a
whole-file validity check.

The user requested automatic assessment for both intake paths. Acquisition must
remain independent of the inference worker, preserve raw evidence and avoid
adding hosted checks or a language model to its monitor. Structural and text
heuristics can support conservative selection, but cannot establish exact
synchronization, translation accuracy or the matching film cut.

The embedded fallback also previously chose the first convertible text stream,
which could be a forced or foreign track even when a full English alternative
was present. The same workflow needs a conservative metadata choice without
claiming that external SRT content validation has also inspected embedded text.

## Decision

Add a versioned, bounded, deterministic full-file SRT validator separate from
the legacy dialogue parser. Check that every cue block parses, timestamp fields
are valid, intervals are positive, control characters are acceptable and cue
times fit the measured film duration. Reject structurally invalid, oversized,
trivial and promo-only candidates from intake selection without modifying them.

Automatic selection additionally requires English lexical and script evidence
across portions of the track, sufficient diverse dialogue and density, and
broad temporal coverage. Check the entire bounded file for structural problems;
do not infer validity from only the preview. Language and coverage rules are
conservative heuristics: weak or conflicting evidence blocks automatic
selection, while an eligible candidate can remain available for explicit review.
An English filename is supporting evidence rather than a sufficient condition.

Use one shared intake resolver for manual and managed sources. Retain positive
filename association or generic English-label association within a matching
release, and the existing forced, commentary, known foreign-marked,
extra-associated and unassociated exclusions. A single passing track can be
selected automatically even without an English filename marker. Retain the
ordinary-track preference over accessibility variants. Multiple conflicting
tracks remain separate review choices and are never merged or guessed between.
Byte-identical copies within that preferred, automatically eligible pool count
as one content choice, using the validated full-file SHA-256. Select the first
path in the resolver's stable order while retaining every original and review
handle; no text normalization or approximate duplicate matching is performed.

Offer automatic selection by default in manual and managed review, with
explicit use and skip still available. An automatic decision uses a uniquely
passing external track; otherwise it leaves external tracks in the release and
allows the existing embedded-subtitle or local Whisper ingestion fallback.
Managed acquisitions with uncertain choices can still pause for review, whose
default automatic option lets the user continue without certifying a subtitle.
Explicit selections must be exact current eligible root-relative handles and
are revalidated before any source mutation. An explicit choice does not bypass
structural eligibility or certify synchronization and translation quality.

Keep manual `GET /incoming` free of video probing and hashing. Obtain duration
through a bounded local metadata-only probe during import. Managed validation
passes the duration from its existing metadata and sampled-decoding inspection
to the resolver before subtitle selection. No GPU, language-model inference or
hosted subtitle check runs in either the API or acquisition monitor. Whisper,
when needed, continues to run inside the existing ingestion queue.

Copy a selected SRT byte-for-byte beside the canonical film before moving the
film, retaining every original release track. Preserve managed acquisition's
frozen import plan, subtitle content hash and source identity checks so restart
recovery cannot silently substitute evidence. Existing canonical external
sidecar precedence, eligibility and dialogue parsing remain unchanged.

For embedded fallback, require an FFmpeg-convertible text subtitle codec, a
valid absolute stream index and an `en` or `eng` language tag. Exclude forced
or commentary dispositions and titles identifying forced, commentary, signs
or songs tracks. Prefer ordinary English tracks over SDH/CC/HOH or
hearing-impaired variants. Within that preferred pool, select its sole candidate
or, if several remain, its unique default. Unknown-language, absent or ambiguous
eligible tracks use the existing local Whisper fallback. This metadata policy
does not inspect embedded subtitle text or verify its language, completeness,
translation or audio synchronization.

Keep the existing dialogue manifest format, which already records source kind
and selected embedded stream index. A changed choice invalidates the affected
cache naturally on the next ingest; an unchanged choice remains reusable. No
automatic library-wide revalidation, backfill or inference replay is scheduled.

## Consequences

- Complete, clearly English associated tracks can be used without asking the
  user to judge an excerpt or repair a filename.
- Obvious malformed timing, misleading language labels and likely partial
  tracks no longer qualify automatically merely through an English marker.
- Valid sparse, mixed-language or unusual films can fail conservative automatic
  gates. Review and the existing ingestion fallback remain available.
- An ineligible first embedded stream no longer overrides a clear ordinary
  English alternative. Uncertain metadata can instead require local Whisper.
- Passing checks means the track fits the deterministic intake rules; it is not
  proof of completeness, audio synchronization, film-cut identity or translation
  accuracy.
- Raw timestamped evidence, archive boundaries, acquisition recovery and
  model-scoped derived data retain their existing contracts.
