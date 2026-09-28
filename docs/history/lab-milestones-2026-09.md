# Lab milestone history — September 2026

> Historical execution record, preserved during the architecture cleanup.
> Statuses, limits, next steps, test counts and project revisions below describe
> their individual milestones; later entries may supersede earlier ones.
> Do not use this record as the current plan or implementation contract.
> [Current work](../current-work.md), the [README](../../README.md) and the
> [architecture contract](../search-architecture.md) describe the active work.

## 2026-09-14 — First played comparisons and timeline scrubbing repair

- The first pacing A/B was inconclusive: the user liked the old opening cuts,
  found the new timing decent, and described the long hold as slightly awkward
  without a concrete failure. For inspection, the user liked the perceived
  taxi-to-glass-breaking association before replacement and also found the
  replacement decent. The small sample and generation variation do not establish
  a better or worse approach; these reactions are neither rejection nor creative
  acceptance. Broader inspection should demonstrate concrete error correction
  while preserving valid creative alternatives and meeting its latency gate.
- Inspection remains available experimentally. `lab.footage_inspection` was
  already off by default and its setting is unchanged. No new architecture,
  settings or hosted evaluation calls were introduced for this feedback.
- Timeline scrubbing now sends seeks directly through the player's imperative
  handle, replacing the seek-state/effect/update chain that could trigger React's
  maximum update-depth warning. Stationary and repeatedly clamped pointer moves
  do not resend seeks; intentional clicks and keyboard seeks remain available.
  Verification passed **191 Lab tests**, TypeScript checking and the production
  build. Browser scrubbing while paused and playing produced no console errors;
  the saved edit remained unchanged.

## 2026-09-14 — Bounded footage inspection: implementation and limited pilots

- [ADR-0061](../decisions/0061-targeted-footage-inspection-and-frozen-comparisons.md)
  is implemented and mechanically verified. Optional inspection remains off by
  default through `lab.footage_inspection: false`. One Generate flow may inspect
  up to two flagged positions with four bounded source observations and one
  joint review, retaining source/cut authority, locks and atomic publication.
  Rejected targeted replacements retain the prior scene labelled uninspected.
  No extra mandatory UI or automatic Match Cuts integration was added.
- Final backend run: **2,178 passed, seven skipped**. Subsequent focused observer
  and progress checks passed 37 tests; these counts are not additive. Frontend
  verification passed 188 tests, TypeScript checking and the production build.
  Browser smoke checks exercised existing Nocturne playback/pause and Task Details.
  The editor worker was idle/available and the separate ingestion worker remained
  busy. Existing saved projects were unchanged throughout these pilots.
- **Automatic short Nocturne:** a real selector over the frozen 30s candidate
  pool flagged 21 of 22 positions. The global two-target budget chose action
  positions 3 and 8; four window observations and one joint review retained both,
  with zero changes or gaps. Selection cost 157,632 tokens and 62.09s; the added
  inspection/review calls cost 29,338 tokens and 240.86s. Total execution was
  310.641s. This measures automatic nomination, bounded review and their latency;
  broad flags are not proof that all nominated positions need inspection.
  Receipt: `.tmp/footage-inspection/nocturne-4ff0dff3/summary.json`.
- **Diagnostic Rumination correction:** the opening 29.333s/40-position excerpt
  used deliberately injected hints at positions 14 and 33, with no new selection
  or listening call. Four observations plus one review used 26,663 tokens and
  234.213s; total execution including two renders was 273.063s. Position 14 changed
  from a blurred City of God shot to an already-offered Persona alternative.
  All cut positions and the other 39 clips remained unchanged. Both before/after
  720p, 24fps exports passed full audio/video decode with 704 video frames each.
  The saved project stayed revision 19. This is a bounded correction example;
  injected hints cannot demonstrate automatic detection coverage, and sparse
  samples do not establish hand/glass contact or creative preference.
  Receipt: `.tmp/footage-inspection/rumination-20260915T022006Z-3d8211b6/summary.json`.
- **Constructed 90s Nocturne context replay:** insert the existing frozen 30s
  excerpt privately into the original 90s document, producing 97 positions.
  All 75 outside positions and clips stayed exactly unchanged. Four observations
  were cache hits; the dry setup took 0.172s and the one new text review took
  51.922s/21,407 tokens, with 53.578s total execution. Targets 33 and 38 were kept
  as supported/uncertain; the saved Nocturne project stayed revision 11. This
  verifies context scale, reusable evidence and preservation, not a newly
  generated 90s edit or automatic nomination across a 90s passage.
  Receipt: `.tmp/footage-inspection/nocturne-long-76e22c7d/summary.json`.
- Evaluation remains limited and played acceptance is pending. The next gate
  is selective hints, acceptable latency and demonstrated played benefit.
  Improve nomination precision before considering greater coverage; do not
  increase the observation budget, add ingestion or enable the feature by
  default based on these mechanical checks. No further hosted calls are part
  of this verification checkpoint.

## 2026-09-14 — Frozen measured-beat timing comparison

- [ADR-0061](../decisions/0061-targeted-footage-inspection-and-frozen-comparisons.md)
  admits a bounded inspection/evaluation slice. The standalone
  `pipeline.experiments.music_edit` runner prepares dry comparisons by default,
  preserves input hashes and requires explicit execution/hosted budgets.
  Focused harness, timing, source-timing and inspection-coordinator checks passed
  90 tests at this checkpoint; this is not the final integration test count.
- Two 30-second frozen inputs reused existing listening and cached beats-on
  plans. Exactly two new timing calls tested beats-off; no audio requests,
  saved-project writes, new scene selection or renders were performed. Payloads
  withhold beats/downbeats and derived markers; corresponding source-fitting
  offers also withhold additional measured pulse choices. User markers, lyrics,
  relative RMS and listening observations remain, with original provenance intact.
- Nocturne's on/off comparison is 22 versus 19 planned positions, with longest
  holds 6.25s versus 2.5s. The off request took 53.63s and 4,096 tokens; its repeated
  1.25s durations match the retained RMS summary interval. Rumination is 36 versus
  39 positions, with longest holds 1.50s versus 1.458s; the off request took 54.07s
  and 5,007 tokens. Both variants cover the exact 30-second/720-frame passage.
  Empty timing placeholders are not retrieval failures.
- Receipts and input/source-offer checks are in
  `.tmp/music-led-pacing/beat-ablation/{nocturne,rumination}-20260915-02/run.json`
  and the combined `summary.json`. The first CLI attempts failed locally on a
  missing process API-key environment before any network request; execute mode
  now loads `.env` consistently with the existing CLI/worker. Those failed
  preparation attempts remain recorded in the `-01` folders.
- Removing explicit guides did not uniformly reduce cut density. These are
  one sample per treatment with frozen listening, not a causal estimate or a
  played preference. Broader inspection/runtime verification is a separate
  checkpoint; creative acceptance remains with the user.

## 2026-09-14 — Music-led timing before footage batches

- [ADR-0060](../decisions/0060-music-led-whole-passage-timing.md) separates a
  compact whole-passage musical timing call from visual directions and footage
  selection. Pace presets are soft preferences without count/average-duration
  quotas. Footage work groups the resulting shots into batches of at most 32,
  preferably 90 seconds, preserving a longer hold as one shot. Listening remains
  bounded to 90 seconds and reusable across pace-only regeneration. One Generate
  job still publishes one complete revision; fixed/manual work keeps its cuts.
- The recorded focused backend run passed 265 tests; 181 Lab frontend tests and
  the production build passed. The first broad backend run reported 2,002 passed,
  seven skipped and 41 failed: 39 Windows long temporary-path failures and two
  outdated expectations. The 41 affected tests passed on a corrected rerun.
  Subsequent scoped cleanup checks passed 109 tests, and final timing/numeric/
  OpenAI checks passed 61 tests. These are separate focused runs, not additive
  unique-test counts or a substitute for the final complete run.
- Final backend verification covers 2,065 passing tests and seven skips across
  the complete run and its targeted correction rerun. The complete run passed
  2,060 and exposed five stale assertions for the old `plan` operation label;
  all five passed after the tests adopted the distinct `timing` label, without
  another runtime change. Receipts: `.tmp/pacing-full-final.log` and
  `.tmp/pacing-full-final-recheck.log`.
- Bounded hosted timing diagnostics reused saved listening without new audio
  calls. For Nocturne 78.99–108.99s, the old clipped layout had 37 positions,
  29 below one second and a 1.375s longest hold. One timing call (53.31s,
  4,379 total tokens) proposed 22 positions, 13 below one second and a 6.25s
  hold. Repeating the identical input reused its cache in 0.015s. For Rumination
  0–30s, one 55.39s timing call proposed 36 positions compared with 41 old
  clipped positions; the new longest hold was 1.5s. Its cache repeat also passed.
  Both original saved project documents remained identical. These outcomes
  demonstrate different legal timing responses, not a preference for fewer cuts
  or a claim that the proposed musical reasons are correct. Frozen inputs,
  receipts and timing diagnostics are under
  `.tmp/music-led-pacing/diagnostics/20260915T003406Z-60f79ca4/`.
- A controlled Nocturne A/B audition uses the same ordered pool of eight
  authoritative source units, the same source in-points and identical encoded
  music for old 37-position and new 22-position timelines. Every unit supports
  the longest 6.25s hold with at least 250ms of unit handles. Both 540p/24fps
  renders decode all 720 frames over 30 seconds, with expected chunk counts,
  legal source bounds and no all-black frames. Round-robin repeats deliberately
  control the imagery for this timing diagnostic; they are not product selection
  quality. Receipts and both outputs are under
  `.tmp/music-led-pacing/audition/20260915T004018Z-6e9eb4d1/`.
- Real isolated generation `24138015-c77f-415f-9f62-c1b6895e22ae` reused the
  Nocturne timing artifact, then ran visual planning, retrieval and selection
  in 153.08s. It filled all 22 positions with 22 distinct indexed units from
  16 films and applied one isolated revision. Selection moved one offered
  boundary from frame 107 to 121; the 6.25s hold remained. The final shortest
  position is 0.6667s. The original user project was not replaced by this check.
- The actual 720p/24fps render passes complete audio/video decoding, all 22
  chunk frame counts and authoritative unit/film bounds. All 720 composed native
  decoded YUV frame hashes equal their corresponding individual chunk frames.
  Video duration is 29.999667s against 30s nominal, within the 1.1ms check;
  no frame is all black at the documented 160×90 RGB threshold. The manifest
  matches the isolated project. Render QA and first/middle/last contact sheets
  are under `.tmp/music-led-pacing/live/1b26c05dfd97/`; the existing output is
  `assets_dir/lab/renders/24138015-c77f-415f-9f62-c1b6895e22ae-verification/output.mp4`.
- Runtime verification confirmed the new API (launcher 42672, serving process
  44596) and CPU editor (launcher 25208, worker 46196). `/lab/workers` reported
  both roles online; the original ingestion launcher 37900 and its queue
  continued, with 192 queued items at the check. These are checkpoint identities
  and counts, not durable service handles. Live Edit settings displayed the soft
  pacing descriptions and old Nocturne job Details remained readable. A rendered
  successful timing-details fixture showed all six notes and 22 positions, with
  its disclosures checked. Runtime/UI evidence is
  `.tmp/music-led-pacing/services.json` and `.tmp/music-led-pacing/ui/`.

Implementation and bounded mechanical verification are complete; subjective
audition acceptance awaits user review. Creative playback preference, exact
action timing/completion and motion matches remain unverified. Sparse contact
sheets and legal windows do not establish
those properties. Priorities 2–4 retain their separate evidence gates; no
automatic action inspection or Match integration is activated by this milestone.

## 2026-09-14 — Joint source timing and independent editor/library workers

- Nocturne generation `04a656fe-0d2d-4a3b-b251-d284f2fb456f` exposed a different
  failure from stale imports: shot 12 selected 0.75075s of eligible footage but
  independently chosen cuts required 0.91667s. ADR-0058 gives the model offered
  source choices, normalized trim positions and cut preferences; a bounded
  local solver fits the existing offered cuts jointly before resolving trims.
  The archived 26-choice diagnostic replay preserves every source and moves
  only the problem cut, from offered frame 262 to 255. It does not modify the
  old response, job or project. Its receipt is
  `.tmp/rapid-worker-fix/source-timing-replay.json`.
- ADR-0059 separates editor and library/GPU jobs using the existing SQLite
  ledger. One default launcher starts both roles; per-role ownership, atomic
  claims and recovery prevent duplicate execution. Shared/exclusive OS locks
  exclude older serial workers. CPU editor queries use the same encoder
  identities and pinned complete Lance/readiness snapshots while library
  publication continues. Hosted work never holds a publication transaction.
- Added `--status`, graceful `--stop`, optional `--role`, independent development
  reload and a read-only `/lab/workers` endpoint. Heartbeats describe process
  activity; they do not grant ownership or replay paid jobs. Stop requests and
  reload exits serialize through short ledger transactions. Queued editor task
  details identify the worker and explain offline/busy states without adding
  controls to the main workspace.
- The prior serial worker drained its active backfill normally. Pending library
  work was preserved. The refreshed API and default two-role launcher run from
  `.tmp/worker-separation/`; browser retry
  `6a161be2-c2ef-491c-bd0e-59fa4f891619` began from Nocturne revision 10 while the
  library role processed temporal backfill concurrently. Runtime receipts and
  final validation results are recorded there.
- Live resource inspection caught a Windows environment edge: an empty CUDA
  mask disappears from the native environment. The editor now uses `-1`, with
  a real fresh-process regression. A read-only CPU probe returned eight
  semantic results in 5.063s and eight PE look/text results in 10.556s, including
  cold model loads. Both actual encoders reported CPU; CUDA remained unavailable
  and uninitialized, with unchanged encoder identities. The receipt is
  `.tmp/worker-separation/cpu-query-proof-native-mask.json`.
- Final code validation: 2,017 backend tests passed, seven skipped; 172 Lab
  frontend tests passed, TypeScript passed and the production build completed.
  The backend suite includes real Windows lock/process, concurrent stop/reload,
  CPU resource and pinned-index checks. Browser verification confirmed offline
  queue guidance, the role in task details and progress without page movement;
  the editor console reported no errors during generation.
- The live retry completed in 619.74s and applied revision 11: 110 timeline
  positions over 90 seconds, 106 distinct source shots from 39 films, and four
  explained gaps at positions 14, 55, 58 and 67. One duplicate was rejected;
  three choices lacked sufficient matching evidence. Every placed source media
  path and indexed source boundary passed the read-only audit; timeline coverage
  is exactly 2,160 output frames. No visual/editorial quality guarantee follows
  from these mechanical checks. The old revision remains in History.
- Browser playback advanced through multiple cuts past 19 seconds with visible
  footage, current-film labels and music/video ready without media errors.
  Pausing worked and the completed edit stayed saved. This is a transport smoke
  check, not a full artistic review of all 90 seconds. The four gaps remain
  visible with Fill gaps available and export appropriately disabled.
- After generation the editor reloaded independently to the verified native
  CPU mask; final editor PID 24524 was idle and library PID 32940 continued
  backfill. These are activation-time identities, not stable management handles;
  use `--status` for current workers. Audit, final runtime and job receipts are
  `.tmp/worker-separation/nocturne-verification.json`,
  `final-live-workers.json` and `nocturne-live-job.json`.

## 2026-09-14 — Rapid worker compatibility and development reload

- Nocturne project `69b6b42d-879f-4d7a-bb79-cdd9a6874e4a` version 10 was valid
  with Rapid pacing. Generation `ffb95eb2-46a3-468e-a2d6-43d7df816da0` failed
  before model work because the worker still held the previous three-value
  Pydantic schema. The old worker had remained alive through the completed
  ingestion queue; the one-off idle refresh stopped on its unrecognized
  Windows console child. No saved-project migration or pacing downgrade was
  needed.
- ADR-0055 adds opt-in `--reload`: fresh worker processes between jobs, stable
  runtime-source checks before claims, preserved configuration, graceful parent
  shutdown, visible startup failures, and no automatic hosted replay. The
  Windows parent pipe uses nonblocking native polling; a real NumPy/LanceDB
  import smoke test reproduces and guards against blocking-stdin startup hangs.
- Queue claims use insertion order when timestamps tie, including consistent
  maintenance displays and ingestion queue positions. A frozen-time regression
  with descending UUIDs verifies FIFO ties and foreground priority.
- Validation: 629 Lab, temporal-backfill and integration tests pass after both
  corrections. The actual failed planner receipt independently violates the
  tightened JSON schema at exactly its two oversized cut frames. Coverage
  includes old documents without settings, all saved pacing modes through
  worker dispatch, real Rapid planning with stubbed hosted calls, revision
  conflicts, preserved failure history, fresh interpreters, Windows startup,
  section-relative cut bounds, and rejection without model-output repair.
- Replaced only verified idle/pre-claim worker processes; no running jobs were
  interrupted. The original failure and frozen snapshot remain byte-for-byte
  equivalent. Explicit retry `9a4f5c8d-26b9-420e-896b-ca18ae693760` passed Rapid
  validation, listening, first-section planning and selection. Its second
  section then failed whole-edit cut validation: the planner proposed 543 and
  565 frames inside a 540-frame section, followed by the required endpoint 540.
  The saved project remains version 10. The hosted whole-edit schema now caps
  `end_frame` at the current section's frame count, with strict independent
  ordering/coverage validation and exact shot/frame diagnostics. The recorded
  invalid output is preserved; no cut sorting, clamping or hosted replay was
  added. ADR-0043 and the architecture contract record this narrow correction.
  Receipts and startup logs
  are in `.tmp/rapid-worker-fix/`. The live reload launcher is PID 11120;
  individual worker child PIDs change after source updates.
- The reload worker refreshed itself after this generation, then continued
  queued ingestion normally. Current ingestion is left uninterrupted; the cut
  schema change activates at the next job boundary. This entry does not claim
  a successful regenerated video after the final correction.

## 2026-09-14 — Rumination frame-boundary repair

- Reproduced shot 48's paused Paul frame: HTML media rounded source start
  2892.5980416666666 down to 2892.598041, before the valley's first native PTS.
  All 19 exported frames already showed the valley. The preview now seeks 1ms
  inside a trim, gates incoming deck visibility on decoded seek readiness, and
  holds a legal final output frame while the music reaches the cut. An outgoing
  deck cannot be overwritten for preload while still visible.
- Decoded all 2,799 output frames and visually inspected first-two/last-two
  frames for all 148 clips, plus complete frames of ten suspected clips. Only
  shot 90 had confirmed exported contamination: its last frame was the following
  robed figure. Its indexed end exceeded the actual native cut by about 83us.
  Revision 18 slips that same water shot about 84ms earlier, preserving its
  19-frame duration and all musical cut times. Every replacement frame and five
  independent source seeks were visually checked. The original clip and saved
  revision remain recoverable.
- Artifacts are in `.tmp/rumination-boundaries/`, including exact native PTS,
  old/new rendered frames, the 148-clip audit and guarded revision-18 update.
  Raw source films and indexed timestamps are unchanged. This observed repair
  is not a new general guarantee of clean boundaries in future generated edits.
- Three playback regressions failed before the fix and passed afterward.
  A review also caught failed preloads getting stuck behind readiness checks;
  active media errors now stop playback with a readable error and permit retry.
  All 171 Lab frontend tests, TypeScript and production build pass. Browser
  checks confirm the unchanged shot 48 trim opens on the valley, the corrected
  shot 90 opens on water, playback crosses both affected sections, and Space
  still plays/pauses without changing the saved sequence.
- The refreshed revision-18 1080p export matches the saved manifest. All 2,799
  frames and the complete audio decode without errors; no black frames were
  detected. Hashes confirm the 147 retained encoded clips and encoded music
  are unchanged. Its receipt and full checks are recorded in
  `.tmp/rumination-boundaries/direct-export.json` and `export-qa.json`.

## 2026-09-14 — Rapid pace and rumination usability

- ADR-0050 adds Rapid (0.65–1.2s average, 0.8s target), smaller bounded text
  scopes, reuse of current audio analysis and a disclosed 288-shot automatic cap.
  Existing fixed cuts remain fixed. Local starters resume after missing beats
  and distribute capacity across the full passage. RMS window calculations inspect
  only overlapping samples.
- The editor keeps 5ms waveform peaks, draws visible pixel detail, pages during
  actual playback, displays the current canonical film title, and hides crowded
  cut labels until zoom/selection makes them useful. Why this shot presents
  recorded intention/selection notes, current neighbors and separate source evidence.
- Rumination project `fc4684eb-81eb-4dbb-a09c-c33c34c1efab` is revision 17.
  Replaced positions 27, 70, 76 and 96 after visual source review; all 148 cut
  times remain exact. Different unit IDs had allowed near-identical tree/river
  compositions through existing exact-reuse checks. Perceptual deduplication is
  not implemented or claimed by this repair.
- `.tmp/rumination-usability/` contains before/after snapshots, verified source
  proposals, attached evidence and export receipts. The refreshed 1080p export
  decodes all 2,799 video frames and audio without errors or black frames;
  144 encoded clips and the original encoded music were preserved. Its render
  manifest matches revision 17 exactly.
- Verification: 564 Lab backend tests, 165 Lab frontend tests, TypeScript and
  production build passed. Browser checks cover 8× waveform detail/page following,
  Space playback, film names across cuts/seeks, Rapid settings, the evidence
  inspector and the corrected 01:09:04 source. Temporary viewport overrides reset.
- Runtime at handoff: API refreshed. Ingestion worker still owns three imports;
  task-local `reload_worker_when_idle.py --execute` (launcher PID 41588) waits
  up to six hours for the entire queue to clear, then verifies the exact old
  processes and stops/reloads only while SQLite excludes new claims. It never
  edits job rows or interrupts running imports. Inspect its JSONL/output/error
  files in the task directory before starting a Rapid generation; worker reload
  was pending when this milestone was recorded.

## Architecture cleanup verification (2026-09-13)

- Retired the unreachable music branch of the shared Lab editor, its orphaned
  components/styles, five stock SVGs and five unused backend helpers. The current
  Music Workspace and live Match behavior remain intact; exact retired UI copies
  are under `.tmp/architecture-audit/retired-music-ui`.
- Corrected CPU preflight, contradictory full-length limits/deferrals and stale
  proposal claims. Moved the completed execution record here, retaining active
  Lab verification in `docs/current-work.md` and raw evidence in place.
- Focused backend checks passed before the full suite: 1,325 passed, one
  platform skip. All 137 frontend tests and TypeScript passed; an isolated
  Webpack production build compiled every app route without modifying the live
  `.next` directory. Seven rendered Match editor states matched the former UI.
- Read-only state verification found one Mirror (1975) bookmark at 920.906s,
  with an indexed burning-building frame and working thumbnail/preview routes.
  Both SQLite databases passed integrity checks. This records storage health,
  not completion of the outstanding Lab editorial or browser acceptance gates.

## Scene-based Match search (2026-09-13)

ADR-0040 adds an explicit scene-to-next-clip discovery route with independent
position, shape, subject and camera explanations at identical proposed cut
points. The engine shares a bounded shortlist and model work; the API freezes
projectless jobs; the UI exposes reference, focus, results and played previews.
The Lab v3 path remains available for diagnostic comparisons. Backend
verification passes 1,152 tests with one skip. A controlled fresh-process
comparison reduced first verified playback from 31.60s to 15.48s; all 12
top-three previews across the cold comparison and two warm diagnostics passed
boundary checks. The live API smoke preserved existing projects/revisions.
Browser verification passes early and on-demand playback, saved-search refresh
without another search job, position-focus cancellation, the 390px layout and
search-options panel, and Escape/X focus restoration. Browser logs contain no
warnings or errors. Final frontend validation passes 93 tests and a production
build. Implementation and verification for this milestone are complete; human
editorial grading and wider coverage remain explicit future gates. No timeline
placement or broader preparation was included.

## Full-length music videos (ADR-0045, 2026-09-13)

- Raise selected passages to 600 seconds, timelines to 300 positions and saved
  clips to 600, while retaining bounded 90-second listening and 32-shot planning
  scopes. One durable Generate job assembles a complete atomic revision with
  shared song context, visual motifs and cross-section footage exclusions.
- The song picker offers Use full song / Use 10 minutes; Fit song remains a
  waveform view control. Export video now requests the full export profile.
  Frontend evidence views preserve later-song observations under explicit
  aggregate provenance rather than truncating to short-excerpt limits.
- The requested BAANDIT rumination project is
  `fc4684eb-81eb-4dbb-a09c-c33c34c1efab`, using the entire 116.610612-second track.
  Its saved notes request fast piano-accent clusters followed by longer holds.
  Local timing found 136 beat guides; two bounded listening results are saved.
- Live listening exposed a 0.333-microsecond final-event rounding overflow.
  Hosted observations within one microsecond of an endpoint now normalize to
  that endpoint before strict validation. Saved/manual evidence remains strict;
  substantive overshoots fail. The completed response was revalidated using
  matching prompt/schema/provider/model/settings identities, retaining its raw
  receipt and avoiding another hosted request. A normal cached Analyze job then
  saved revision 6.
- Final frontend checks: 121 tests, TypeScript and production build pass. Full
  backend suite: 1,325 passed, one skipped; the final global saved-bin preflight
  and long-edit checks passed 20 focused tests, including one additional guard.
- The first full-song Generate saved revision 7 atomically: 39 positions, 38
  selected sources, one explained gap. Its initial two-second piano cuts were
  too conservative for this request. A project-specific manual timing revision
  placed 31 positions through 30.9167 seconds using measured pulse landmarks,
  preserving the quiet 12.33–19.5-second interlude and later footage. Fill gaps
  then planned 19 new directions (five using offered neighboring image
  references), selected 19 sources and preserved all fixed timing/placements.
- Reviewed source footage and saved alternatives replaced three disruptive
  later images; a plain main-search query filled one remaining canopy gap that
  the generated search recipe had missed. Footage existed, but the offered
  shortlist did not include the relevant natural image. No fallback search
  policy was added in this work.
- BAANDIT revision 10 contains all 58 positions. Every selected window decoded:
  2,799 frames, no wholly black frames or decode failures. Browser playback
  reached 63 seconds through the rapid opening, and Space paused cleanly with
  no media or browser warnings/errors. This is an AI-assisted, reviewed edit;
  neither the initial one-click plan nor sparse evidence verified motion matches.
- Full-quality export job `141bb007-0c3d-4377-acda-b5847a2d0cf0` completed. Its
  output is H.264/AAC, 1920×1080, 24fps, 2,799 frames and 116.625 seconds.
  The complete encoded video and audio decode without errors.

## Scoped scene selection and input cleanup (ADR-0044, 2026-09-13)

- Nocturne's 53.99–143.99 passage failed on an unoffered source after 229 seconds;
  project revision 5 is unchanged. All returned IDs exist in the index, but failed
  jobs previously lacked the exact offered map. The selector used 407,705 input
  tokens for 554 sources, including repeated evidence and identifiers.
- The first live retry reduced selector input to 182,155 tokens but exposed a
  cross-source timestamp on shot 13. Its manifest proves the source ID, timestamp
  and description were mixed between two offered candidates; revision 5 stayed
  unchanged. The response now pairs each offered catalog alias with that source's
  bounded timestamp in a closed one-key object, without a second numbering system.
  Legal cut enums remain scoped per shot. Timing, response validation and prompt construction are separated;
  compact model evidence preserves distinct context and drops duplicate plumbing.
  Offer manifests make failed selections auditable. Final focused checks pass 75
  schema/prompt/timing cases and 183 generation/integration cases. Full backend:
  **1,278 passed, one skip** (`.tmp/selv3all`).
- Live v3 retry `b8865f47-479a-4b1f-adae-5349e8249a37` completed in 101.24 seconds
  and applied revision 6: 28 positions, 26 scenes, two explicitly explained gaps.
  All offered identities, cut choices, source trims and 90-second coverage validate.
  The previous revision remains in History. Selector input is 219,358 tokens,
  **46.2% below** the original 407,705, including the stronger response schema.
  Browser confirms 28 timeline clips, Saved, and the completed status. Worker,
  API and frontend remain running; temporary viewport changes were reset.
  Diagnostics are under `.tmp/selection-cleanup/v3-live-validation.json`.

## Music editor presentation and controls (2026-09-13)

- Missing-scene follow-up: Nocturne positions 2 and 26 were explicit abstentions;
  one fill-only request filled both at revision 7 while preserving all cuts and
  existing footage. The Her city view at position 20 exposed a separate moment
  error: the indexed unit starts with about eight seconds of black, while the
  matched skyline frame is later. A manual source-window adjustment to
  2593.387601–2596.720934 seconds saved revision 8, retaining the shot and duration.
  All 80 sampled decoded frames in the corrected window contain picture. All
  28 positions are now filled; raw films and annotations remain intact.
- Empty positions have a subtle hatch, a clickable missing-scene count and their
  actual selection reason above scene results. Selection guidance now explicitly
  anchors visual trims to their query-specific matched frame and final duration.
  This prompt guidance is not a guarantee that every frame was inspected.

- Use neutral charcoal panels, restrained shared amber emphasis, a compact project
  bar, and a full-width timeline below the preview and scene library. Flatten scene
  results into readable rows, center transport controls, and adapt clip text to
  its actual visible width. Timeline zoom restores the full prompt detail.
- Keep Save/Exit visible; move deletion into Project with the existing confirmation
  and job/revision safeguards. Restore Project-menu focus after cancelling Delete.
  A compact task row retains progress, cancellation and error details; suppress the
  duplicate main-page error while preserving distinct save and conflict failures.
- Memoize clip/source lookups and beat guides across playback updates. No new UI
  dependencies, model calls or persistence format. All 106 frontend tests,
  TypeScript and production build pass. Browser checks at 1440×960 and 390×844
  verify overflow, stationary popovers, song/settings dialogs, guarded delete
  cancellation, Space playback, one-frame cut adjustment and exact Undo. Browser
  warnings/errors are empty; the existing test-viewed edit remains saved at its
  original revision.

## Song meaning, edit density and fresh footage (ADR-0043, 2026-09-13)

- Follow-up cut validation failure: a regeneration returned six cut frames outside
  the offered choices (first: shot 16 returned 1184). The original stayed at revision
  8. The response schema now binds each slot to its own allowed-frame enum and exact
  requested choice count; local source/timing and atomic revision guards remain.
  No repair, silent snapping or extra model stage was added. All 21 focused timing
  tests and 1,218 backend tests pass (one skip). The same failed regeneration was
  retried after loading the fix: it completed in 121 seconds, selected 28 of 30
  shots, returned zero unoffered cuts, and applied revision 9 with revision 8
  retained in history. Two gaps explicitly lack matching footage. Browser status
  shows completion without console errors; diagnostics are under `.tmp/cut-frame-fix`.

- Read-only audit of Nothing Lasts Forever revisions 6/8 confirmed Balanced 12
  shots became Energetic 10, with 7.5–10-second holds. Yi Yi was reused within the
  new edit and Didi overlapped the previous version despite alternative offers.
- Existing text planning now owns whole-edit cut count/timing/directions within
  a pace budget. Energetic is 23–32 shots for this 90-second passage. Fixed/manual
  work remains fixed; uniform timing is diagnostic, not an automatic rejection.
- Same audio call now extracts scoped, uncertain vocal meaning separately from
  musical atmosphere. The UI exposes heard paraphrases and approximate song times.
  New packets retain provenance; old/unclear meaning is not invented.
- Regeneration excludes prior placed footage before the offer limit; automatic
  selections reject repeated units/overlapping windows, with explained gaps.
  Manual reuse remains possible. Read-only replay flags the two actual failures.
- Validation: 1,209 backend tests passed with one skip after focused checks; 101
  Lab frontend tests and the production build passed. A separate live copy of the
  same passage produced 30 slots / 26 placed scenes across 17 films in 238 seconds,
  with no repeated units, overlapping windows or reuse of the previous ten shots.
  Four explained gaps total ten seconds. Playback crossed six shots without media
  errors; song meaning and heard evidence fit anchored desktop/mobile panels.
- Dense timelines exposed cut hit areas intercepting short clip bodies. Handles
  now occupy the ruler strip with noninteractive guide lines. At 1x Fit, a
  two-second gap selects correctly; frame nudging and Undo restore exact timing.
  Seven focused timeline tests and browser checks pass without console errors.
- The comparison establishes density and reuse control, not expressive editing:
  22/30 slots still last exactly three seconds. Indexed captions support several
  relationship-distance, barrier and memory metaphors, but some selections still
  rationalize unsupported separation, action or geometry. One timing explanation
  contradicts its actual slot. Meaning remains model-inferred, not independently
  verified. The original projects remain at revisions 8/5; the separate revised
  draft is retained for comparison. Snapshots and the detailed source audit are
  under `.tmp/editor-quality-audit`.

## Whole-edit regeneration from settings (ADR-0042, 2026-09-13)

- A completed or partially generated video exposes Regenerate edit through the
  existing settings dialog. Its primary action applies the draft before enqueue;
  Apply changes alone and Enter do not start generation. Fill gaps remains separate.
- Explicit regeneration privately replans all cuts and scenes with the current
  pacing and user context. It retains old footage, rejects placed locks and applies
  one final revision with existing failure/cancellation/stale-result guards.
- Validation: 87 focused backend tests and the full suite (1,168 passed, one skip);
  93 Lab frontend tests across the integrated suite and added workspace tests;
  production build/TypeScript passed. Desktop/mobile settings and staged pace changes
  passed browser checks. A live Balanced-to-Energetic rebuild of the same Steve Lacy
  passage produced 11 selected clips (versus seven), no gaps, and 2–4-second durations
  from 220 candidates in 200.2 seconds. The original layout stayed saved during
  generation; completion applied one revision. Browser Undo/Save restored its exact
  cuts and footage. Diagnostic JSON is in `.tmp/editor-refresh`; only the temporary
  QA project was deleted. Newly created user projects remain. This one comparison
  verifies regeneration, not a guaranteed cut count or artistic-quality improvement.

## Simple editor and bounded source-aware first cuts (ADR-0041, 2026-09-13)

- One editor entry and one default Generate action; no v1/v2 selection. Consolidated
  Scenes query/results and contextual clip tools; music/beat/cut actions live beside
  their source. Settings stage output, film scope and creative options together.
- First untouched generation can choose finite nearby cuts with source windows;
  fixed/manual edits remain guarded. Explicit abstention and bounded candidate
  diagnostics replace forced irrelevant choices. Normal search defaults unchanged.
- Durable progress details expose real steps/models/timestamps and supplied counts.
- User-authorized reset removed six idle Lab projects, 16 revisions and eight project
  jobs via the revision-checked API. Five original music files and raw films remain.
  Diagnostic copies are under `.tmp/lab-reset-20260913T091121Z`, not a permanent backup.
- Validation: 152 focused backend checks, then 1,102 backend tests passed with one
  platform skip; 82 Lab frontend tests and the production build/TypeScript passed.
  A real Steve Lacy passage generated seven clips from 133 offered candidates in
  183 seconds, with durations from 3 to 8.5 seconds. Browser checks covered complete
  playback, manual search, source trim/crop, drag placement, cuts, Undo, locks,
  staged settings, responsive overlays, progress, and Save and exit. A media-time
  rounding regression at a cut after Undo was fixed and verified in the browser.
  Export produced exactly 30 seconds / 720 frames at 24 fps. The disposable QA
  project was deleted after saving diagnostic JSON in `.tmp/editor-refresh`.
  Original music and films remain. The wider passage assembler/temporal-perception
  experiments below remain proposals; these checks do not establish artistic quality.

## Proposed planning architecture review (2026-09-13)

The [AI Music Video planning review](../experiments/ai-music-video-planning-review.md)
compares storyboard-first filling, local pair continuation and global intent
with bounded source-aware assembly. It proposes separate evidence/selection and
timing experiments before wider integration. It is not an accepted replacement
contract; no production search, generation or project data changed in this review.

## Editor overlays and scene-selection audit (2026-09-13)

- More, History, How AI chooses, Visual plan, Music & output, Timeline options
  and musical cue details now use one anchored top-layer panel. They do not
  insert content into the page. Panels fit the viewport, scroll internally,
  dismiss on Escape/outside click and keep editor shortcuts from acting behind
  them. Expand remains the deliberate way to enlarge the timeline.
- Removed the Creative brief input and its unused styles. Planner settings stays
  in the toolbar; existing saved `brief` values remain compatible. Placed clips
  and the shot inspector label the requested imagery as Search intent.
- Validation: 65 Lab frontend tests, TypeScript and production build passed.
  Browser checks covered every converted disclosure, toggle/Escape/outside
  dismissal, focus restoration, Space isolation and the visual-plan/settings
  transition. At 390px width and 500px height the long panel scrolled internally.
  No browser errors or warnings. The inspected Steve Lacy project stayed at
  revision 6 with eight placed clips; no hosted jobs or saved edits were made.

The same project's three reported mismatches establish a selection-quality
failure, not a completed fix: the chooser must select from every nonempty offer,
and legal source bounds do not verify the requested action. Parasite's indexed
caption describes intimate fabric pulling, which selection recast as readiness.
Dune's annotation itself claims stillness; the user reports movement. Wild
Strawberries' 15.27-second annotated subdivision has only one sampled frame in
the chosen four-second window. Its retained frame does not establish the action
or dream context. All three use three sampled stills in the current annotation
profile. Neither model replacement nor a generic trim correction is established
as sufficient by this audit.

Recommended next scope, not implemented here: explicit choose-or-abstain
decisions; preserve the musical purpose while adapting AI-owned imagery to
available footage; one bounded repair search; and inspection of exact proposed
windows for contradictions. User-written requirements stay binding. Continuous
motion and narrative context need appropriate evidence, not stronger prose over
still captions. Any selection-contract change must update the architecture and
record an ADR before implementation. This does not authorize a full reingestion,
an unbounded agent loop or a main-search architecture change.

## Match Cuts workspace and effectiveness (ADR-0038, 2026-09-13)

The user approved implementing the simpler A→B workflow and testing actual
feature effectiveness. This extends ADR-0027 within the bounded Lab collection;
it does not close the unrelated discovery/music or release gates below.

- [x] One monitor with Automatic matching, optional Subject/Camera/Shape focus,
  nearby timing, native-frame pinning, three played choices, Keep and Undo.
- [x] Separate immutable timing-adjustment previews, progressive result delivery,
  original framing/speed defaults and optional reframing.
- [x] Prepare the pinned SAM 2.1 Small + RAFT tracked-subject profile for all 80
  windows: 229 tracks, 305.55 seconds. Subject and shape readiness is now live.
- [x] Correct exit/entry motion scoring, dissolve confidence, mask aspect and
  actual source-frame preview/export boundaries. Real-media tests cover through
  60 fps, variable frame rate and nonzero source timestamps.
- [x] Preserve legacy/fixed/nearby diagnostic comparisons with code/profile/source
  identities, played MP4 hashes, latency and blank human playback judgments.
- [x] Final integrated backend suite: 1,072 passed, one platform skip. Frontend:
  57 tests and production build pass. Working-tree whitespace check passes.
- [x] Compare five versus ten initial refinements: all four diagnostic requests
  retain exact ranked top-three cuts. Subject first preview is 16–21 seconds,
  shape 8–14 seconds; Automatic remains 29 seconds first/31 seconds all three.
- [x] Live HTTP preview → native A/B adjustment → exact Keep → Undo → restore →
  export passes. All three existing projects remain unchanged. The dedicated
  `Match Cuts verification` project is `ec9e7009-90eb-44a4-bd5e-5844c9ee5575`,
  revision 5, with its verified adjusted cut/export retained for playback.
- [ ] Browser walkthrough remains unverified: CUA tab creation stalled twice
  despite timeouts. Component behavior and live API workflow are verified; the
  app preview is queued for this task. No existing browser tab was edited.
- [ ] Human played-cut review remains open. The diagnostic references do not
  establish the frozen 12-visual/24-motion quality gate or p95 performance.

Initial tracked subject/shape previews all passed boundary checks (12/12), but
first-playable latency was 22–34 seconds and broad mask similarity did not
establish useful editing. Repeated scoring was cached and refinement bounded;
foreground IoU corrected a mask agreement failure. Candidate quality remains an
explicit measurement, not inferred from test counts. Automatic and some focused
runs still miss the 15-second first-playable target. Optional DINOv3 dense weights remain unprepared; no gated model access,
full-library backfill, search activation or branch merge was implied.

## Implemented: song-informed first-edit timing (ADR-0039, 2026-09-13)

- Inspected Steve Lacy `oh yeah_`, 9.94–39.94s: 36 detected beats and nine
  downbeats became only two local starter slots (14s and 16s). The saved project
  had no footage or applied analysis; its last Generate was interrupted at
  sequence selection. That interrupted job was not replayed.
- New empty rhythm starters carry an explicit timing fingerprint. First Generate
  can adopt the song interpretation's moments only while those starters remain
  untouched. Legacy/manual timelines, user directions and saved shot work retain
  their timing. Failure/cancellation applies no intermediate edit.
- The timeline explains starter timing. Timeline options → Plan cuts from music
  uses the existing explicit analyze/replan operation for older edits, retaining
  previous footage and Undo. Manual timing, placement and per-shot work take
  ownership of starter cuts.
- Interpretation contract v6 removes the blanket long-hold preference for
  repetition. A real same-passage request returned eight proposed moments in
  53.48s, versus four in the retained prior interpretation. Its mostly regular
  4s/3s pacing is not verified vocal alignment or evidence of artistic quality.
- A checked-in stripped measured fixture reproduces the actual 14s/16s failure
  and verifies first-generation moment adoption, unchanged beat evidence,
  conservative legacy/manual ownership, cancellation and one-step restoration.
- Frontend: **52 Lab tests**, TypeScript and production build passed. Backend:
  **1,046 passed, one skip**; 146 focused checks passed. The eight real-record
  regressions were rerun successfully after moving their fixture into the
  versionable `pipeline/tests/data/` directory.
- Live browser/API/worker QA used a temporary copy of the Steve project. Plan
  cuts from music adopted the saved v6 interpretation as eight empty positions;
  browser Undo/Save restored the original two. A fresh local rhythm job published
  the starter notice; moving a cut claimed its timing and Undo restored starter
  eligibility. Browser errors/warnings: none. The test project and tab were removed.
- The original project remains revision 4 with its two existing placeholders.
  Its explicit recovery is Plan cuts from music, then Generate edit. API/worker
  were externally restarted while QA was in progress; a guarded restart attempt
  aborted on changed process IDs without stopping anything. New schema and worker
  behavior were verified through the successful live jobs, so no restart remains
  required for this timing change.

## Implemented: next-scene audition (ADR-0036, 2026-09-12)

The next focused plan is [next-scene.md](../experiments/next-scene.md): test whether
an anchor scene and musical moment can produce three useful, playable next-scene
choices, with explicit application and bounded timing adjustments. Begin with
real pair comparisons and reuse the existing timeline, worker and renderer.
The user approved implementation with lighter evaluation: ordinary playback
reactions are sufficient to guide polishing; the formal twelve-task pilot is
optional and no fine-tuning/RL dataset is required. ADR-0036 admits bounded
pair proposals and optional sampled-frame inspection for the observed
missing-moment failure. Longer automatic sequences remain a later decision.
Keep the open discovery, music, Match Cuts and release gates below; no new model,
profile activation, branch replacement or merge is implied.

- [x] Implement frozen pair suggestions and explicit revision-checked Apply.
- [x] Add music-backed preview, compact choice/adjustment UI and one-step Undo.
- [x] Admit shorter sources through explicitly bounded flexible-cut feasibility.
- [x] Compare optional sampled-frame inspection with the existing text path.
- [x] Finish focused/full checks and real-source HTTP/worker/render checks.
- [x] Add direct Moment and Crop & zoom controls to the active candidate monitor
  and ordinary placed-scene review (ADR-0037), preserving local Cancel and Undo.
- [ ] Walk through the next-scene pair endpoints in the browser. Earlier automatic
  approval review blocked isolated test-server startup; API/worker have since
  been restarted (2026-09-13). Pair rendering/proof already passed in-process.

### Next-scene verification

- Final backend: **952 passed, one platform skip**. Frontend: **32 checks**, TypeScript
  and production build passed. Includes non-grid unchanged cuts, exact offered
  timing, abstention, null evidence, cancellation, source authority, preview
  proof, stale revisions and one-step restoration.
- Real 10cc passage: a live search-intent/selection run completed in 107.38s and
  returned one legal Yi Yi → Didi pair. GET preview, Apply, duplicate rejection
  and restoration passed through the current API in-process. No browser server
  or background worker was started for these checks.
- Controlled follow-up reused the same interpretation, planned directions and
  **same six offers** for text-only and sampled-frame selection. Both selected
  the same Didi shot and source window. Both prepared playable previews and
  preserved the original document until Apply. Observed totals were 55.80s and
  55.27s; one comparison does not establish latency or creative superiority.
  Frame inspection remains experimental and off by default.
- A retained real proposal also passed a 0.5s source-in adjustment, durable
  preview, preview GET, exact adjusted Apply and restoration. Missing matching
  preview proof was rejected before rendering. No extra hosted call was needed.
- Media tests compare every decoded pair picture with the corresponding full
  timeline excerpt and check the original music/fade clock. Synthetic and real
  anamorphic sources now retain display proportions. Match Cuts remains strict
  and unchanged. Cache/manifest provenance records the new display component.
- Real execution found null suggested timestamps and non-square source pixels;
  both are fixed and covered. The current editor and song-picker Cancel loaded
  without browser warnings or horizontal overflow at 1280px. New candidate
  audition and timing controls still need the full endpoint walkthrough above.

### Manual moment/crop verification (ADR-0037)

- Backend: **968 passed, one platform skip**, including 98 focused lifecycle,
  source-authority, crop serialization and search-reference checks.
- Frontend: **40 tests**, TypeScript, production Next build and whitespace check
  passed. The production build contains only the expected application routes.
- Real-source HTTP/worker smoke reused a retained 10cc proposal without a new
  hosted call: a 0.5s source shift and off-center crop rendered, served, applied
  exactly and restored. Missing or mismatched crop preview proof was rejected.
- Ten media tests include an independent decoded crop reference, every frame
  of the pair versus the full edit excerpt, anamorphic display proportions,
  unchanged original files and the original music/fade clock.
- Browser component QA used real Didi footage on the existing frontend, with
  local fixture state only. Verified Moment dragging and frame keys, zoom,
  picture dragging, full-frame reset, bounded playback, Space inside the review
  dialog, local Cancel/Use, invalid media-bound recovery, and read-only controls.
  Landscape and portrait previews fit desktop and 390px layouts without
  horizontal overflow. No browser errors or warnings. The temporary route and
  tab were removed after testing; no user project was changed.
- Browser testing found cached metadata could leave the source controls disabled;
  initialization now reads an already-loaded video. Review also caught identity
  crop versus null proof equality, interrupted-preview recovery, and stale manual
  search recipes. These are fixed with focused regressions. The existing API and
  worker were subsequently restarted on 2026-09-13; the full pair-endpoint browser
  verification above remains separate from the completed source-control tests.
- Integration fixtures and reports live under `.tmp/ns-live`; the user's saved
  project, original tracks and films were preserved. Human creative acceptance
  remains open; this does not require a ratings or labeling campaign.

## Milestone: source-backed Lab on a simpler search baseline

Working branch: `codex/scene-recall-lab`, created from
`prototype/modular-search` at `511ca6d70597e969da9d3314bc855ee039da93b0`.
`master` remains at `4e140d324859c33d091c78bc9ea12e72ef44d524`.
The new branch preserves the prototype's retrieval improvements and tests.
No branch replacement or merge approval is implied by this implementation.

### Implemented

- [x] Broad-query-first search with contextual Refine and drag destinations.
- [x] Honest distinction between retrieved keyframe and live player position.
- [x] Lab registry, AI Music Video and manual Match Cuts screens.
- [x] Durable project revisions, hashed original music and source-range clips.
- [x] Standalone durable worker and ingestion queue migration.
- [x] Local waveform/intensity and pinned Beat This! rhythm derivations.
- [x] Separately configured audio interpretation and grounded draft planning.
- [x] Search replacement, trim, crop, reorder, lock, undo and history.
- [x] Source-resolved 24-fps rendering, music replacement and MP4 preview.
- [x] Offline region/crop proposal and human oracle evaluation harness.
- [x] Bounded offline DINOv3 descriptor/profile/ranking adapter; no real model
  run or production activation.
- [x] Architecture/ADRs and runnable worker/model preparation instructions.
- [x] Complete mechanical integration validation and record the live-provider
  limitation; the final suite has 671 passes and one platform skip.
- [x] Verify a successful live hosted interpretation through the explicitly
  selected OpenAI audio provider; see the ADR-0028 follow-up below.
- [x] Verify a successful live source-grounded draft; the real 10cc generation
  and complete playback are recorded under ADR-0035 below.
- [ ] Complete human editing-quality review; mechanical playback is not creative
  acceptance. The proposed next-scene pilot narrows the immediate question.
- [ ] Review the final diff and approve a release/merge path.

### Initial milestone verification record

- Final full backend suite: 671 passed, one Windows symlink permission skip. Includes
  original search/intake tests and new Lab/music coverage.
- Renderer tests inspect actual decoded frame colors, long-GOP trim, normalized
  crop, duration/frame counts, and imported audio replacing source audio.
- Local Beat This! inference completed on synthetic audio with checkpoint
  SHA-256 `8c328b45f59d8dd3dff219253ff6a8d6482be57d0133a29140e2febbf8eb8331`.
- Hosted transport test verifies real serialized audio/schema, disabled
  response storage, and one request on a retryable failure.
- Next production build passed after initial integration.
- Final production build also passes with all Lab routes and latest UI fixes.
- Browser Match Cuts: real library search, source trims, region selection,
  saved revisions, A→B render/playback, 390px overflow check, and two-tab stale
  save preservation checked. Music upload, waveform, passage changes, timing
  markers, save and reload also checked.
- Live hosted smoke uses a generated 12-second melody, not a human quality
  case. Initial request-format issue was corrected and covered by a transport
  test. Next request returned provider high demand; it remained explicitly
  failed and was not automatically retried.
- One controlled later retry returned the same provider high-demand error.
  At that milestone, hosted audio-to-draft live success remained unverified;
  no further retry loop or model substitution was started. The subsequent
  user-requested OpenAI provider change is recorded separately below.
- Real manual music pipeline smoke completed: four source films, 12 seconds,
  288 H.264 frames at 24 fps, 1280×720, AAC music. Browser readyState 4 and
  duration 12 verified. API restart preserved revision, job history and render;
  attachment download supports HTTP range requests.
- Final UI checks also covered main search/facet edits and Escape, independent
  source finder, image Look gate, scope, bookmark add/remove and honest player
  anchors. Manual markers survive analysis, including an intentional empty set.

### Human acceptance remains open

The old handoff's user interaction/release gate was not silently checked off.
Browser automation validates mechanics; the user still needs to confirm that
discovery and editing feel useful at representative viewport sizes.

1. Approve or replace `pipeline/eval/discovery_workbench_queries.yaml`, then
   judge 10–15 real imagined/remembered-scene queries across broad text, Scene,
   Words, Mood, Look and Framing. Record expected examples before judging the
   top results; separate missing candidates, ranking, duplicates and evidence.
2. Fill `pipeline/eval/music_sketch_cases.yaml` with three real selected music
   passages and judge emotional interpretation, timing,
   film/motif progression, useful clips kept, substitutions and editing time.
3. Use `docs/experiments/editorial-reference-study.md` to study six varied
   creator-published edits with short source-linked annotated
   timelines. Distinguish selection/timing from effects outside the v1 renderer.
4. Complete the frozen 12-reference static Match Cut gate. Manual instant/crop
   oracle and played-transition judgments stay distinct from static rank.
5. Review the branch diff. Decide whether to first fast-forward the stable
   prototype baseline into master or review the integrated branch as a release.

### Match Cuts implementation (ADR-0027)

- [x] Reusable matching package with immutable 200-shot/600-frame cohorts.
- [x] Independent dense/PE candidate paths, region proposals and bounded visual refinement.
- [x] Real RAFT Small evidence for 80 motion windows over eight films.
- [x] Confidence-gated camera/residual movement and candidate timing refinement.
- [x] Durable match jobs, immutable suggestions, previews, revision/lock guards and Undo.
- [x] Region marking independent of crop, scene/example entry points and setup feedback.
- [x] Real motion runs: ten candidates and three verified previews, 30–39 seconds.
- [x] Final backend suite: 685 passed, one platform skip; Next production build passes.
- [x] Isolated WAFT checkpoint import, strict load and real-frame GPU feasibility probe.
- [ ] Obtain approved local DINOv3 weights; run real dense extraction and image retrieval.
- [ ] Freeze and human-grade 12 visual and eight motion references; compare baselines.
- [ ] Benchmark WAFT vs RAFT on held-out motion cases before selecting a replacement.
- [x] On-demand preview jobs for remaining candidates, without changing the edit.
- [x] At most four optional reference-window alternatives for movement and image matching.

The implementation and technical results are recorded in
`docs/experiments/match-finder-status.md`. Human acceptance is still open;
DINOv3 access was requested, not bypassed. Keep this handoff until those checks
and the earlier discovery/music acceptance gates are resolved. Film-audio
retrieval, plot profiles, ANN, always-on routing and full-song orchestration
remain deferred. No branch merge or model promotion was performed.

### Music interaction and provider follow-up (ADR-0028)

The user reported that the editing controls were difficult to discover and
that Gemini high-demand failures offered no useful progress. This follow-up
keeps the bounded workflow and makes the provider choice explicit.

- [x] Default AI Music Video to OpenAI `gpt-audio-1.5` with actual audio input;
  preserve explicit Gemini compatibility independently of annotation.
- [x] Validate returned JSON and complete passage coverage locally; scope
  interpretation caches and hosted receipts by provider and adapter contract.
- [x] Persist clear preparation, listening, streamed writing, validation,
  retrieval and planning stages; keep single-attempt and cancellation guards.
- [x] Add a finite passage-relative linear fade-in to project and render
  contracts; default zero and validate against passage duration.
- [x] Focused backend verification: 78 tests passed, covering OpenAI transport
  serialization, no storage, no automatic retry on 503/429/401/404, stream
  cancellation, provider cache isolation and real decoded fade amplitudes.
  Render tests also verify unchanged source hashes and passage-relative timing.
- [x] One authorized live OpenAI request on the existing synthetic 12-second
  **Validation melody.wav** completed in 5.48 seconds. It streamed text and
  returned three valid segments covering 0–12 seconds. No user song was sent
  and no saved project was changed. Receipt:
  `C:/Users/julia/Videos/cinema-assets/lab/requests/openai-synthetic-1789169194-interpret.json`.
  This establishes endpoint mechanics, not musical or editorial quality.
- [x] Finish and browser-verify the simplified staged music editor, draggable
  passage/playhead/markers and live fade preview at desktop and narrow widths.
- [x] Run the integrated Next production build after all interaction changes.
- [ ] Human-grade three real passages, including interpretation, edit timing,
  useful scene choices and how easily the user can revise a draft.

No always-on language-model search layer, film-audio index, full-song planner
or production matching activation is introduced by this follow-up.

Interaction verification for this follow-up:

- Main search: descriptive query, Mood text Apply, contained reference lookup,
  selection without changing the main query, real pointer drag into Scene,
  moving a reference from Scene to Words, keyboard Related menu, and normal
  scene playback. The pointer gesture has an activation threshold, cancellation,
  click suppression and one shared clue operation; native/file drops remain.
- Music: actual waveform edge trim, overview passage move and one-step Undo,
  playhead, marker add/drag/keyboard/Delete, passage-end playback stop and fade
  adjustment. Numeric controls are optional. All three screens were checked
  at desktop width and the music/direction screens at 390 pixels.
- Match Cuts: select among played A→B previews, keep a match, Undo and return
  to reference selection, then complete a fresh ten-candidate motion run.
  Reference and comparison layouts fit 390 pixels. Locked following clips
  block application with a clear hint; moving a reference beyond its optional
  window keeps the window valid. Your cut stays reachable with one clip.
- The initial full backend run passed 704 tests with one platform skip.
  A live complete music workflow subsequently exposed the unsupported all-text
  audio-model planner path; the explicit planner correction and final results
  are recorded with ADR-0028. Do not mistake the earlier build/test pass for
  end-to-end hosted drafting validation.

Final integration results:

- Backend: **708 passed, one platform skip** after the explicit text planner
  correction (`--basetemp .tmp/mu93`). Next production build passes.
- Live browser workflow on the synthetic melody: Listen returned a valid
  interpretation; **Find scenes for this music** completed in 19.94 seconds,
  saved four clips covering 12 seconds, and opened Edit & preview. Rendering
  completed in 2.68 seconds with a 0.75-second passage-relative fade-in.
  Browser playback verified duration 12 seconds, readyState 4 and advancing
  currentTime. Project `804e414a-55a1-4375-912a-b6364a8ef076`, draft job
  `146cfc95-ba3d-4a58-a9fb-484b5a177650`, render job
  `06c80881-9367-4df9-b0ec-16908ddfc5b7` preserve the evidence.
- API and standalone worker were restarted with the final provider configuration;
  the frontend remains running at `http://127.0.0.1:3000`.
- This verifies mechanics and integration. Real-song editorial quality and the
  prepared-image matching gate remain separate open acceptance work.


### Timeline workspace refinement (ADR-0029)

The staged AI Music Video screens are superseded by a preview, selected-shot
inspector, and authoritative music/clip timeline. Analyze creates editable
section prompts and suggested cuts before retrieval. Fill only searches empty
shots; Suggest a replacement explicitly replaces one unlocked shot and opens
its shortlist. The planner receives the full passage and neighboring selections.
Main search now uses persistent compact Match chips and one anchored editor;
Lab is a registry-driven experiment directory with recent projects.

- [x] Actual browser cut drag from 4.0 to 4.5 seconds, one-frame keyboard
  adjustment, Undo, then fill: the edited boundaries remained 0/4.5/7/10/12.
- [x] New synthetic project `85a430b8-acd0-4492-ab81-d3edd6ea273a` analyzed the
  existing Validation melody.wav, detected 24 beats, and produced four slots.
  Fill job `82a1ee36-e1ec-4278-acc7-6c60c90e0a60` saved four legal clips and six
  alternatives per slot. No user song was sent during validation.
- [x] A manual alternative and targeted updated prompt worked in the browser.
  Targeted job `d650e1f9-b893-4a14-93f1-a4607e83b149` preserved all neighboring
  slot objects. Job `f9229730-79e3-4cf8-82c5-db5edc662585` verified the new
  one-step AI Undo: saved revision 10 exactly matches its pre-job document.
- [x] Whole-sequence source playback sampled about 8–18 ms from the music
  clock across transitions, stops at the passage end, and holds the final
  scene. Opening passage/export/source dialogs pauses the main transport;
  closing the passage picker unmounts its audio. No competing playback.
- [x] Export `f1cc15c5-8d99-451b-8b93-4ea674e5aa8e` completed in 2.19 seconds.
  Its 1280x720/24-fps manifest has 108/60/72/48 frames and a 0.75-second music
  fade. Browser playback reached exactly 12 seconds and stopped.
- [x] Fixed cached native-audio responses breaking waveform fetches; decoding
  now fetches a complete uncached response and retains the bounded peak cache.
- [x] Reanalyze uses the current brief and regenerates section directions,
  preserving cut positions and clips. Draft never silently listens. Tests
  cover this distinction and explicit new-track reset/revision restoration.
- [x] Desktop and 390-pixel layouts checked. The narrow layout places the
  timeline directly after preview; timeline ruler density adapts to width.
  The passage picker no longer exposes a second, disconnected marker editor.
- [x] Search bar position remains stable when opening/closing Match editors;
  real pointer drags and contained reference selection work. A no-overlap
  Framing-plus-description result now explains the missing overlap and offers
  Search without Framing or Look deeper. This is an honest UI boundary,
  not a ranking-quality fix; conditional reranking remains unimplemented.
- [x] Final backend suite: **734 passed, one platform skip**, using
  `--basetemp .tmp/mu9y`. Next production build and TypeScript pass. Focused
  frontend timing checks cover migration, legal trim bounds, locks, splitting,
  rolling edits and section ownership. Diff whitespace check is clean.
- [x] API, worker and frontend are running with the final changes; API and
  app endpoints return HTTP 200. No active jobs were interrupted at restart.

The current-work document remains for the still-open real-passage editorial
study and prepared-image matching acceptance gates. Synthetic integration
checks establish mechanics, not aesthetic quality or motion-aware selection.

### Essential music controls and walkthrough

- [x] Song selection now uses one waveform with a movable section, two trim
  handles, Play section, and an explicit Use this section action. Numeric
  timing, waveform zoom, and fade-in live under More options. Draft selection
  changes stay local until applied; Cancel after an upload restores the prior
  song and preserves the Undo history from before the picker opened.
- [x] The default timeline has labeled Clips and Music rows, concise film
  titles, and one edit playhead. AI directions, beat visibility, snapping, and
  zoom live under Options. The preview has one labeled Play edit control.
- [x] Actual desktop and 390-pixel browser walkthrough on isolated project
  `a7f606c5-a136-4993-a498-358a6f674dc6`: trim and move a section, Cancel, Apply
  and Undo, drag a cut, adjust it by one frame, seek the edit playhead, play
  through the sequence, and replace a scene. Neighboring slots and their cut
  positions remained unchanged when choosing an alternative.
- [x] Uploaded a synthetic 120-second song through the file chooser, selected
  a passage starting at 60 seconds, zoomed and scrolled its waveform, and
  verified playback stopped at 60.5 seconds. Cancelling restored the previous
  song; Undo then restored the exact pre-edit document. A fade-only adjustment
  preserved the four-clip arrangement. No new hosted model calls were made.
- [x] The user's project `85a430b8-acd0-4492-ab81-d3edd6ea273a` was verified
  unchanged. API and frontend return HTTP 200; browser errors and warnings
  were empty during the walkthrough.
- [x] Completed AI jobs keep editing locked until their saved project result
  has loaded, preventing a song-picker cancellation from restoring an older
  revision during that interval. Editing and Undo clear stale save notices.
- [x] Final Next production build and TypeScript pass. Focused checks cover
  delayed analysis/draft completion with concurrent cancellation, stale
  notices, and the existing timeline editing invariants. Diff whitespace
  check is clean.

### Song-specific intentions and adjustable editorial timing (ADR-0030)

- [x] Replaced fixed initial duration buckets with versioned, validated audio
  edit moments: query/facet, audible cue, purpose and timing note per moment.
  Broad musical sections remain context. Initial plans use moment boundaries;
  explicit user markers and existing cuts remain authoritative.
- [x] Each timeline clip owns its editable direction. Two clips within the
  same section can search independently. Reanalysis preserves written
  directions; Use AI direction restores the matching generated idea locally.
  Retrieval deduplicates at most 32 effective slot queries and includes each
  target's intent plus neighboring selections in one grounded planning call.
- [x] Explicit Replan cuts creates empty slots and retains prior source
  selections. Placed locks block it before hosted work. Capacity checks prevent
  fills, splits or manual placements from creating an invalid 101st selection.
- [x] Space plays/pauses from the workspace, selected timeline clips and cut
  controls; song-picker playback is scoped to its dialog. Typing, modifiers,
  IME and ordinary native controls retain their behavior. Beat snapping starts
  off. Add cut, Set end at playhead and Remove cut after clip make holds and
  scene-dependent manual timing available without another hosted request.
- [x] Browser validation on isolated project
  `5c9630e3-5b20-4a25-9d7b-20e556e755b1` covered independent directions,
  native and clip-focused Space, typing spaces, off-grid cut placement,
  splitting/joining with Undo, AI-direction reset, and narrow layout.
- [x] Live analysis `c89ec254-9e93-45bf-a4f8-8f00cca07fcf` preserved the four
  existing cut intervals and a custom third-clip query. Its unsupported vocal
  hint on the known instrumental fixture motivated tighter listening
  instructions and the separately cached v4.1 contract. No automatic retry
  or provider fallback occurred.
- [x] v4.1 replan `0f264897-c441-49f7-b780-73fa96d5e515` completed in 8.62 s
  and chose three longer holds for the quiet repetitive synthetic melody.
  Fill `b0abd1e6-a260-4ac4-834c-e2ea9bbf7d39` completed in 17.26 s, selected
  three legal source clips, preserved the 0/4/8/12 boundaries, and retained
  the four previous selections unplaced. A cached replan followed by browser
  Undo restored the exact filled document (saved revision 8 equals revision 6).
- [x] Final full backend suite: **752 passed, one platform skip**
  (`--basetemp .tmp/moments-final`). The subsequent prompt-only v4.1 tightening
  passes 40 focused music/moment tests. Final frontend production build,
  TypeScript, timing/merge/capacity/reset checks and whitespace checks pass.
  API and worker restarted idle with the final contract; original project
  `85a430b8-acd0-4492-ab81-d3edd6ea273a` remains unchanged.

Editorial acceptance remains open: the synthetic melody is a mechanics test,
not a representative song. The planner still cannot verify action completion
from video. The live draft's Mirror selection at 6051.0867–6055.0867 opens on a
hand although its retrieved caption describes a landscape; this is a concrete
source-window/caption evidence case for the temporal review, not proof of
automatic action-aware timing. Do not treat legal range validation as visual
or editorial correctness.

## Direct cut markers and coordinated direction planning

- [x] ADR-0031 adds explicit text-only `plan` jobs for exact frozen slot IDs,
  separate from audio listening and scene retrieval. Strict direction-only
  output, user/lock protection, scoped cache/provenance, cancellation and late
  result preservation retain the existing bounded architecture.
- [x] Splits, rolling cuts and joins retain earlier directions as context and
  flag only affected prompts. The visible creative brief and Plan prompts
  action coordinate shot roles without silently changing timing or clips.
- [x] Focused timing/helper tests and plan hook acceptance, cancellation-race,
  Undo and newer-local-edit tests passed. The full backend suite passes
  **766 tests, one platform skip** (`.tmp/timeline-direction-final`).
- [x] Live text-only plan `c8f0d82a-aa88-4226-9f17-97ed7979f9ca` on isolated
  project `2805bfc7-6354-4c10-8174-4dc4c2d67d3e` gave the 0–2 and 2–4.5 s
  split pieces distinct queries (quiet interior detail, then a figure oriented
  toward the following doors). Exact timings, all selected clips, untouched
  neighbors, custom ending query and analysis were preserved. Browser Undo
  followed by Save restored the exact pre-plan document (revision 5 equals 3).
- [x] Browser checks verify one-frame cut nudging, Delete to join, Ctrl Z to
  restore, and Space playback through the complete 12-second edit with no
  media errors. Original user project remains unchanged.
- [x] Direct marker dragging, M to split, double-click splitting of both filled
  clips and empty placeholders, zoom/Fit and expanded cards pass browser QA.
  Fixed the moving playhead intercepting the second click by handling ruler
  double-clicks at the shared timeline surface. Default 1265×712 layout shows
  the music row; 390px layout has no horizontal page overflow.
- [x] Fixed obsolete completed-job notices after saved Undo/reload. Thirteen
  focused recovery/completion scenarios pass, including explicit unapplied
  proposals and later-revision races. Production build, TypeScript and
  whitespace checks pass. API and worker are running with the new plan job.

The live melody remains a mechanical fixture; creative quality on real songs
and source-window/caption accuracy still need the acceptance work above.

## Transparent musical evidence and planner preferences (ADR-0032)

- [x] Share scoped beat/downbeat guides, compact RMS summaries, supplied lyric
  meaning and explicit preferences across Analyze, Plan and Find. Reject stale
  manual-marker scope independently of derived beat evidence; omit malformed
  legacy event containers. Listening excludes prior interpretations.
- [x] Add separate approximate audio observations, editable pacing/lyric
  treatment, track-scoped notes and lyric spans, an owned arc/motif plan and
  per-shot corrective feedback. Preserve existing timing and default lock/user
  protections. Track replacement clears song-specific state but keeps preferences.
- [x] One Planner settings Apply/Cancel boundary with Undo; new lyric cues use
  the playhead or selected span. Settings changes do not start hosted jobs.
  Generated prompts are flagged only when affected. AI/user direction ownership
  is visible, and empty initial projects retain AI-generated first-cut planning.
- [x] Add a Cues lane with source and uncertainty details. Click seeks to the
  event; detailed beat guides are optional. Review footage plays the actual
  proposed trim with Start/Middle/End seeking and bounded source-in audition.
  Apply keeps slot duration; Cancel/Undo and locked read-only review are retained.
- [x] Focused backend checks passed 141 tests; complete backend suite passed
  **792 tests, one platform skip** (`.tmp/planner-improvements-full`). TypeScript,
  production build and focused frontend timing/context/provenance checks pass.
- [x] Live isolated project `a9786cbc-0944-4078-9c75-f9c1132f6b18` used the existing
  synthetic Validation melody.wav and explicitly supplied paraphrases. Analyze
  `5875b903-d185-4462-8e63-dbfe6cf549a7` completed in 8.76 s, with 24 measured RMS
  summary windows and three separate approximate audio observations. Plan
  `9425bf04-4035-457d-a5d2-cdbbdfe8ec6b` completed in 9.74 s and generated a visual
  arc plus four requested directions. Existing cut intervals, source selections
  and the unrequested custom ending stayed intact.
- [x] Replacement `53b547ae-be66-4bb1-b612-1c5847cb0fdb` completed in 10.33 s and
  returned six legal alternatives for the first slot. Other positions and all
  cut times stayed fixed. Test project final revision 7 retains the results.
  Original user project `85a430b8-acd0-4492-ab81-d3edd6ea273a` is unchanged.
- [x] Browser QA covered source-window Space playback/end stop, exact seeking,
  source-in Apply/Cancel/Undo, settings Cancel and saved Undo, meaning-only cue
  creation, user visual-plan ownership, explicit feedback and cue seeking.
  Desktop and 390px layouts fit; fixed inherited dialog padding clipping footer
  controls. Browser errors/warnings were empty. API and worker restarted idle
  with the final backend; frontend remains running on port 3000.

These checks establish mechanics and evidence handoff. Real-song creative
acceptance is still open. No automatic lyric transcription, new structure model,
video-model inspection or automatic retiming is claimed. Audio interpretation
still shares one call with creative moment generation, so its cache remains
coupled to brief and planning context. Retain this handoff for the earlier
discovery/music study and prepared-image matching gates.

## Search-aware editing and one-button generation (ADR-0033)

- [x] Share a versioned capability catalog between the planner, Lab retrieval
  adapter and optional UI details. Check real active profiles without loading
  models. Support existing text and retained indexed-reference recipes, bounded
  to three unique facets, with explicit unsupported requirements.
- [x] Freeze reference authority and exclude requested replacement targets from
  offered anchors. Preserve query-specific text/frame/clause evidence separately
  from canonical source ranges. Hydrate known scalar annotations for only the
  offered units; actual Lance/result-formatter verification covers this handoff.
- [x] Add atomic Generate and targeted Improve jobs. Reuse current audio and
  valid saved plans; preserve fixed cuts, placed selections, user directions and
  locks. Failed or cancelled stages apply no intermediate revision. Report no
  fitting replacement without claiming improvement or deleting the old choice.
- [x] Focused backend checks passed 217 search/music/recipe tests and 57
  orchestration/core Lab tests. The complete backend suite passed **837 tests,
  one platform skip** (`.tmp/search-editor-full`). Frontend production build and
  nine focused editing/evidence helper scenarios pass. A final planner-context
  correction passed 56 focused checks after the full suite.
- [x] Live isolated project `9579d8e3-3d35-45b2-96e3-7f62b6b9c4d4` uses the
  existing synthetic Validation melody.wav. Generate job
  `4ec13ad5-e032-49d0-85bc-840915b34fb1` ran Analyze, Plan and Find in 36.27 s and
  applied exactly one revision (2 to 3). It produced three filled four-second
  slots, with two/three/three complementary Scene, Mood and Look clues and
  explicit unverified requirements. Browser Space playback reached 12 seconds
  and stopped with all three media elements ready and no media errors.
- [x] Reference-based Improve `b5d71854-a90e-45f5-8e97-4a2b06f57eba`
  exercised Framing + Scene + Mood in 15.71 s, but revealed that the planner
  substituted the following shot when the requested preceding source window
  had no indexed frame. Planner context now identifies each neighbor and its
  actual available reference IDs; instructions explicitly forbid substitution.
  Final Improve `2a89e495-0f4b-4c2a-a66f-d5de76682208` completed in 23.99 s,
  reused analysis, and explained the unavailable preceding reference while
  producing a text-only plan. All cuts and both neighboring slot documents
  stayed identical; one revision applied. One browser Undo and Save restored
  the exact document from before improvement (validation project revision 8).
- [x] Browser QA covered optional search details, clue add/cancel/remove and
  Undo, query editing that clears obsolete recipes, feedback and alternatives.
  Desktop and 390px layouts fit without horizontal page overflow. API and worker
  run the final backend; frontend production build passes and port 3000 remains
  available. Original project `85a430b8-acd0-4492-ab81-d3edd6ea273a` retains its
  original revision and exact normalized document.

The real-song study remains open. The synthetic fixture verifies orchestration
and source-grounded mechanics, not musical sensitivity or match-cut quality.
No new indexes, candidate-video model, automatic retiming, dialogue mixer or
promotion of the bounded Match Cuts experiment is introduced here.

## Explicit shot search and selection (ADR-0034)

- [x] Replace the inspector's vague automatic replacement action with a Search
  tab and Find scenes. Edit the prompt/clues, preview returned alternatives, then
  choose Use scene. Optional AI prompt assistance exposes Rewrite prompt using
  intent and feedback, separately from retrieval and scene placement.
- [x] Reuse the durable draft path with a strict single-slot `suggest_only`
  option. Return six ranked legal windows with evidence without a hosted planner
  or selector call. Preserve footage, cuts, analysis, directions and other slots;
  keep whole-edit Generate automatic and its standalone placement stage labeled
  Fill gaps. Existing targeted generation API remains compatible.
- [x] Focused backend and lifecycle checks passed, including no-match, locks,
  cancellation and stale revisions. Full backend suite: **859 passed, one
  platform skip** (`.tmp/shot-choice-full`). Frontend production build, nine
  editing helper tests and draft/plan completion-and-Undo checks pass.
- [x] Isolated project `9579d8e3-3d35-45b2-96e3-7f62b6b9c4d4` searched an edited
  window/light prompt in job `3f304e80-1d3c-4b5a-b53a-6b78d495f86b` (11.01 s,
  including first search after worker restart). Only alternatives and execution
  details changed; no hosted request receipt was created. Browser Space preview
  stopped at the candidate window end, Cancel kept the current scene, Use scene
  replaced only the selected position, and one Undo restored the exact document
  with its shortlist. Validation project remains at revision 12. Original user
  project is unchanged. API, worker and frontend are running with the final code.

## Editor usability pass

- [x] Give the inspector its own usable height instead of tying it to a tiny
  preview. Keep the timeline full width, collapse the visual-plan summary by
  default, and replace cramped result tiles with readable rows offering Preview
  and Use scene. Mobile retains the timeline above the shot inspector.
- [x] Name the editable disclosure Search clues, explain recipe replacement
  before typing, and show plain-language signal hints. Disable scene search with
  a nearby reason for empty clues, missing references or unavailable signals.
  Shot tabs support arrow/Home/End navigation and reset their scroll position
  when switching content. Reset to song prompt describes the actual fallback.
- [x] Replace live-mutating source-time input with Adjust source window, which
  opens the existing preview-and-apply dialog. Browse library keeps short results
  visible but disabled, with actual/required duration explanations inside the
  modal rather than an error hidden behind it.
- [x] TypeScript, ten focused editing/validation tests and production build pass.
  Browser QA at 1280x800 and 390x844 covered tab navigation, clue validation/Undo,
  song picker Cancel, candidate selection/Undo, and short library results (3.42s
  correctly unavailable for a 4s slot). No hosted planning calls were required;
  the existing validation project and original user edit retain their saved state.

## Local timing, project lifecycle and library editing (ADR-0035)

- [x] Delete the 19 existing Lab projects at the user's request. Keep imported
  songs and film evidence. Add revision-checked deletion and explicit Save/Exit,
  including Save/Discard/Cancel for unsaved work and active-job safeguards.
- [x] Prepare local beat guides and empty placeholders when applying a song
  passage, before hosted listening. Replace the old uniform energy buckets with
  measured bar/pulse grouping and amplitude-change preferences. Retain the
  timing contract and landmark provenance; never invent unavailable beat data.
- [x] Give AI Music Video a persistent right-hand Library with direct searches,
  source preview/trim and explicit placement. Pointer dragging validates the
  destination length/lock and preserves the matched instant when fitting a
  shorter slot. Manual Find scenes also works before audio interpretation.
- [x] Show detected beats initially; keep numbered cuts editable independently.
  Add scissors, snapping, zoom and transport icons. Verify splitting, one-frame
  cut adjustment, Undo, source-window trimming and whole-edit Space playback.
- [x] Real 10cc passage 14.43–58.11s produced 78 guides and six placeholders
  lasting 7.58, 5.38, 7.13, 7.17, 8.88 and 7.55 seconds. Generate completed
  listening, per-shot planning and selection atomically (revision 6 to 7), with
  distinct shot intentions across all six positions. The 43.68s edit played to
  its end. A separate fresh import confirmed Apply automatically queues local
  timing: 66 guides/four placeholders over 0–30s, with analysis still null.
- [x] Backend suite: **880 passed, one platform skip** (`.tmp/pt5`). Frontend
  **19 editing, source-evidence and lifecycle checks**, TypeScript and production build
  pass. Browser testing covers manual library search, successful and too-short
  drops, preview after dragging, saved exit and confirmed project deletion.
- [x] Match the Lab to Scene Recall's shared amber accent and neutral dark
  surfaces, including the song picker, source review, settings and lifecycle
  dialogs. Library rows use smaller previews and top-aligned compact content
  (about 94px at desktop width); keep search sticky. Desktop and 390px browser
  inspection confirms consistent styling and no horizontal page overflow.
- [x] Remove both named validation projects after the browser run. Preserve the
  new untitled project created during testing (`ba0a24f0-629b-4370-922a-c169e98d9455`),
  which was not part of the original deletion request. API, worker and frontend
  remain running; browser warnings/errors are empty and temporary viewport
  overrides have been reset.

These checks validate the editing mechanics and remove the old fixed-bucket
failure. Creative acceptance remains open: audio cues are approximate and the
selector reads captions, annotations and matched-frame evidence without watching
candidate footage. Exact action endings and motion continuity remain unverified.
Keep the earlier discovery and prepared-image matching decision gates.
