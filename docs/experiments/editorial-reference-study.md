# Editorial reference study

This is a source-verified study packet, not a completed viewing report. Exact
cut timestamps, musical accents and quality judgments remain blank until the
clips have been played and annotated. No popularity or view-count claim is
attached to this set. It intentionally includes an effects-heavy control so
the first editor is not judged as though it supports speed ramps.

Six starting references, grouped by the questions to investigate:

- Emotional/motif sequence: [Malick // Fire & Water](https://kogonada.com/portfolio/malick-fire-water),
  by Kogonada. Study how repeated natural imagery carries a progression; do
  the same motifs work when scenes come from different directors?
- Emotional/gesture sequence: [Hands of Bresson](https://kogonada.com/portfolio/hands-of-bresson),
  by Kogonada for Criterion. Study small gestures, necessary lead-in/out time,
  and whether a useful action is actually visible inside a retrieved shot.
- Graphic composition: [Wes Anderson // Centered](https://kogonada.com/portfolio/wes-anderson-centered),
  by Kogonada. Study center, subject scale and context before permitting crop.
- Graphic perspective: [Kubrick // One-Point Perspective](https://kogonada.com/portfolio/kubrick-one-point-perspective),
  by Kogonada. Study spatial continuity and whether similar geometry becomes
  monotonous without changes in meaning or scale.
- Rhythmic/sound sequence: [Sounds of Aronofsky](https://kogonada.com/portfolio/sounds-of-aronofsky),
  by Kogonada. Separate source-sound punctuation from choices that could work
  with the first editor's music-only mix.
- Energetic transition control: [Seamless speed-ramp transition](https://www.mariosomedia.com/blog/editseamlesstransition),
  by Mario So. The creator's method explicitly combines matching motion,
  speed changes and sound effects. Annotate which part comes from source
  selection and which part cannot be reproduced by original-speed cuts.

These categories are study hypotheses derived from the creators' titles and
published descriptions, not timestamped observations. The set is weighted
toward film montage; replace a redundant reference with a creator-published
social edit when the target aesthetic is clearer. Do not infer that all popular
edits use one formula.

For each, choose a 15–30 second passage and record a small sequence of rows in
an evaluation JSON: `source_url`, `passage_start`, `passage_end`, then `cuts`
with `time`, `preceding_duration`, `musical_accent`, `feeling_before/after`,
`visible_motif`, `transition_type`, `crop_or_effect`, `required_source_handles`,
and `supported_by_music_sketch_v1`. Record observations only after playback.
Compare selection, sequence and timing independently of production effects.

Use [music_sketch_cases.yaml](../../pipeline/eval/music_sketch_cases.yaml) for
three actual music passages. The useful outcome is whether the draft reduces
editing time while retaining good scene choices—not a high score inferred from
audio energy. The [proposed discovery prompts](../../pipeline/eval/discovery_workbench_queries.yaml)
are ready for existing `pipeline.eval.review` / `pipeline.eval.experiment`
tools, but should be approved or replaced before freezing a human corpus.
