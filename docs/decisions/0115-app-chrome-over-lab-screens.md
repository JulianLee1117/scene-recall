# ADR-0115: The app's chrome over every Lab screen

- Status: Accepted
- Date: 2026-10-09
- Amends: ADR-0051 (one Labs return control; no brand destination in a workspace)
- Superseded by: None

## Context

The home page, with its Search, Saved, Films and Info views, had one bar and
one warm palette. The Lab directory had its own breadcrumb and tile styling,
the workspace header a cool grey palette of its own, and each experiment
framed that header differently: full-bleed through negative margins in two
labs, inset inside a padded container in two others, inside a full-height grey
page in the fifth. ADR-0051 kept workspaces to one Labs return and forbade a
brand destination, so the home page was two hops from any experiment, through
a small breadcrumb.

The directory also fetched every saved project with its whole document (8 MB
for 46 edits: music timelines, analysis, rhythm) and showed no experiment until
that list had arrived, although the experiments are a static registry of a few
hundred bytes.

Experiments are meant to be quick and exploratory; their screens cannot be
known in advance, so whatever is shared must not constrain them.

## Decision

- One `AppBar` component renders the home page's tab strip over every route:
  Search, Saved, Films, Info and Lab. On the home page the four views still
  switch in place; elsewhere they are links, and `/?tab=saved|films|info`
  lands on that view, after which the address returns to `/`. Lab is a link
  everywhere.
- `LabWorkspaceHeader` renders the app bar, then one row: the Labs return, the
  experiment's title and, for a project editor, the project's name, save state
  and `ProjectActions`. A lab renders it first in `<main>`, before any container
  of its own. The header is the app's: it takes its colors from the page tokens
  (`--page-*`, now on `:root`) and no longer takes a class. Everything below it
  belongs to the experiment: its own width, background, palette and tools.
- ADR-0051's rule becomes: every way out of a project editor, the app bar's
  places included, passes the one unsaved-edits guard (`useProjectExit` takes
  a destination). The exit stays single in kind, not in destination. Sessions
  keep plain links.
- The Lab directory is a page-chrome page like Films, Saved and Info: eyebrow,
  title, description, a quiet grid of experiments and the gallery of saved
  edits.
- `GET /lab/projects` returns summaries: `track_name`, `clip_count`,
  `sheet_unit_ids` and `active_job_count`, never a `document`;
  `GET /lab/projects/{id}` serves the document. The directory renders
  experiments as soon as their list arrives and lets the edits fill in.

## Consequences

- The home page, any of its views and the directory are one click from any
  experiment, by the same bar as everywhere else.
- A new experiment gets the app's chrome by rendering the header first; its
  body is free. No layout, context or plugin machinery was added.
- Existing labs keep their internal styling; only how they frame the header
  changed. The old rhymes editor at `/lab/visual-rhymes` follows along.
- The project listing no longer carries documents. Agent scripts create
  projects and read them by id, which is unchanged. An API started before this
  change lists projects without their summary until it is restarted.
