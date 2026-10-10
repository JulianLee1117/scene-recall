# Lab workspace conventions

Use these conventions when adding or changing an experiment. The
[architecture contract](search-architecture.md#search-and-lab-application-boundary)
defines the boundary; [ADR-0051](decisions/0051-shared-lab-navigation-and-unsaved-drafts.md)
records the decision. Keep experiment tools specific to their task while reusing
the existing navigation and editing lifecycle.

## Register the entry and saved-work routes

Add an explicit entry to [the registry](../pipeline/lab/registry.py):

- `route` opens the experiment from Labs.
- `project_route` opens an existing saved edit with `?project=<id>`.
- `persistence` describes the entry: `project` for an editor with local drafts,
  or `session` for an experiment that does not create edit projects.

AI Music Video uses `/lab/music-sketch` for both routes. Match Cuts opens
`/match` for both, under its original `visual-rhymes` identifier. Transitions
uses `/lab/transitions` with session persistence and no saved-edit lifecycle. Recent
projects must use `project_route`, even when the experiment's entry is a session.
Do not special-case these paths independently in cards and recent-project lists.
This is a small application registry, not a plugin framework.

## Use one workspace header

Render [LabWorkspaceHeader](../web/features/lab/kit/LabWorkspaceHeader.tsx) first
in `<main>`, before any container of your own, in loading, empty, unavailable
and error states as well as the working screen. It is the app's chrome: the
app bar shared with the home page, then the **Labs** return and the
experiment's title, in the page tokens. Everything below it is yours: your own
width, background, palette and tools, however the experiment turns out. Do not
re-frame the header (it takes no class), and do not add an Exit, brand or
Scene Search control of your own; the bar already leads everywhere.
For initial entry, use the shared `LabEmptyState` export from `LabWorkspaceHeader`
to keep the title, explanation and first action consistent between experiments.

Project editors pass their state to the header, which composes
[ProjectActions](../web/features/lab/kit/ProjectActions.tsx) and its shared exit
guard; every way out, the app bar's places included, then asks about unsaved
edits. Keep project naming, save status, Save and the saved project's menu in
this shared area. A session supplies only its title, and may pass `tools` for
a few header actions.

## Create projects only when there is work to keep

Project editors use [useLabProject](../web/features/lab/kit/useLabProject.ts):

1. Opening without a saved project fetches
   `GET /lab/experiments/{experiment_id}/draft`. Its canonical document has
   `id: ""` and `revision: 0`; fetching it writes no project, revision or job.
2. Track document and name changes against that baseline. Renaming, applying
   settings, selecting a source for an edit, trimming and placing clips count
   as edits. Browsing results, playback, moving the playhead and opening panels
   do not. Undoing all changes restores the clean state.
3. Leaving a clean workspace returns to Labs without a prompt or save request.
   Leaving with edits offers **Save and exit**, **Discard and exit**, and
   **Cancel**. Failed saves and edits made during a save keep the workspace open.
4. The first save posts the complete changed document to `POST /lab/projects`.
   The API validates all source anchors before writing the project and its
   first revision together. Subsequent saves use the existing revision guard.
   A clean Save is a no-op; entering a screen must never issue a creation POST.
5. An operation that needs an edit snapshot first saves its actual input, then
   queues the project job. Do not enqueue a project job against revision zero.
   A clean exit can leave a durable job running; exiting is not cancellation.

Keep transient controls out of the project document. Do not add an autosave on
mount, an empty project followed by an immediate update, or a second local
persistence layer. Existing explicitly created projects and revisions remain
valid; do not silently delete or hide older projects because they appear empty.

Match Cuts at `/match` uses the session lifecycle: searches are synchronous
reads of the moment index, the reference and settings live in the URL, and a
match-cut chain stays in the browser session. Nothing creates a job or an edit
project (ADR-0099). Sharing navigation does not imply that
every experiment needs Save, a name or a project history.

Transitions at `/lab/transitions` follows the same projectless boundary. A render
freezes a pair of source windows and a parameterized recipe in a durable job;
opening the workspace or changing controls creates no project. A render retains
its recorded inputs, restored explicitly with **Use these settings**. Keep
experimental transition controls, durable returned-video imports and explicitly
quoted Runway generation inside this workspace until an explicit editor
integration decision (ADR-0070 and ADR-0071). AI generations own their source
frames, cost/task receipts and output independently of manual import notes;
opening the workspace or requesting a local quote makes no paid call.

## Keep original media separate from drafts

`POST /lab/tracks` imports original audio and returns only its public identity,
name and duration. Importing for a new draft does not create a project or start
analysis. The music picker stages the track and passage locally; Cancel restores
the opening document, name and Undo history. **Use this section** applies the
selection and saves its input before the rhythm job starts.

Preserve imported originals even if the user cancels the picker or discards the
draft. Tracks are hashed durable evidence, not disposable project attachments.
The existing saved-project track endpoint retains its revision checks and
replacement behavior. Do not send local server paths from clients or delete
original media as part of draft cleanup.

## Verify the lifecycle

For each new project editor, check entry and untouched exit leave project,
revision and job counts unchanged; a first save creates one complete revision;
an invalid first save creates none; and abandoning a staged import creates no
project. Check dirty exit, save failure, save races, Undo to clean, saved-project
reopening and active-job exit. For a session, confirm query jobs never create
projects. Verify the shared header at desktop and narrow widths, including a
failed load, and keep navigation available without duplicating exit controls.
