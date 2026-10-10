"use client";

import { useState } from "react";
import { mediaUrl, seconds } from "@/lib/lab";
import type {
  FrozenSearchReference,
  LabClip,
  MusicDirection,
  MusicMatchEvidence,
  MusicRecipeFacet,
  MusicSearchCapabilities,
  MusicSearchClause,
  MusicSearchFacet,
  MusicSearchPlan,
  ResolvedMusicSearch,
} from "@/types/lab";
import { addTextSearchClue, newTextClueFacets } from "./musicEdit";
import styles from "./musicSearchDetails.module.css";

const SIGNAL_HINTS: Record<MusicRecipeFacet, string> = {
  all: "An overall match for your description.",
  scene: "People, places and actions described in the scene.",
  words: "Spoken dialogue or words visible in the image.",
  look: "Visual appearance, such as color, light or subject matter.",
  mood: "Emotion and energy, such as uneasy or calm.",
  composition: "Layout and framing from a reference image.",
};

type Props = {
  embedded?: boolean;
  direction: MusicDirection;
  resolved: ResolvedMusicSearch | null;
  evidence: MusicMatchEvidence | null;
  capabilities: MusicSearchCapabilities | null;
  clips: LabClip[];
  films: Record<string, { title: string }>;
  disabled: boolean;
  onFacet: (facet: MusicSearchFacet) => void;
  onPlan: (plan: MusicSearchPlan | null) => void;
};

function referenceChanged(
  reference: FrozenSearchReference | undefined,
  clips: LabClip[],
) {
  if (!reference) return false;
  const clip = clips.find((item) => item.id === reference.clip_id);
  return (
    !clip ||
    clip.film_id !== reference.film_id ||
    Math.abs(clip.source_start - reference.source_start) > 0.001 ||
    Math.abs(clip.source_end - reference.source_end) > 0.001
  );
}

export default function MusicSearchDetails({
  embedded = false,
  direction,
  resolved,
  evidence,
  capabilities,
  clips,
  films,
  disabled,
  onFacet,
  onPlan,
}: Props) {
  const [adding, setAdding] = useState(false);
  const [draftText, setDraftText] = useState("");
  const [draftFacet, setDraftFacet] = useState<MusicRecipeFacet | null>(null);
  const plan = direction.search_plan;
  const facets = capabilities?.facets ?? [];
  const labelOf = (facet: MusicRecipeFacet) =>
    facets.find((item) => item.facet === facet)?.label ?? facet;
  const availableText = facets.filter(
    (item) => item.facet !== "composition" && item.text_available === true,
  );
  const descriptionAvailability = facets.find(
    (item) => item.facet === direction.search_facet,
  )?.text_available;
  const references = plan?.references ?? [];
  const requirements =
    plan?.unverified_requirements ?? resolved?.unverified_requirements ?? [];
  const choices = newTextClueFacets(direction, capabilities);
  const chosenFacet =
    draftFacet && choices.includes(draftFacet) ? draftFacet : choices[0];

  function changeClause(index: number, patch: Partial<MusicSearchClause>) {
    if (!plan) return;
    const clauses = plan.clauses.map((clause, i) =>
      i === index ? { ...clause, ...patch } : clause,
    );
    onPlan({ ...plan, clauses });
  }

  function removeClause(index: number) {
    if (!plan) return;
    const clauses = plan.clauses.filter((_, i) => i !== index);
    onPlan(clauses.length ? { ...plan, clauses } : null);
  }

  function resetDraft() {
    setAdding(false);
    setDraftText("");
    setDraftFacet(null);
  }

  function addDraftClue() {
    const next = addTextSearchClue(direction, chosenFacet, draftText, capabilities);
    if (!next) return;
    onPlan(next);
    resetDraft();
  }

  const Container = embedded ? "div" : "details";
  return (
    <Container className={styles.details}>
      {!embedded && <summary>Search clues{plan ? ` · ${plan.clauses.length}` : ""}</summary>}
      <div className={styles.content}>
        {plan ? (
          <>
            <p>Combine up to three clues. Edit each detail to guide the search.</p>
            <div className={styles.clauses}>
              {plan.clauses.map((clause, index) => {
                const reference = references.find(
                  (item) => item.reference_id === clause.reference_id,
                );
                const stale = referenceChanged(reference, clips);
                const capability = facets.find(
                  (item) => item.facet === clause.facet,
                );
                const ready = capability?.[
                  clause.kind === "text" ? "text_available" : "source_available"
                ];
                const textChoices = availableText.filter(
                  (item) =>
                    item.facet === clause.facet ||
                    !plan.clauses.some((other) => other.facet === item.facet),
                );

                return (
                  <div className={styles.clause} key={`${index}-${clause.kind}`}>
                    <div className={styles.clauseHeading}>
                      {clause.kind === "text" ? (
                        <label>
                          <span className={styles.srOnly}>
                            Clue {index + 1} search signal
                          </span>
                          <select
                            disabled={disabled || !capabilities}
                            value={clause.facet}
                            onChange={(event) =>
                              changeClause(index, {
                                facet: event.target.value as MusicRecipeFacet,
                              })
                            }
                          >
                            {!availableText.some((item) => item.facet === clause.facet) && (
                              <option value={clause.facet}>
                                {labelOf(clause.facet)}
                              </option>
                            )}
                            {textChoices.map((item) => (
                              <option key={item.facet} value={item.facet}>
                                {labelOf(item.facet)}
                              </option>
                            ))}
                          </select>
                        </label>
                      ) : (
                        <strong>{labelOf(clause.facet)} · reference</strong>
                      )}
                      <button
                        type="button"
                        disabled={disabled}
                        onClick={() => removeClause(index)}
                        aria-label={`Remove clue ${index + 1}`}
                      >
                        Remove
                      </button>
                    </div>
                    {clause.kind === "text" ? (
                      <label>
                        <span className={styles.srOnly}>Clue {index + 1} text</span>
                        <textarea
                          rows={2}
                          maxLength={400}
                          value={clause.text ?? ""}
                          disabled={disabled}
                          onChange={(event) =>
                            changeClause(index, { text: event.target.value })
                          }
                        />
                        {!clause.text?.trim() && (
                          <small className={styles.warning}>
                            Write a clue or remove it before searching.
                          </small>
                        )}
                      </label>
                    ) : reference ? (
                      <div className={styles.reference}>
                        <img
                          src={mediaUrl(
                            `/media/keyframe/${encodeURIComponent(reference.unit_id)}/${reference.frame_index}`,
                          )}
                          alt={`Reference frame from ${films[reference.film_id]?.title ?? reference.film_id}`}
                          loading="lazy"
                        />
                        <div>
                          <strong>
                            {films[reference.film_id]?.title ??
                              reference.film_id.replaceAll("_", " ")}
                          </strong>
                          <span>{seconds(reference.timestamp)} · saved frame</span>
                        </div>
                      </div>
                    ) : (
                      <p className={styles.warning}>
                        This reference is not bound to a saved frame. Remove this clue
                        or use Rewrite prompt under AI prompt assistance.
                      </p>
                    )}
                    {stale && (
                      <p className={styles.warning}>
                        The reference scene changed. Remove this clue or use Rewrite
                        prompt under AI prompt assistance to plan with current references.
                      </p>
                    )}
                    {ready === false && (
                      <p className={styles.warning}>
                        This search signal is currently unavailable.
                      </p>
                    )}
                    {ready == null && (
                      <small>Availability has not been verified.</small>
                    )}
                    {SIGNAL_HINTS[clause.facet] && (
                      <small>{SIGNAL_HINTS[clause.facet]}</small>
                    )}
                  </div>
                );
              })}
            </div>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onPlan(null)}
            >
              Use only the description
            </button>
          </>
        ) : (
          <>
            <p>Choose what to match, or add another detail.</p>
            <label>
              Search signal
              <select
                disabled={disabled || !capabilities}
                value={direction.search_facet}
                onChange={(event) => onFacet(event.target.value as MusicSearchFacet)}
              >
                {!availableText.some((item) => item.facet === direction.search_facet) && (
                  <option value={direction.search_facet}>
                    {labelOf(direction.search_facet)}
                  </option>
                )}
                {availableText.map((item) => (
                  <option value={item.facet} key={item.facet}>
                    {labelOf(item.facet)}
                  </option>
                ))}
              </select>
            </label>
            {SIGNAL_HINTS[direction.search_facet] && (
              <small>{SIGNAL_HINTS[direction.search_facet]}</small>
            )}
            {capabilities && descriptionAvailability === false && (
              <p className={styles.warning}>
                This search signal is currently unavailable.
              </p>
            )}
            {capabilities && descriptionAvailability == null && (
              <small>Availability has not been verified.</small>
            )}
            {!capabilities && (
              <small>
                Search availability could not be checked. Saved directions remain editable.
              </small>
            )}
          </>
        )}
        {choices.length > 0 && (
          adding ? (
            <div className={styles.composer}>
              <label>
                New clue signal
                <select
                  disabled={disabled}
                  value={chosenFacet}
                  onChange={(event) => setDraftFacet(event.target.value as MusicRecipeFacet)}
                >
                  {choices.map((facet) => (
                    <option key={facet} value={facet}>{labelOf(facet)}</option>
                  ))}
                </select>
              </label>
              <label>
                New clue text
                <textarea
                  autoFocus
                  rows={2}
                  maxLength={400}
                  disabled={disabled}
                  value={draftText}
                  placeholder="Describe one more detail to look for…"
                  onChange={(event) => setDraftText(event.target.value)}
                />
              </label>
              {SIGNAL_HINTS[chosenFacet] && <small>{SIGNAL_HINTS[chosenFacet]}</small>}
              <div>
                <button
                  type="button"
                  disabled={disabled || !draftText.trim()}
                  onClick={addDraftClue}
                >
                  Add clue
                </button>
                <button type="button" disabled={disabled} onClick={resetDraft}>
                  Cancel
                </button>
              </div>
              <small>This clue is only saved when you choose Add clue.</small>
            </div>
          ) : (
            <button
              type="button"
              disabled={disabled}
              onClick={() => setAdding(true)}
            >
              Add text clue
            </button>
          )
        )}
        {requirements.length > 0 && (
          <div className={styles.requirements}>
            <strong>Check by watching</strong>
            <p>These intentions are not verified by this search.</p>
            <ul>
              {requirements.map((requirement, i) => (
                <li key={i}>{requirement}</li>
              ))}
            </ul>
          </div>
        )}
        {resolved && (
          <details className={styles.executed}>
            <summary>Last search used</summary>
            <ul>
              {resolved.clauses.map((clause, index) => (
                <li key={index}>
                  <strong>{labelOf(clause.facet)}</strong> · {clause.kind === "text" ? clause.text : "saved reference frame"}
                </li>
              ))}
            </ul>
            <p>Minimum source duration: {resolved.min_duration.toFixed(2)}s</p>
            {capabilities?.recipe.fusion && <small>{capabilities.recipe.fusion}</small>}
          </details>
        )}
        {evidence && (
          <div className={styles.requirements}>
            <strong>Placed scene’s search evidence · result {evidence.rank}</strong>
            <small>
              From the search that selected this scene; new alternatives have their own evidence.
            </small>
            {evidence.matched_text && <p>{evidence.matched_text}</p>}
            {evidence.matched_text_view && (
              <small>Matched text: {evidence.matched_text_view}</small>
            )}
            {evidence.matched_frame_timestamp !== null && (
              <small>Matched frame: {seconds(evidence.matched_frame_timestamp)}</small>
            )}
            {evidence.matches.length > 0 && (
              <ul>
                {evidence.matches.map((match, index) => {
                  const detail = match.evidence && typeof match.evidence === "object"
                    ? match.evidence as Record<string, unknown>
                    : null;

                  return (
                    <li key={index}>
                      {typeof match.facet === "string"
                        ? labelOf(match.facet as MusicRecipeFacet)
                        : "Search clue"}
                      {typeof match.rank === "number" ? ` · result ${match.rank}` : ""}
                      {typeof detail?.text === "string" && detail.text ? ` · ${detail.text}` : ""}
                      {typeof detail?.timestamp === "number"
                        ? ` · frame at ${seconds(detail.timestamp)}`
                        : ""}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}
        {capabilities && (
          <details className={styles.executed}>
            <summary>Search limits</summary>
            <p>{capabilities.unit_scope}</p>
            <p>{capabilities.source_scope}</p>
            <p>Not verified here: {capabilities.unsupported.join("; ")}.</p>
          </details>
        )}
      </div>
    </Container>
  );
}
