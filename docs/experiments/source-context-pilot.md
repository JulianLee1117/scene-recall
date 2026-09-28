# Source-context pilot — 2026-09-15

The optional context layer is implemented under [ADR-0066](../decisions/0066-source-context-pilot.md).
The first backfill completed all twenty windows across Parasite, Wild Strawberries
and Moonlight. A cache-only replay reused all twenty artifacts with zero new
provider calls. **Keep `lab.context_profile: null`: the initial producer still
makes factual mistakes, and creative benefit has not been established.**

## Implemented boundary

- Immutable source-context artifacts retain source identity, model/prompt/input
  versions, supporting timestamps, uncertainty and explicit coverage. A profile
  pins compatible producers across films; historical artifacts remain readable.
- The independent `pipeline.context.build` command prepares bounded windows from
  indexed frames, captions and structurally checked timed dialogue. It defaults
  to dry-run, resumes completed work and never automatically retries an uncertain
  or failed hosted attempt.
- The editor can read cached context after candidate retrieval. It freezes the
  exact supplied packet and artifact IDs alongside the normal selector receipt.
  Source offers, legal trims and response validation remain authoritative.
- The private `pipeline.experiments.context_selection` harness compares frozen
  context-off/on inputs and preserves choices, receipts and factual-audit material.

No ordinary search representation, category UI, saved project, raw film or
ingestion queue was changed by this pilot. The default editor path performs no
context I/O. Song-specific metaphor remains an editorial reading, separate from
claims about what happened in the original source.

## Initial evidence

Each of the twenty explicit windows spans 180 seconds. The producer received up
to sixteen existing sampled keyframes per window, ordered captions and available
timed dialogue. These windows were selected across three films; this is not a
full-film analysis or an exhaustive test set. Some neighboring windows overlap.

The twenty successful `gpt-5.6-terra` requests used 172,002 input and 18,245 output
tokens (190,247 total; zero reported cached input). The output total includes
1,717 reasoning tokens. Hosted elapsed time summed to 18 minutes 16 seconds;
the first-start/last-finish receipt span was 18 minutes 39 seconds, excluding
local setup and finalization outside those receipts. No dollar cost is inferred.
The twenty sequence records retain 252 claims, including twenty producer
uncertainty statements, and 1,450 evidence occurrences: 502 captions, 628 dialogue
cues and 320 sampled keyframes. Claim status is the producer's judgment.

The agent spot audits found:

- **Parasite, 640.188–820.188:** a frame correctly overruled a caption describing
  a young man as a woman. One claim also used “dim interior” without adequate
  support from its cited tight frame and surrounding outdoor sequence.
- **Wild Strawberries, 385.433–565.433:** the record described the coffin/carriage
  imagery but left its literal-versus-dream status unknown. The supplied window
  does not establish the wider narrative meaning. The record also flagged a
  captioned wheel collapse as unsupported by the sampled images.
- **Moonlight, 1449.632–1629.632:** claim-5 repeated the caption's “older gray-haired
  man” description. Its cited images at 1539.632 and 1550.080 seconds show a child
  with white lather on their hair. This is a material contradiction despite the
  claim having image citations and the producer assigning `supported` status.
  Other inspected observations were consistent with their images; dialogue was
  not verified against audio.

These are limited agent spot checks, not human acceptance or a measured accuracy
rate. The original generated records are retained unchanged so the failures are
auditable. `supported` means the producer supplied citations; it does not mean a
human or independent verifier accepted the statement.

The current producer emits one sequence record for each window. Evidence times
can identify particular events, but the record's applicability covers the whole
window. Nearby claim selection limits prompt size; it does not prove that every
claim is visible in every offered shot. No full plot, character continuity,
reliable motives, exact action endings or universal symbolism is claimed.

## Private comparison design

The comparison uses a previously frozen Nocturne listening/document snapshot and
its 78.99–108.99-second music passage. A private evaluation brief asks for a
progression from emotional isolation, through attempted connection, to unresolved
distance. This is an editing intention, not a claim about the lyrics.

Both variants receive the same six curated candidates (two per film), their
original captions, three fixed ten-second slots, source authority and music
evidence. Only the cached context packet and its handling guidance differ. Rank
is presentation order, explicitly not a retrieved relevance score. This isolates
selection from retrieval and cut timing; it cannot establish better search
recall, better action boundaries or general creative superiority.

The real six-source preparation exposed early records consuming the entire
context budget. The adapter now shares the serialized character budget across
records, retaining complete claims and explicit omissions. The frozen packet
contains all six records in 22,258 serialized characters, with 3, 4, 4, 3, 4 and
4 supplied claims respectively. Every record retains its overall uncertainty
warning. This fixes unequal packing; it does not eliminate the explicit film,
candidate or record coverage caps, nor guarantee an unbiased model preference.

Private renders use the existing renderer, recheck original film identities and
registered music bytes, and apply the existing repeated-footage guard. A rejected
choice remains a gap; no new candidate or model retry repairs the result. Saved
projects are untouched. Human factual and played preference grades remain pending.

## Comparison result and verification

Both selector requests completed with three legal choices and no gaps. The
context-off sequence was Moonlight's bath shot, Parasite's table conversation,
then Wild Strawberries' car conversation. Context-on kept the bath opening,
moved the car conversation into the middle, and ended with Moonlight's phone
close-up. Two of three source choices changed; the car excerpt's source start
also changed. Three ten-second music slots stayed fixed.

Both opening explanations called the bath occupant an older man. The context-on
explanation explicitly relied on the supplied context, so the new layer did not
correct this known error and gave the selector another source repeating it.
Sampled output frames show the child with white lather, the table exchange, the
car occupants and the phone close-up. No human creative preference is recorded.
One generated pair cannot distinguish every contextual effect from model
variation or establish a general benefit.

The context-off request used 9,007 input / 373 output tokens and took 49.0 seconds;
context-on used 16,400 input / 482 output tokens and took 50.0 seconds. These are
single-run measurements, not a latency benchmark. The pair used two additional
hosted calls, with no new retrieval, listening or context analysis. Combined
backfill and comparison usage was 22 calls and 216,509 reported total tokens.

The two private MP4s are under `assets_dir/lab/renders/` in
`context-pilot-20260915-context-off/` and `context-pilot-20260915-context-on/`.
Both decoded without errors: H.264, 1280×720, 720 frames / 30 seconds, with
30-second AAC audio. Decoded music hashes match exactly; the music is non-silent.
Source repetition guards rejected no choices. `pair/render-verification.json`
retains media hashes, stream checks and sampled-frame paths.

The full backend suite passed **2,462 tests, 9 skipped** before the last packing
refinements. After those refinements, the focused context, configuration and
selector suite passed **230 tests, 1 skipped**. The skip is the Windows symlink
privilege case. Tests cover independent publication/replay, source and citation
integrity, profile compatibility, fallbacks and budgets, uncertainty retention,
unchanged candidate/trim authority and exact frozen selector inputs. These
mechanical passes do not validate the model's factual or creative judgments.

## Retained artifacts and next gate

Local execution artifacts are under
`C:/Users/julia/Videos/cinema-assets/lab/context-pilot-20260915/`:
`plan.json`, `anchors.json`, `build-report.json`, `cache-replay.json`,
`agent-spot-audit.json`, the frozen selection input and comparison outputs.
Immutable source records and provider
receipts are under `assets_dir/context/`; the comparison freezes what it actually
supplied even if a later backfill replaces an active profile manifest.

The next decision is whether a revised producer can improve the audited failures
and whether people prefer its played edits under a controlled comparison. Preserve
this run as the baseline. Do not expand to library-wide context, a context search
index or automatic activation from successful JSON validation alone.
