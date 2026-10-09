"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import UseInSearchMenu from "./UseInSearchMenu";
import FacetIcon from "./FacetIcon";
import BookmarkIcon from "./BookmarkIcon";
import { displayTitle, filmLabel, formatTime } from "@/lib/format";
import { FACET_LABELS } from "@/lib/searchRecipe";
import { matchBreakdown } from "@/lib/matchReasons";
import MatchBreakdown from "./MatchBreakdown";
import type { RecipeMatchFacet, SceneAlternative, SearchResult } from "@/types/api";

interface VideoModalProps {
  shot: SearchResult;
  onClose: () => void;
  onUseInSearch?: (shot: SearchResult, facet: RecipeMatchFacet) => void;
  disabledUseFacets?: ReadonlySet<RecipeMatchFacet>;
  sourceReferenceFacet?: RecipeMatchFacet;
  /** Saved scenes by unit, so whichever shot of the scene is picked shows its own state. */
  bookmarkedUnitIds?: ReadonlySet<string>;
  pendingBookmarkUnitIds?: ReadonlySet<string>;
  bookmarkDisabled?: boolean;
  onToggleBookmark?: (shot: SearchResult) => void;
}

const BADGE_LABELS = { iconic: "Iconic", gem: "Hidden gem" } as const;
const NO_UNITS: ReadonlySet<string> = new Set();

/** Another shot of the same scene, as a result the player's actions can take. */
function sceneShot(shot: SearchResult, alternative: SceneAlternative): SearchResult {
  return {
    unit_id: alternative.unit_id,
    film_id: shot.film_id,
    film_title: shot.film_title,
    t_start: alternative.t_start,
    t_end: alternative.t_end,
    caption: "",
    keyframe_url: alternative.keyframe_url,
    keyframe_index: alternative.keyframe_index,
    preview_url: alternative.preview_url ?? "",
    thumbnail_url: alternative.thumbnail_url,
    // Its thumbnail's moment, so saving keeps the frame that was picked.
    evidence_timestamp: alternative.hero_time,
    scene: shot.scene,
  };
}

export default function VideoModal({
  shot,
  onClose,
  onUseInSearch,
  disabledUseFacets,
  sourceReferenceFacet,
  bookmarkedUnitIds = NO_UNITS,
  pendingBookmarkUnitIds = NO_UNITS,
  bookmarkDisabled = false,
  onToggleBookmark,
}: VideoModalProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  const hasSeenCanPlay = useRef(false);
  const [timestampCopied, setTimestampCopied] = useState(false);
  const titleId = useId();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "";
  const [playbackAttempt, setPlaybackAttempt] = useState(0);
  const [playback, setPlayback] = useState<{
    filmId: string; attempt: number; url?: string; error?: string;
  } | null>(null);
  const currentPlayback = playback?.filmId === shot.film_id && playback.attempt === playbackAttempt ? playback : null;
  const evidenceTime = shot.matched_frame_timestamp ?? shot.focus_start ?? shot.t_start;
  const [playheadTime, setPlayheadTime] = useState(evidenceTime);
  // A matched subtitle line is the most precise moment to start from.
  const seekTarget = Math.max(0, (shot.matched_line?.t_start ?? evidenceTime) - 1);
  const filmTitle = displayTitle(shot.film_title ?? filmLabel(shot.film_id));
  const hasBreakdown = matchBreakdown(shot).rows.length > 0;
  // The scene's shots from this search. Picking one makes it the shot the
  // player's actions (Save, Related, Match cuts) act on.
  const sceneShots = useMemo(
    () => [shot, ...(shot.scene_alternatives ?? []).map((alternative) => sceneShot(shot, alternative))],
    [shot],
  );
  const [pickedUnitId, setPickedUnitId] = useState(shot.unit_id);
  const current = sceneShots.find((item) => item.unit_id === pickedUnitId) ?? shot;
  const isRetrieved = current.unit_id === shot.unit_id;
  const momentOf = (item: SearchResult) => (item === shot ? evidenceTime : item.evidence_timestamp ?? item.t_start);
  const currentMoment = momentOf(current);
  const bookmarked = bookmarkedUnitIds.has(current.unit_id);
  // Match cuts start from what is on screen while it is still the current shot.
  const matchTime = playheadTime >= current.t_start && playheadTime <= current.t_end ? playheadTime : currentMoment;
  const matchCutsHref = `/match?unit_id=${encodeURIComponent(current.unit_id)}&time=${matchTime.toFixed(3)}`;
  const pickShot = (item: SearchResult) => {
    setPickedUnitId(item.unit_id);
    if (videoRef.current) videoRef.current.currentTime = item === shot ? evidenceTime : item.t_start;
  };

  useEffect(() => {
    hasSeenCanPlay.current = false;
    setPlayheadTime(evidenceTime);
    setTimestampCopied(false);
    setPickedUnitId(shot.unit_id);
  }, [shot, evidenceTime]);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const controller = new AbortController();
    const filmId = shot.film_id;
    hasSeenCanPlay.current = false;
    void (async () => {
      let errorMessage = "Playback could not be loaded. Check that the API and film are available, then retry.";
      try {
        const response = await fetch(`${apiUrl}/video/${encodeURIComponent(filmId)}/playback`, {
          signal: controller.signal, cache: "no-store",
        });
        if (!response.ok) {
          const problem = await response.json().catch(() => null);
          if (typeof problem?.detail === "string" && problem.detail.trim()) errorMessage = problem.detail;
          throw new Error(errorMessage);
        }
        const result = await response.json();
        if (typeof result?.url !== "string" || !result.url.startsWith("/video/")) throw new Error(errorMessage);
        if (!controller.signal.aborted) setPlayback({ filmId, attempt: playbackAttempt, url: result.url });
      } catch {
        if (!controller.signal.aborted) setPlayback({ filmId, attempt: playbackAttempt, error: errorMessage });
      }
    })();
    return () => controller.abort();
  }, [apiUrl, shot.film_id, playbackAttempt]);

  const handleCanPlay = useCallback(() => {
    const video = videoRef.current;
    if (!video || hasSeenCanPlay.current) return;
    hasSeenCanPlay.current = true;
    video.currentTime = seekTarget;
    video.play().catch(() => {
      // Autoplay may be blocked; native controls remain available.
    });
  }, [seekTarget]);

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const focusFrame = window.requestAnimationFrame(() => {
      closeButtonRef.current?.focus();
    });

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;

      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'button:not([disabled]), video[controls], [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => !element.hasAttribute("hidden"));

      if (focusable.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      window.removeEventListener("keydown", handleKeyDown);
      if (previouslyFocused?.isConnected) previouslyFocused.focus();
    };
  }, []);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  const copyTimestamp = useCallback(() => {
    void navigator.clipboard
      .writeText(`${filmTitle} ${formatTime(videoRef.current?.currentTime ?? playheadTime)} (${(videoRef.current?.currentTime ?? playheadTime).toFixed(3)}s)`)
      .then(() => {
        setTimestampCopied(true);
        window.setTimeout(() => setTimestampCopied(false), 1600);
      })
      .catch(() => setTimestampCopied(false));
  }, [filmTitle, playheadTime]);

  return (
    <div
      className="modal-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className="modal-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <div className="modal-header">
          <span id={titleId} className="modal-title">{filmTitle}</span>
          <span className="modal-time">
            {isRetrieved && typeof shot.matched_frame_timestamp === "number"
              ? `${formatTime(evidenceTime)} match`
              : `${formatTime(current.t_start)} – ${formatTime(current.t_end)}`}
          </span>
          <div className="modal-header-actions">
            <button ref={closeButtonRef} type="button" onClick={onClose} aria-label="Close">×</button>
          </div>
        </div>

        {currentPlayback?.error ? <div className="modal-evidence">
          <p role="alert">{currentPlayback.error}</p>
          <div className="modal-actions"><button type="button" onClick={() => setPlaybackAttempt((attempt) => attempt + 1)}>Retry playback</button></div>
        </div> : currentPlayback?.url ? <video
          key={`${shot.unit_id}:${evidenceTime}:${currentPlayback.url}`}
          ref={videoRef}
          src={`${apiUrl}${currentPlayback.url}`}
          controls
          playsInline
          onCanPlay={handleCanPlay}
          onError={() => setPlayback({ filmId: shot.film_id, attempt: playbackAttempt,
            error: "This video could not play. Retry playback, or check that the film is available." })}
          onTimeUpdate={(event) => setPlayheadTime(event.currentTarget.currentTime)}
          onSeeked={(event) => setPlayheadTime(event.currentTarget.currentTime)}
          style={{ width: "100%", display: "block", background: "#000" }}
        /> : <p className="modal-evidence" role="status">Loading player…</p>}

        <div className="modal-toolbar">
          <div className="modal-anchor-context">
            <span>Playing <strong>{formatTime(playheadTime)}</strong></span>
            <button type="button" onClick={() => pickShot(shot)}>
              Return to retrieved moment · {formatTime(evidenceTime)}
            </button>
          </div>
          <div className="modal-actions">
            {onToggleBookmark && (
              <button
                type="button"
                className={bookmarked ? "is-active" : undefined}
                disabled={bookmarkDisabled || pendingBookmarkUnitIds.has(current.unit_id)}
                onClick={() => onToggleBookmark(current)}
                aria-label={bookmarked ? "Remove this shot from Saved" : "Save this shot"}
                aria-pressed={bookmarked}
                title={bookmarked ? "Remove from Saved" : `Save this shot at ${formatTime(currentMoment)}`}
              >
                <BookmarkIcon filled={bookmarked} size={14} />
                {bookmarked ? "Saved" : "Save"}
              </button>
            )}
            {onUseInSearch && sourceReferenceFacet ? (
              <button
                type="button"
                className="modal-source-picker-use"
                disabled={!Number.isInteger(current.keyframe_index)}
                onClick={() => onUseInSearch(current, sourceReferenceFacet)}
              >
                <FacetIcon facet={sourceReferenceFacet} size={15} />
                Use for {FACET_LABELS[sourceReferenceFacet]}
              </button>
            ) : onUseInSearch ? (
              <UseInSearchMenu
                shot={current}
                onUse={onUseInSearch}
                variant="modal"
                disabled={!Number.isInteger(current.keyframe_index)}
                disabledFacets={disabledUseFacets}
              />
            ) : null}
            <a
              className="modal-action-link"
              href={matchCutsHref}
              target="_blank"
              rel="noopener"
              title={`Find shots that cut well from ${formatTime(matchTime)} (opens Match Cuts in a new tab)`}
            >
              Match cuts
            </a>
            <button type="button" onClick={copyTimestamp} title="Copy the film title and current time">
              {timestampCopied ? "Copied" : "Copy time"}
            </button>
          </div>
        </div>

        <div className="modal-evidence">
          {/* The current shot's own details; a picked shot shares only the scene's. */}
          {(current.scene?.title || (current.badges?.length ?? 0) > 0) && (
            <div className="modal-story-header">
              {current.scene?.title && <strong>{current.scene.title}</strong>}
              {current.badges?.map((badge) => (
                <span key={badge} className={`result-badge result-badge-${badge}`}>{BADGE_LABELS[badge]}</span>
              ))}
            </div>
          )}
          {current.scene?.summary && <p className="modal-scene-summary">{current.scene.summary}</p>}
          {current.action && (
            <p className="modal-story-action">
              {current.action}
              {(current.characters?.length ?? 0) > 0 && <span> — {current.characters?.join(", ")}</span>}
            </p>
          )}
          {current.famous_line && <p className="modal-famous-line">“{current.famous_line}”</p>}
          {current.caption && <p className="modal-caption">{current.caption}</p>}
          {isRetrieved && hasBreakdown && (
            <section className="modal-reasons" aria-label="Why this scene ranked here">
              <h3>Why it&apos;s here</h3>
              <MatchBreakdown shot={shot} />
            </section>
          )}
          {sceneShots.length > 1 && (
            <div className="modal-scene-alternatives" aria-label="Matching shots in this scene">
              <span>Matching shots in this scene</span>
              <div>
                {sceneShots.map((item) => (
                  <button
                    key={item.unit_id}
                    type="button"
                    aria-pressed={item.unit_id === current.unit_id}
                    title={`Pick the shot at ${formatTime(item.t_start)}`}
                    onClick={() => pickShot(item)}
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={`${apiUrl}${item.thumbnail_url ?? item.keyframe_url}`} alt="" loading="lazy" />
                    <span>{formatTime(momentOf(item))}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
