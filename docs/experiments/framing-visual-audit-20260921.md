# Framing visual audit — 2026-09-21

Status: AI technical review completed; subsequent qualitative user feedback is
recorded below. Structured human preference and promotion gates remain
unmeasured. No production change or new feature extraction was performed.

The small pilot does not identify a universal winner. It does provide concrete
examples where appearance and layout disagree, where a different representation
recovers a useful match, and where all methods bury a plausible candidate. Keep
the bulk preparation batch held while these specific failures guide the next
bounded comparison. This audit does not evaluate Jev.

## What was reviewed

The frozen [representation pilot](framing-representation-pilot.md) contains
12 references (four tuning, eight held out), 512 candidates from eight films,
and 448 eligible cross-film candidates per reference. Seven methods include
global-only PE, three grid representations, and their fixed 65% global / 35%
grid blends. Every eligible candidate was scored; these are controlled offline
rankings, not live product rankings or a reproduction of its candidate shortlist.

- The primary AI reviewer viewed every reference and each method's first five
  results. Method names and film titles were hidden, with A–G labels shuffled
  independently for each reference. Notes were saved before unblinding.
- A second AI reviewer independently assessed ref03, ref07 and ref11 with the
  same blinded sheets. This is a consistency check, not independent human truth.
- A separate reviewer inspected all 512 candidate thumbnails without the
  rankings, then 23 candidates at full resolution, to nominate possible matches
  for ref03, ref09 and ref11. Full-resolution checks rejected at least one
  misleading thumbnail. The nominations are not exhaustive relevance labels.
- CPU replay of the cached descriptors reproduced **all 84 saved top-ten lists
  and scores exactly**, with descriptor hashes verified. This supplies the
  ranks below without new model or provider calls.

Original tuning/held-out labels are retained. Because the held-out images have
now been inspected, changes motivated by this audit need fresh held-out examples
before claiming generalization. No nDCG, recall percentage or user preference
score is inferred from these judgments.

## Findings grounded in the images

**Appearance can suppress a useful layout match.** In ref03, the reference is an
upright waist-up person left of centre beside a vertical architectural boundary.
The independently nominated Le Samourai frame C378 preserves much of that
arrangement, although its gaze differs. It ranks **1st with the final PE grid,
24th with the current-style final-grid blend, and 163rd with PE-Spatial**. The
blend's first result is instead a reclining woman. Both blinded reviewers found
C378 useful, without claiming the entire final-grid list was consistently best.

**Different representations recover different matches.** For ref07, both
blinded reviewers preferred the intermediate block-17 grid: its results better
preserve a large head on the right, facing left, with open space on the left.
Several other lists favour similar costumes/settings but reverse placement or
add a second person. For ref09, a lone standing dark figure, independently
nominated C438 ranks **58th with the final-grid blend, 6th with PE-Spatial, and
5th with its blend**. Yet PE-Spatial performs poorly on ref03. Changing to one
new model would exchange failures, not resolve them all.

**Some useful candidates are buried by every method.** C166 has a useful
standing stance, scale and placement for ref09, but its best rank is **43rd**.
For ref03, C155 matches part of the body framing and gaze, though a large gun
changes the foreground; its best rank is **31st**. These examples address the
concern about unseen candidates directly. They do not measure full-library
recall or establish an exhaustive set of good answers.

**Global appearance remains useful.** For ref05's reclining person, global-only
and the current-style blend both put a convincing reclining-patient analogue
C297 first; several grid-only lists lose the posture. The primary review found
PE-Spatial more useful for ref10's wide courtyard, while global-only results
often followed the fire rather than the arrangement. These are case-specific
AI observations, not aggregate wins.

**A poor result need not mean a good answer was hidden.** Neither blinded review
nor the independent pool inspection found a convincing cross-film match for
ref11's huge left-hand spacecraft and isolated small right-hand object. That
may be a limitation of this 512-frame sample; it says nothing about the whole
library. Several other references had only weak partial analogues.

Some raw grid lists retrieve title cards or nearly blank images. The pilot's
timeline pool does not replicate production junk filtering, so this does not
establish that live Framing returns those frames. Future evaluation should
record ordinary eligibility explicitly while preserving this frozen run.

## User feedback and deduplication follow-up

After viewing the comparison, the owner reported that repeated movies/results
make it feel insufficiently deduplicated and that framing similarity looks weak
across all methods. This is real qualitative feedback, not twelve scored
reference judgments. The pilot has not demonstrated framing quality worth a
full-library build; isolated useful examples must not obscure that conclusion.

The viewer shows raw model rankings from eight films, with the reference film
excluded. It does not apply the serving path's one-result-per-shot grouping,
visual suppression, reference temporal spread or film balancing. Repetition
therefore needs to be assessed separately from live search behavior. Preserve
raw rankings for diagnosis; a future product-like view should apply the same
explicit presentation policy to all methods.

A read-only repetition audit found that 33 of the 84 reference/method top-five
lists contain at least four results from one film; 14 contain five. No saved
top-ten list contains repeated shot IDs, identical file hashes or same-film
frame pairs within 30 seconds. This does not rule out perceptual duplicates or
repeated setups farther apart. The current-style final-grid blend averages
2.17 distinct films in its first five results versus 3.25 for the grid alone.
These describe this small raw pilot, not live-search diversity or quality.
The measurements are retained in `repetition-audit.json` alongside the audit.

Current serving deduplication also uses general image-vector similarity across
films (`_is_duplicate` in `pipeline/search/retrieve.py`). A similar-looking shot
from a different film can be a desired Framing answer, so more aggressive
suppression is not automatically safer. Proposed policy: group same-shot
instants, conservatively group near-identical same-source moments with access
to alternatives, and treat repeated films as a soft ordering preference rather
than duplicates. Do not delete retained evidence or impose one-result-per-film
limits. These are design proposals; no deduplication behavior changed here.

## Practical next step

Keep the three representations replaceable and leave ordinary search and the
bulk hold unchanged. Before treating fusion tuning as the remedy, establish
whether the image evidence captures the arrangement that matters. On a small
set with known useful alternatives, test an explicit subject-layout baseline
alongside the frozen embeddings: subject count, normalized position, relative
size and spacing. Existing Lab person-layout code is a reuse candidate for
person references; it is not a solution for architecture, objects, pose or
gaze. Non-person references must remain visible as a separate limitation.
Compare representation and presentation effects separately, retain the buried
matches as regression cases, and use fresh references for generalization.
Fusion remains a hypothesis to test, not an assumed fix. These are proposed
diagnostics, not a new serving design or a reason to store three full-library
descriptor sets.

Test preprocessing only if image inspection provides a specific suspected
failure; encoded bars and aspect distortion are hypotheses, not findings here.
Do not start a broad model sweep or resume the expensive backfill on the basis
of these few examples. The human review and serving cost gates in ADR-0082 and
ADR-0092 remain unchanged. User input can focus later on creative preference;
the present technical diagnosis does not require the user at the PC.

## Retained evidence

The original run is unchanged at
`pipeline/eval/runs/framing-representation-20260921/`. The source manifest hash is
`420d828ef49f66546b4b2995ead21d5046db36d569d02648829cdb486af71ea2`.

Audit notes, blinded arm mapping, candidate mapping, independent nominations,
exact ranks, replay/render helpers and a checksum receipt are retained at
`pipeline/eval/runs/framing-visual-review-20260921/`. Contact sheets remain in
`.tmp/framing-visual-review-20260921/`; cached descriptors remain in the original
pilot directory. These are local, git-ignored artifacts, not a remote backup.
The self-contained Framing method review canvas embeds the reviewed result
previews for later inspection. No paid calls, queue mutations, indexes or source
media were changed during this review.
