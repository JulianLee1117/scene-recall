# ADR-0067: Keep experimental Match Cuts entry inside Labs

- Status: Accepted
- Date: 2026-09-15
- Supersedes: ADR-0040 for ordinary search and source-player entry only
- Preserves: ADR-0051 Lab navigation and projectless Match search

## Context

Match Cuts is still an experimental workflow. Its actions on ordinary search
results, Saved scenes and the main scene player make it look like part of the
established scene search product. The user wants to keep this experiment
independent in Labs until it is ready for broader integration.

## Decision

Remove Match Cuts actions from the shared search/Saved result card and main scene
player. Keep ordinary Save, Find related and category-reference actions intact.
Cards with no available actions must not render an empty action container.

Enter new Match Cuts discovery through the Lab directory, which opens the
existing projectless `/match` workspace. Choose its reference scene inside that
workspace. Preserve the route, source-selection helper, restored search links,
existing saved Lab projects and the engine/API/job contracts. The workspace
continues to return to Labs.

This changes only where the experiment is offered. It does not change retrieval,
ranking, prepared coverage, evidence profiles, exact-pair audition or persistence.
Reintroducing ordinary-search or editor integration requires an explicit product
decision supported by the experiment's existing effectiveness gates.

## Verification

Check that ordinary search/Saved cards and the main player have no Match Cuts
action, while the Lab directory still opens `/match` and its reference selection,
restored jobs and previews remain available. Run TypeScript and the existing Lab
navigation, Match search and scene-player checks.
