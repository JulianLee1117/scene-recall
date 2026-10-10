"use client";

import type { SyntheticEvent } from "react";
import { mediaUrl, seconds } from "@/lib/lab";
import type { LabClip, MusicAlternative, MusicMatchEvidence } from "@/types/lab";
import EditorIcon from "@/features/lab/kit/EditorIcon";
import { sameSceneCrop } from "./nextScene";
import { useLabSceneDrag } from "./useLabSceneDrag";
import styles from "./musicSceneChoices.module.css";

type Props = {
  alternatives: MusicAlternative[];
  currentClip: LabClip | null;
  films: Record<string, { title: string }>;
  disabled: boolean;
  locked: boolean;
  minimumDuration?: number;
  onDragScene?: (clip: LabClip | null, evidence?: MusicMatchEvidence | null) => void;
  onDropScene?: (slotId: string) => void;
  onPreview: (clip: LabClip) => void;
  onChoose: (clip: LabClip, evidence: MusicMatchEvidence | null) => void;
  onDialogue?: (clip: LabClip, evidence: MusicMatchEvidence | null) => void;
};

export default function MusicSceneChoices({
  alternatives,
  currentClip,
  films,
  disabled,
  locked,
  minimumDuration = 0,
  onDragScene,
  onDropScene,
  onPreview,
  onChoose,
  onDialogue,
}: Props) {
  const drag = useLabSceneDrag({
    enabled: !disabled && !!onDragScene && !!onDropScene,
    onDragScene,
    onDropScene,
  });
  return (
    <ul
      className={styles.choices}
      aria-label="Scene search results"
      onClickCapture={drag.onClickCapture}
    >
      {alternatives.map((alternative, index) => {
        const clip = alternative.clip;
        const tooShort = clip.source_end - clip.source_start < minimumDuration - 1e-6;
        const chosen =
          !!currentClip &&
          currentClip.film_id === clip.film_id &&
          currentClip.unit_id === clip.unit_id &&
          Math.abs(currentClip.source_start - clip.source_start) < 1 / 24 &&
          Math.abs(currentClip.source_end - clip.source_end) < 1 / 24 &&
          sameSceneCrop(currentClip.crop, clip.crop);
        const title = alternative.film_title || films[clip.film_id]?.title || "Scene";
        const matchedFrame = alternative.search_evidence?.matched_frame_index;
        const frameIndex =
          typeof matchedFrame === "number" &&
          Number.isInteger(matchedFrame) &&
          matchedFrame >= 0
            ? matchedFrame
            : 0;
        const thumbnailBase = clip.unit_id
          ? `/media/keyframe/${encodeURIComponent(clip.unit_id)}/`
          : null;

        function handleThumbnailError(event: SyntheticEvent<HTMLImageElement>) {
          const image = event.currentTarget;
          const fallback = mediaUrl(`${thumbnailBase}0`);
          if (frameIndex !== 0 && image.getAttribute("src") !== fallback) {
            image.src = fallback;
          } else {
            image.style.visibility = "hidden";
          }
        }

        return (
          <li
            key={`${clip.id}-${index}`}
            className={`${styles.choice} ${chosen ? styles.chosen : ""}`}
            data-draggable={!!onDragScene && !!onDropScene && !disabled}
            onPointerDown={(event) => drag.begin(event, {
              clip, evidence: alternative.search_evidence ?? null, title,
            })}
            onDragStart={(event) => event.preventDefault()}
          >
            <button
              type="button"
              className={styles.preview}
              disabled={disabled}
              onClick={() => onPreview(clip)}
              title="Preview and trim this scene"
              aria-label={`Preview scene ${index + 1} from ${title}`}
            >
              {thumbnailBase && (
                <img
                  src={mediaUrl(`${thumbnailBase}${frameIndex}`)}
                  alt=""
                  loading="lazy"
                  draggable={false}
                  onError={handleThumbnailError}
                />
              )}
              <span>
                <EditorIcon name="play" size={12} />
              </span>
            </button>
            <div className={styles.info}>
              <strong title={title}>{title}</strong>
              {clip.title && <p title={clip.title}>{clip.title}</p>}
              <div className={styles.choiceFooter}>
                <small
                  title={`Source footage from ${seconds(clip.source_start)} to ${seconds(clip.source_end)}`}
                >
                  Source {seconds(clip.source_start)}
                </small>
                {onDialogue && <button type="button" className={styles.use} disabled={disabled}
                  aria-label={`Use dialogue from scene ${index + 1} from ${title}`}
                  onClick={() => onDialogue(clip, alternative.search_evidence ?? null)}>Use dialogue</button>}
                <button
                  type="button"
                  className={styles.use}
                  disabled={disabled || locked || chosen || tooShort}
                  aria-label={
                    chosen
                      ? `Scene ${index + 1} is selected`
                      : `Use scene ${index + 1} from ${title}`
                  }
                  onClick={() => onChoose(clip, alternative.search_evidence ?? null)}
                >
                  {tooShort ? "Too short" : chosen ? "Selected" : "Use scene"}
                </button>
              </div>
              {tooShort && (
                <small className={styles.durationWarning}>
                  {Number((clip.source_end - clip.source_start).toFixed(2))}s source · needs {Number(minimumDuration.toFixed(2))}s
                </small>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
