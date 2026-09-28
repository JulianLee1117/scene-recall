# ADR-0051: Shared Lab navigation and unsaved drafts

- Status: Accepted
- Date: 2026-09-14
- Supersedes: ADR-0035 for the separate workspace Exit control only
- Extends: ADR-0024 for unsaved editor drafts and first-save creation
- Superseded by: None

## Context

AI Music Video and Match Cuts had different entry and return controls. Opening
an editor could create a saved project before the user made an edit, leaving
abandoned empty projects in the directory. Match search already supports
durable query jobs without an edit project. A common navigation pattern must
preserve that distinction and protect actual unsaved edits.

## Decision

Use one shared `LabWorkspaceHeader` with a Labs return control for all workspace
states. Project editors compose the existing `ProjectActions` and
`useLabProject` lifecycle; sessions need no project controls. The explicit
registry owns entry `route`, saved-edit `project_route`, and entry `persistence`.
Match Cuts enters `/match`, while existing edits retain `/lab/visual-rhymes`.
Do not introduce a generic plugin framework or duplicate exit destinations.

Opening a project editor without a saved ID loads a read-only canonical draft,
with an empty ID and revision zero. Untouched entry and exit write nothing and
show no save prompt. Actual document/name changes enable Save and the existing
Save/Discard/Cancel exit guard. A first save validates all new source anchors
and writes the supplied document as the first revision atomically. Subsequent
saves preserve optimistic revisions and the existing durable job contract.
Project jobs first save their changed input; session Match search continues to
create only query jobs. Explicit API project creation remains compatible.

Audio originals can be imported independently through `POST /lab/tracks`.
An unsaved music picker stages this identity locally; cancellation restores its
opening draft and Undo history without creating an edit. Applying the passage
saves its input before local timing preparation. Preserve original audio after
cancellation or discard. Existing saved-project import and deletion rules stay
in force, including revision checks, active-job guards and preservation of media.
Do not automatically remove historical empty projects.

Document reusable implementation and verification conventions in
[Lab workspace conventions](../lab-workspaces.md). They cover loading/error
navigation, dirty-state boundaries, save races and the distinction between
durable original media, edit projects and query sessions.

## Consequences

- Users can enter and leave experiments without accumulating empty projects.
- Both experiments return to Labs consistently while retaining different tools
  and persistence needs.
- First saves retain their complete source-validated state in one revision;
  unsupported anchors cannot leave an empty project behind.
- Unapplied draft edits remain local until saved. Imported originals can remain
  without a project by design; no implicit media cleanup or job cancellation is
  introduced.
- This changes workspace lifecycle, not retrieval, model profiles, rendering or
  the worker's resource and revision boundaries.
