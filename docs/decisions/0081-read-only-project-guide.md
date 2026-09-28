# ADR-0081: Independent read-only project guide

- Status: Accepted
- Date: 2026-09-20

## Context

The user wants an accessible technical explanation of the current project,
including understandable processes, diagrams, models and algorithms. Static model
names alone would drift when runtime configuration changes; generating an
explanation with a model on every visit would add cost and uncertainty.

## Decision

Add a lazy-loaded Info tab with topic navigation and small HTML/CSS overview
diagrams. Present one topic as a continuous article with visible steps, adjacent
model/method details and a separate code-reference section. Diagrams summarize
the article rather than opening nested disclosures or scrolling the reader.
Storage is shown as independent ownership layers, not a sequential process.
Keep explanatory content separate from presentation in
`web/features/info/guide.ts`, maintained with the architecture contract. This
feature does not participate in ingestion, search ranking or editor execution.

Read selected model names and tuning values from a small `GET /project/info`
endpoint. Use an explicit field allowlist over `app.state.config`; never dump
the configuration object, environment, storage paths or credentials. Opening or
refreshing the guide must not load models, scan storage, query the index, call
providers or enqueue work. An API outage does not hide the explanations.

Label this response as loaded configuration, not feature readiness or per-film
provenance. A configured model/profile can differ from historical derived
artifacts and does not establish complete library coverage. Refresh does not
hot-reload configuration or reconcile independent workers.

## Consequences

- A small standalone feature explains the system without new diagram packages,
  a second documentation backend or coupling to processing code.
- Model and tuning changes are reflected after the API loads its configuration.
- Process descriptions still require deliberate review when the architecture
  changes; the architecture contract remains authoritative.
