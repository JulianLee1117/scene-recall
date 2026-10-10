# ADR-0116: Lab folders by experiment, and one Match Cuts address

- Status: Accepted
- Date: 2026-10-09
- Amends: ADR-0051 (the route for saved Match Cuts edits); ADR-0027 and
  ADR-0038 (their web editor)
- Superseded by: None

## Context

`web/features/lab` held three things at once: the pieces every lab shares,
the whole AI Music Video workspace (about fifty files) and the frozen
prepared-cohort Match Cuts editor at `/lab/visual-rhymes` (ADR-0027). Music
came first and the shared pieces grew around it, so the folder's name no
longer said what was in it, and nothing stopped the next experiment from being
added there too. The frozen editor was reachable only from saved
`visual-rhymes` projects, of which there are none among the 46 saved edits;
ADR-0099 replaced it with `/match`. The web also carried its own map of
experiment names beside the registry's.

## Decision

- The Lab is one folder, `web/features/lab`: its directory at the root, the
  shared kit in `kit/` (the workspace header, the project lifecycle with
  `useLabProject`, `ProjectActions` and editor direction, the source browser,
  job status with its details, and the icon and popover pieces), and one
  folder per experiment beside it: `music`, `matching`, `transitions`,
  `algmods`. An experiment imports the kit and nothing else of the Lab.
- The web editor at `/lab/visual-rhymes` is removed with its components, its
  types and its tests. The `visual-rhymes` registry entry stays as Match Cuts'
  identity, with `/match` as both its entry and its `project_route`. The
  Python match-job endpoints, `pipeline/matching/` outside `moments/` and the
  project hook's handling of match jobs remain until their own cleanup, as the
  contract already schedules.
- Experiment names come from the registry alone. A lab names itself for its
  own title, in its own folder.

## Consequences

- A new experiment is one registry line, one route file and one folder under
  the Lab, and the kit's role is visible from its folder.
- One address for Match Cuts. A typed `/lab/visual-rhymes` is a 404.
- Music's behaviour is unchanged; its files moved.
