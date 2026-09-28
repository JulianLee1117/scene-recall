# Personal search trial — retired

Status: retired at the owner's request on 2026-09-21 under
[ADR-0090](../decisions/0090-retire-personal-search-trial.md). The trial UI, runtime
integration and activation helper have been removed. This is a historical
protocol, not instructions to resume collection.

The owner found the interaction unintuitive and could not judge useful footage
against unseen scenes that search may have missed. Category suggestion feedback
did not answer that retrieval question. No evaluation phase or completed quality
comparison is claimed. At shutdown the ledger contained two calibration goals,
one provider attempt with unknown cost, and no evaluation goals. Live collection
was disabled; the cost uncertainty is preserved rather than treated as zero.
The 136 KiB ledger remains at `paths.state_dir/search-trial.sqlite3` as a retired
receipt until the old API process is restarted. It is not ordinary library data.

Existing ADR-0086/0088 offline tools and receipts remain separate. Any next
comparison must distinguish fixed-pool reranking from additional candidate
retrieval and evaluate source evidence when neither pool contains a useful scene.

## Historical protocol (inactive)

## The question

Does optional Jev category assistance make Scene Recall more useful to its owner
than the same interface with manual refinements? We need practical personal
feedback, not a population accuracy claim. Category recognition and finding a
useful scene remain separate questions.

## Minimum effort from the user

Use two ordinary searches to check the mechanics, then collect approximately
12 new, genuine search goals during normal use. Twelve is a collection target,
not a requirement to invent tasks or sit through scheduled research sessions.
The initial two are calibration and stay outside the comparison. Existing Booth,
Matrix and Shining examples remain regressions, not new evaluation goals.

For a trial search, record the natural query and, if the meaning is not already
clear, one short private note describing what would make the result useful or
wrong. No category checklist, essay or timestamp answer key is needed. Capture
this before assigning the condition or revealing a suggestion. Jev receives only
the actual query and confirmed movie scope, not the private success note.

After playing results, the user can mark **Found something useful** or **Not yet**.
For a remembered moment, useful means the recognized moment or an acceptable
surrounding scene; for exploration, it means footage they would actually use.
A click or playback alone is not success. Missing feedback stays unknown.
Reformulated results require playing one of those results before recording it as
useful; loading more results for the same submission preserves that playback.
One optional follow-up asks whether a suggestion understood their meaning; the user
can correct it in a few words. Later clarifications are appended, never substituted
for the original intent note.

The Search trial control on the search page enables or pauses collection. A fresh
installation starts disabled. The optional note is entered before submitting the
first description for a goal. **New search goal** ends the previous assignment;
rewording alone does not. Earlier searches are not retroactively trial data.

## The comparison

Keep the UI, retrieval, player and manual controls identical:

- **Manual:** ordinary search with optional manual refinements.
- **Assisted:** the same controls plus up to two unselected Jev suggestions
  inside Refine. The user chooses an aspect, enters or selects refinement text,
  and applies it through the same editable field.

Suggestions never change film scope, create hidden filters, rewrite the query or
introduce a new reranker. Ordinary results do not wait for Jev. Request suggestions
once per submitted query, not per keystroke. Freeze a one-second display deadline;
late, failed or stale responses leave the manual interface available. Populate
suggestions only when opening Refine, so an open menu does not move under a pointer.
Keep every manual choice reachable.

Prepare a shuffled two-manual/two-assisted sequence for each consecutive block
of four genuine goals. Reveal the condition only after recording the goal. Continue
across days as needed; do not repeat the same goal under the opposite condition.
All reformulations of one goal retain its assignment and count as one task.
Related scenes and paraphrases are not new independent trials. Do not force the
owner to wait for an identical-task crossover or supply new participants.

The first twelve evaluation assignments define the initial readout; incomplete
feedback remains visible rather than silently replaced. If there are fewer real
needs, report what exists. Do not keep extending the sample until Jev wins.

This remains a descriptive personal comparison. Task difficulty varies, the owner
already knows some controls, and assistance may teach techniques useful in later
manual searches. Report these limitations; later goals are fresh goals, not naive
participants. Spread feedback over normal use rather than requiring a long session.

## Judge meaning and usefulness separately

The user owns intent and usefulness. An agent may check source frames, stored
captions, timestamps, candidate traces, latency and provider receipts, but cannot
invent the user's intended meaning or substitute its preferences for theirs.

In assisted tasks, suggestion acceptance is not proof of correct interpretation.
Keep the optional user judgment separate: helpful interpretation, unnecessary,
wrong, or unclear. The interactive client requests interpretation only for assisted
submissions. Manual goals remain a retrieval/control baseline; they do not produce
an automatic category accuracy score. The original query and intent note remain
the reference.

Report confirmed useful outcomes, confirmed unsuccessful outcomes and unknown
feedback for every assigned goal, grouped by condition and task type. Include
ignored suggestions, timeouts and unsupported requests. Do not compute a flattering
success rate by dropping missing feedback or failed cases.

Record time and reformulations as secondary diagnostics. Use time to a played,
user-accepted result; retain a 180-second comparison horizon, while allowing the
user to continue searching normally. Confirmed failures or abandonment get the
cap; interrupted activity and missing judgments are explicitly unknown, not
silently successful or failed. Do not compare only fast successful tasks.

For unsuccessful searches, diagnose interpretation, missing capabilities,
missing/incorrect source evidence, candidate/ranking loss, interaction friction
and playback separately. Multiple causes or unknown are allowed. Keep these
failures in the end-to-end account. Not finding a scene does not establish that
it is absent from the library.

## Work only with real capabilities

Content, Appearance and Mood can suggest existing Scene, Look and Mood text
refinements. Dialogue and on-screen words are distinct research intentions but
still share the current combined Words capability. Typed shot language stays in
the normal description; existing Framing requires a reference/image and can gate
candidates. Plot intent does not create plot evidence or a usable filter.

Freeze mappings, labels, tie order, abstention, maximum two suggestions and timeout
after calibration. Preserve unsupported words in the original query. Report
classifier omissions separately from capability limits and suggestions omitted by
the display cap. Unknown intent never authorizes a hard decision.

## Small implementation and stable evidence

One isolated variant switch, the existing bounded query-only adapter, and a local
SQLite ledger are enough. Record query/goal IDs, assignment, confirmed scope,
search and model responses, suggestion exposure, accept/edit/remove, playback,
user feedback and monotonic times. Model judgments, source observations and user
judgments stay separate. No global telemetry service, new queue or ingestion
schema is needed.

Freeze model/prompt and UI/retrieval policy for the comparison. Record index
versions; normal ingestion and other sessions may continue. Flag changed corpus
or code as a comparability limit. Do not secretly repair test evidence, discard
bad tasks or rerun a remembered answer. Source-free-text stays local except the
registered provider input. Unknown charges halt further hosted attempts; no
provider retries. Account late receipts even when the UI deadline has expired.

The implemented ceiling is 60 paid attempts and $0.25 across calibration and
reformulations, using durable shared accounting. This is conservative admission,
not a provider-enforced billing cap. Exhausted budgets fall back to manual
behavior and remain in assisted outcomes. Pausing or restarting does not replenish
the allowance. A single in-flight attempt prevents a model backlog. Pending cost
after interruption or any unpriced attempted response halts further paid calls.

The ledger is `paths.state_dir/search-trial.sqlite3`; API status and the full local
readout are at `GET /search/trial/status` and `GET /search/trial/summary`. Provider requests
and raw responses stay in the local ledger. Automated checks use temporary stores
and fake transport; genuine participant records must not contain QA searches.
The 180-second comparison horizon is a readout convention, not a UI timeout or
automatic success/failure classification. Interrupted timing remains unknown.

## What would justify keeping it?

Remove the previous multi-person and fixed 15-second decision thresholds. Review
whether assistance produces useful wins across several different goals, whether
corrections are easy, and whether those benefits outweigh distraction and delay.
Show the complete small task ledger, including regressions and unknowns, instead
of declaring a statistically significant winner.

If suggestions reliably feel helpful to the owner and observable results support
that impression, keep an opt-in personal experiment and confirm on a fresh batch
of ordinary searches. If interpretation is accurate but does not help, leave the
manual controls sufficient. If the bottleneck is bad or missing evidence, work
on that independently. Any invented movie restriction, lost exclusion or hidden
mandatory gate blocks activation pending investigation. General-use claims or
default production changes still need the existing activation decision; other
participants are not a prerequisite for improving this owner's workflow.

Background: [second-round findings](jev-round-two.md). The earlier
[metric guidance](https://www.microsoft.com/en-us/research/?p=680556) and
[blocking principle](https://www.itl.nist.gov/div898/handbook/pri/section3/pri332.htm)
remain useful, but do not establish statistical power for this personal trial.
