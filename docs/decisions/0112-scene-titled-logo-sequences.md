# ADR-0112: Scene titles identify logo sequences

- Status: Accepted
- Date: 2026-10-08
- Refines: [ADR-0054](0054-caption-backed-visual-junk-filtering.md) (adds a
  second picture-derived source for one junk category)
- Supersedes: None
- Superseded by: None

## Context

ADR-0054 limits visual junk detection to the shot's visual caption, because
dialogue describes what is said, not what is shown. Captions describe what a
picture looks like, not what it is. Searching "neon" or "neon in the rain"
returned NEON's distributor ident from Anora (0:23) and Portrait of a Lady on
Fire (0:17). Both captions read like ordinary shots, for example "a glowing red
neon sign spelling NEON". Neither says "logo", so no caption pattern can
catch them.

The understanding pass (ADR-0093) watches the film and titles each dramatic
scene from the picture. Both shots sit in scenes it titled "Opening Production
Logos". Across 14,234 scenes, 289 titles consist only of logo, credit or
title-card terms. But opening credits and title sequences often run over real
footage: Raging Bull's shadowboxing, Seven's title montage, Singin' in the
Rain's raincoats. A broad credits rule would drop footage people search for.

## Decision

A scene title is a second picture-derived source of junk. It is used through
a category table, `_SCENE_JUNK_TITLES`, with one entry today: `logos`. A
scene's title must name logos or idents and contain nothing beyond
logo, credit or title-card terms, for example "Opening Production Logos",
"Studio Logo" or "Opening Logos and Title Card". Titles that add story content
("Studio Logos and Prologue") or have no logo term ("Opening Credits", "End
Credits", "Opening Title Sequence") do not qualify. Their individual shots
remain subject to the caption rules.

Amended the same day: a company name of up to four capitalized words may
precede or surround the logo phrase ("Focus Features Opening Logo",
"Universal Pictures Opening Logo", "MGM Studio Logo"), and the card terms
include title credits, main titles, other named cards, a feature
presentation card and a blackout. That raised matches from 101 to 118 of
14,757 scenes with none lost. Mixed titles ("Studio Logos and Prologue",
"Opening Logos and Aerial LA", La La Land's "Mia's Script and Seb's Logo")
still do not match.

The table uses the existing query overrides: a query that asks for logos or
studio idents keeps them. One helper,
`_units_in_unrequested_junk_scenes`, applies the rule wherever compiled
evidence is available: broad search (after the caption filter, once evidence
loads), film highlights, and chronological film browsing. Units without
evidence are never excluded by this rule. Scene lookups are chunked like
evidence lookups.

## Consequences

- Distributor and studio idents leave ordinary results even when their
  captions describe only a sign or a shape.
- Adding a category, such as end-credit rolls once their footage risk is
  understood, is one table entry plus tests built from real titles.
- The rule is only as good as the understanding pass's titles. A mistitled
  scene is a data error to fix at its source, not with a query workaround.
- It adds one scene lookup per search over the eligible candidates. No model,
  index, backfill or raw data changes.
