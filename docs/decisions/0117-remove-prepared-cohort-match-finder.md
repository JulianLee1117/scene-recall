# ADR-0117: Remove the prepared-cohort Match Finder

- Status: Accepted
- Date: 2026-10-09
- Supersedes: ADR-0027 and ADR-0038 (their code); the scene-based search of
  ADR-0040, ADR-0046 and ADR-0048 was already superseded by ADR-0099
- Superseded by: None

## Context

ADR-0099 replaced the prepared-cohort Match Finder with match cuts on a
library-wide moment index, and ADR-0116 removed its web editor. Its Python
stayed frozen: 21 modules under `pipeline/matching` (cohorts, SAM subject
tracks, RAFT motion, DINOv3 descriptors, the scene-based search and its
jobs), a `/matching` router, the match, match-preview, match-search and
match-search-preview job kinds in the worker, the store and the role map, the
match endpoints and apply-match flow in the lab API with their models, the
played-cut ablation script, their tests, and the project hook's handling of
match jobs in the web. Nothing live reached any of it except through those
seams: the moments package borrowed a film lookup from it, and Transitions a
shot lookup. The live job ledger holds only finished or cancelled rows of the
retired kinds.

## Decision

- Remove the finder: every module under `pipeline/matching` outside
  `moments/`, the lab's job adapter, the `/matching` router, the four job
  kinds with their worker branches, store methods and role mapping, the match
  endpoints and models in the lab API, the played-cut ablation script, and
  their tests.
- The two lookups the finder housed live in `pipeline/lab/media.py`:
  `resolve_film`, which the moments package now imports from there, and a
  new `resolve_unit` for Transitions.
- The web's project hook and job progress forget the match job kinds.
- Kept: `pipeline/matching/moments/` and everything built on it; the offline
  region-crop proposals and the DINOv3 challenger under
  `pipeline/experiments`, which never imported the finder; the frame caches
  under the assets folder's `matching/results`, which storage maintenance
  keeps reclaiming by job id; and the old ledger rows, as history.

## Consequences

- `pipeline/matching` is the moment index and its scorer, nothing else.
- Old ledger rows of the removed kinds are inert: no role claims them and no
  endpoint reads them.
- The prepared cohort data on disk serves nothing and can be deleted by hand.
