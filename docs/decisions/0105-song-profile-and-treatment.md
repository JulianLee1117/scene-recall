# ADR-0105: Know the song, then choose the edit's style from it

- Status: Accepted
- Date: 2026-10-04
- Amends: ADR-0043 (no song identification), for harness v2 planning only;
  ADR-0096 (harness pipeline)
- Superseded by: None

## Context

After rounds 1 and 2 (ADR-0103, ADR-0104), the owner said that the song
understanding behind the edit's style was lacking. The listening pass hears
an excerpt and, under ADR-0043, may not identify the song or use its track
title. Its readings were generic and sometimes wrong:
- Prospa's *Will You Be Mine*, a 130 BPM UK rave track, was "earnest and
  pleading".
- Radiohead's *Everything In Its Right Place* was "warm, calm", meaning
  "acceptance or reassurance".

Nothing turned an understanding of the song into a style. Pace, footage and
match cuts came from settings, and the planner worked inside them. The same
blind spot existed for films until evidence v2 made world knowledge
first-class (ADR-0093).

## Decision

1. **Song profile** (`harness-song-profile-v1`, `pipeline.lab.harness.song`).
   One cached planner request identifies the song from its track name. It
   records what is known: genre and scene, era, sound, how the lyrics are
   usually read (irony included), mood, how the whole song develops and where
   the excerpt falls in it, and notable uses in film and online edits.
   - Its input includes the whole track's measured loudness per four seconds.
   - It never quotes lyrics, and it says when it does not recognize the song.
   - It is knowledge kept beside the listening pass, which is unchanged and
     still does not identify songs.
2. **Treatment** (`harness-treatment-v1`). A second cached request reads the
   profile, the excerpt's listening, the measured sections, the user's
   direction and the settings. It chooses the edit's style:
   - a name and the idea;
   - pace and how it moves;
   - footage fame and look (era, palette, energy, what clashes);
   - match cuts and how cuts behave;
   - where impact lands;
   - what to avoid.

   The direction comes first.
3. **`planner_settings.auto`** (default false). When it is false, pace, footage
   and match cuts stay the user's settings and the treatment shapes the rest
   around them. When it is true, the treatment chooses them for this run; the
   saved edit keeps the user's settings and records the treatment.
4. The concept planner (`harness-concept-v7`) receives the profile and the
   treatment and builds acts, queries, paces and moves from them. The edit's
   `direction_plan.song` keeps both for review.

## Consequences

- Each new song or excerpt costs two more text requests (cached), plus
  decoding the whole track once per profile.
- Recognition can be wrong. The profile carries a recognized flag and its
  uncertainty, and the user's direction and song notes override it.
- An edit with no brief now gets a style chosen for its song rather than a
  generic default. A treatment the owner likes is the natural thing to save as
  a preset.
- v1 planning and the listening cache are unchanged; `auto` is excluded from v1
  evidence.
