# ADR-0043: Song meaning, controlled edit density and fresh footage

- Status: Accepted; amended by [ADR-0105](0105-song-profile-and-treatment.md): harness v2 planning identifies the song from its track name (listening still does not)
- Date: 2026-09-13
- Extends: ADR-0032, ADR-0033, ADR-0041 and ADR-0042
- Supersedes: audio-proposed moment count as whole-edit shot-count authority; pacing as an unenforced adjective; automatic repeated source windows
- Superseded by: [ADR-0060](0060-music-led-whole-passage-timing.md) for combined timing/direction planning, the three-hosted-role limit and enforced pacing density; song meaning and source reuse rules remain active

## Observed failure

For the user's 90-second Nothing Lasts Forever passage, Balanced generated 12
shots, then Energetic generated 10 shots mostly lasting 7.5–10 seconds. Settings
and cache scoping were correct; the audio model chose the count and the downstream
selector could only adjust nearby cuts. The new edit repeated the same Yi Yi unit
twice, with all 7.5 seconds of the second selection overlapping the first despite
23 other offers. Its final Didi selection overlapped the previous edit by 4.659
seconds. The model justified the Yi Yi repeat as a motif. Regeneration had no
previous-footage context. The saved song summary described generic musical emotion;
the prompt discouraged lyric transcription and had no separate vocal-meaning field.

## Decision

Keep the existing three hosted roles: listen, plan, select. Do not add a retry loop,
new service, model switch, required user feedback or search-ranking change.

**Listening:** interpretation contract `vocal-meaning-and-music-evidence-v7` adds
bounded `song_meaning`: vocal-understanding status, concise summary, up to six
themes, up to eight approximately timed heard paraphrases with confidence, and
uncertainty. Do not return a full lyric transcript or identify a song from its
filename. Understood content requires heard cues; unclear/absent vocals cannot
establish lyric themes. Shift and validate cue times against the original passage.
Preserve model/track/passage provenance and expose only current valid meaning in
`shared-music-and-vocal-meaning-v2`. Legacy analysis remains readable with meaning
unknown. User-supplied interpretation remains separate and takes precedence as
creative direction. Missing words do not justify inventing a hopeful resolution.

**Whole-edit planning:** the existing text planning stage now owns shot count,
variable cut positions and search directions together. The private output is
`shots[{end_frame, direction}]` plus a visual plan, immediately validated and
materialized into the existing timeline. Average-duration bands over the complete
passage are Patient 8–14 seconds, Balanced 5–8, and Energetic 2–4, bounded to 32
shots and available output frames. For 90 seconds these yield 7–11, 12–18 and
23–32 shots. Validate the count and complete legal coverage. Individual holds or
clusters can fall outside those averages; musical evidence and visual development
guide boundaries. Uniformity is a diagnostic, not an aesthetic rejection rule.
The 2026-09-14 Rapid Nocturne retry exposed an unbounded hosted `end_frame`:
its second section had 540 output frames, but the planner proposed 543 and 565
before returning to the required 540 endpoint. Scope the hosted numeric maximum
to the section's frame count as well as validating it locally. Keep strict order
and full coverage checks; do not sort, clamp or automatically retry invalid plans.
Audio editorial moments remain contextual suggestions and compatibility input for
standalone analysis, not the whole-edit count. This path applies to untouched
starters, pristine unprepared edits and explicit regeneration. Fixed/manual/per-shot
work remains fixed. The existing source-aware selector then chooses footage and
bounded nearby cuts. Persist the pace budget and chosen/final duration statistics.

**Footage:** privately pass previously placed sources to regeneration retrieval.
Exclude those units and overlapping film windows before the final offer limit.
The previous saved clip bin is not a blanket exclusion. Automatic selection cannot
reuse an indexed unit or overlap already retained/selected footage: convert a later
repeat into an explained abstention, preserving an old targeted clip when relevant.
Do not substitute an arbitrary fallback. Record excluded counts and repetition
reasons; recurring motifs should use different footage. Manual search and placement
still permit deliberate reuse. These checks apply to the Lab assembler, not main
search ranking or the ingestion/index representation.

**UI:** retain the existing controls. Pace choices show their average-duration bands
and 32-shot limit. Music analysis separates Song meaning from Musical atmosphere and
can reveal the model's heard paraphrases, approximate song times and uncertainty.
User notes remain optional. Progress continues to expose actual stages and counts.

## Validation and limits

The actual saved generations form the regression: changed count must obey the pace
budget, and the two observed reuse cases must be excluded or flagged. Cover source
time, stale/invalid meaning packets, unclear vocals, count/frame validity, fixed-cut
protection, first/whole generation, scoped cache identity, cancellation and atomic
revision recovery. A new live run should inspect extracted meaning, cut count,
duration variation and reuse separately from subjective montage quality.

The live comparison produced 30 slots and 26 distinct placed units across 17 films,
with no internal source overlap or reuse from the previous ten placed shots. Four
abstentions total ten seconds. The heard meaning now concerns relationship
dysfunction, distance and letting go, but remains a model interpretation. Most
durations are still three seconds; several selector reasons stretch the supplied
spatial/action evidence, and one timing explanation contradicts its actual slot.
The saved audit records those limits separately from count/reuse checks. Backend
validation passed 1,209 tests with one skip.

The model's vocal paraphrases remain uncertain; source captions do not verify film
plot, action completion or a suitable emotional relationship. Exclusion can leave
gaps in a limited library. This change does not claim that every completed edit is
coherent or artistically successful. The configured audio route remains supported
by the [official model documentation](https://developers.openai.com/api/docs/models/gpt-audio-1.5);
endpoint support is not evidence of music-interpretation accuracy.
