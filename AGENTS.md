# Scene Recall repository guidance

- `README.md` is the operational source of truth.
- `docs/search-architecture.md` is the current architecture contract.
- `docs/decisions/` contains historical rationale, not current specification.
  Read relevant records before changing a boundary.
- A material architecture change must update the architecture contract and add
  or supersede an accepted ADR in the same work. Update the README only when
  runnable behavior or commands change.
- Preserve raw films and timestamped evidence. Derived annotations, embeddings,
  and indexes must be independently backfillable and model/version scoped.
  Never silently mix incompatible vector spaces.
- Do not implement deferred work without a concrete failure and the decision
  gates required by the architecture.
- Preserve unrelated worktree changes. Run focused tests before the full suite.
- For files under `web/`, also follow `web/AGENTS.md`.
