"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import UseInSearchMenu from "./UseInSearchMenu";
import FacetIcon from "./FacetIcon";
import BookmarkIcon from "./BookmarkIcon";
import { displayTitle, filmLabel, formatTime } from "@/lib/format";
import { FACET_LABELS } from "@/lib/searchRecipe";
import { matchBreakdown, matchedWordsEvidence } from "@/lib/matchReasons";
import MatchBreakdown from "./MatchBreakdown";
import ShotDialogue from "./ShotDialogue";
import { displayMoment } from "@/lib/resultMoment";
import { matchingShots } from "@/lib/sceneShots";
import type { RecipeMatchFacet, SearchResult } from "@/types/api";

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
  const evidenceTime = displayMoment(shot);
  const wordsEvidence = matchedWordsEvidence(shot);
  const resultTime = wordsEvidence?.t_start ?? evidenceTime;
  const [playheadTime, setPlayheadTime] = useState(resultTime);
  // A spoken passage gets a short lead-in. Visual results start at the displayed
  // moment itself: leading in can cross a cut into a different picture.
  const seekTarget = Math.max(0, resultTime - (wordsEvidence?.t_start !== undefined ? 1 : 0));
  const filmTitle = displayTitle(shot.film_title ?? filmLabel(shot.film_id));
  // Matching shots in film order. Picking one makes it the shot the
  // player's actions (Save, Related, Match cuts) act on.
  const sceneShots = useMemo(
    () => matchingShots(shot),
    [shot],
  );
  const [pickedUnitId, setPickedUnitId] = useState(shot.unit_id);
  const current = sceneShots.find((item) => item.unit_id === pickedUnitId) ?? shot;
  const hasBreakdown = matchBreakdown(current).rows.length > 0;
  // Beyond this scene's shots, the shot on screen comes from the library, so
  // browsing the film retargets the actions to what you are watching.
  const [onScreen, setOnScreen] = useState<SearchResult | null>(null);
  // The result also owns its matched moment, which can sit on its shot's edge.
  const playing = (item: SearchResult | null | undefined): item is SearchResult =>
    Boolean(item && ((playheadTime >= item.t_start && playheadTime < item.t_end)
      || (item === shot && Math.abs(playheadTime - evidenceTime) < 0.5)));
  const sceneTarget = playing(current) ? current : sceneShots.find(playing);
  // The last shot found stays the target until the next one arrives, so a cut
  // never flashes back to the result for the moment a lookup takes.
  const browsed = !sceneTarget && onScreen ? onScreen : null;
  // What Save, Related and Match cuts act on: the shot on screen, else the picked one.
  const target = sceneTarget ?? browsed ?? current;
  const onResult = target.unit_id === shot.unit_id;
  const momentOf = displayMoment;
  // A scene shot keeps the moment its picture shows; a browsed one, the frame on screen.
  const savedMoment = target === browsed ? playheadTime : momentOf(target);
  const bookmarked = bookmarkedUnitIds.has(target.unit_id);
  const matchTime = playing(target) ? playheadTime : momentOf(target);
  const matchCutsHref = `/match?unit_id=${encodeURIComponent(target.unit_id)}&time=${matchTime.toFixed(3)}`;
  // The line names the scene on screen, the result's too, so it changes with
  // the story rather than at every cut. Away from the result's scene it shows
  // only the time until the library has said what is playing.
  const nowShowing = (browsed ? browsed.scene : playing(target) ? target.scene : undefined)?.title ?? "";
  const pickShot = (item: SearchResult) => {
    setPickedUnitId(item.unit_id);
    setPlayheadTime(momentOf(item));
    if (videoRef.current) videoRef.current.currentTime = momentOf(item);
  };
  const seekDialogue = (time: number) => {
    if (!videoRef.current) return;
    videoRef.current.currentTime = time;
    void videoRef.current.play().catch(() => { /* Native controls remain available. */ });
    setPlayheadTime(time);
  };
  // Ask which shot is on screen only beyond this scene, and only until one answers.
  const lookup = sceneTarget || playing(onScreen) || !currentPlayback?.url ? null : Math.round(playheadTime * 10) / 10;
  useEffect(() => {
    if (lookup === null) return;
    const controller = new AbortController();
    void fetch(`${apiUrl}/library/shot?film_id=${encodeURIComponent(shot.film_id)}&t=${lookup}`, { signal: controller.signal, cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((found: SearchResult | null) => { if (found && !controller.signal.aborted) setOnScreen(found); })
      .catch(() => { /* no shot at that time, or superseded */ });
    return () => controller.abort();
  }, [apiUrl, shot.film_id, lookup]);

  useEffect(() => {
    hasSeenCanPlay.current = false;
    setPlayheadTime(resultTime);
    setTimestampCopied(false);
    setPickedUnitId(shot.unit_id);
    setOnScreen(null);
  }, [shot, resultTime]);

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
            {onResult && (typeof shot.matched_frame_timestamp === "number" || wordsEvidence?.t_start !== undefined)
              ? `${formatTime(resultTime)} match`
              : `${formatTime(target.t_start)} – ${formatTime(target.t_end)}`}
          </span>
          <div className="modal-header-actions">
            <button ref={closeButtonRef} type="button" onClick={onClose} aria-label="Close">×</button>
          </div>
        </div>

        {currentPlayback?.error ? <div className="modal-evidence">
          <p role="alert">{currentPlayback.error}</p>
          <div className="modal-actions"><button type="button" onClick={() => setPlaybackAttempt((attempt) => attempt + 1)}>Retry playback</button></div>
        </div> : currentPlayback?.url ? <video
          key={`${shot.unit_id}:${resultTime}:${currentPlayback.url}`}
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
          {/* What is on screen, which the actions below act on, and the way back to the result. */}
          <div className="modal-anchor-context">
            <span className="modal-now">
              Playing <strong>{formatTime(playheadTime)}</strong>
              {nowShowing && <span className="modal-now-line"> · {nowShowing}</span>}
            </span>
            <button type="button" onClick={() => { setPickedUnitId(shot.unit_id); seekDialogue(resultTime); }}>
              Back to result · {formatTime(resultTime)}
            </button>
          </div>
          <div className="modal-actions">
            {onToggleBookmark && (
              <button
                type="button"
                className={bookmarked ? "is-active" : undefined}
                disabled={bookmarkDisabled || pendingBookmarkUnitIds.has(target.unit_id)}
                onClick={() => onToggleBookmark(target === browsed ? { ...target, evidence_timestamp: savedMoment } : target)}
                aria-label={bookmarked ? "Remove this shot from Saved" : "Save this shot"}
                aria-pressed={bookmarked}
                title={bookmarked ? "Remove from Saved" : `Save this shot at ${formatTime(savedMoment)}`}
              >
                <BookmarkIcon filled={bookmarked} size={14} />
                {bookmarked ? "Saved" : "Save"}
              </button>
            )}
            {onUseInSearch && sourceReferenceFacet ? (
              <button
                type="button"
                className="modal-source-picker-use"
                disabled={!Number.isInteger(target.keyframe_index)}
                onClick={() => onUseInSearch(target, sourceReferenceFacet)}
              >
                <FacetIcon facet={sourceReferenceFacet} size={15} />
                Use for {FACET_LABELS[sourceReferenceFacet]}
              </button>
            ) : onUseInSearch ? (
              <UseInSearchMenu
                shot={target}
                onUse={onUseInSearch}
                variant="modal"
                disabled={!Number.isInteger(target.keyframe_index)}
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
          {/* The explicitly picked shot's details stay put while the film plays on. Each kind of
              text is labelled: the scene's story, what happens in this shot,
              and what the picture shows. */}
          <dl className="modal-facts">
            {(current.scene?.title || current.scene?.summary || (current.badges?.length ?? 0) > 0) && (
              <div>
                <dt>Scene</dt>
                <dd>
                  {(current.scene?.title || (current.badges?.length ?? 0) > 0) && (
                    <div className="modal-story-header">
                      {current.scene?.title && <strong>{current.scene.title}</strong>}
                      {current.badges?.map((badge) => (
                        <span key={badge} className={`result-badge result-badge-${badge}`}>{BADGE_LABELS[badge]}</span>
                      ))}
                    </div>
                  )}
                  {current.scene?.summary && <p className="modal-scene-summary">{current.scene.summary}</p>}
                </dd>
              </div>
            )}
            {(current.action || current.famous_line) && (
              <div>
                <dt>Shot</dt>
                <dd>
                  {current.action && (
                    <p className="modal-story-action">
                      {current.action}
                      {(current.characters?.length ?? 0) > 0 && <span> — {current.characters?.join(", ")}</span>}
                    </p>
                  )}
                  {current.famous_line && <p className="modal-famous-line">“{current.famous_line}”</p>}
                </dd>
              </div>
            )}
            <ShotDialogue shot={current} onSeek={seekDialogue} canSeek={Boolean(currentPlayback?.url)} />
            {current.caption && (
              <div>
                <dt>Picture</dt>
                <dd><p className="modal-caption">{current.caption}</p></dd>
              </div>
            )}
          </dl>
          {hasBreakdown && (
            <section className="modal-reasons" aria-label="Why this scene ranked here">
              <h3>Why it&apos;s here</h3>
              <MatchBreakdown shot={current} />
            </section>
          )}
          {sceneShots.length > 1 && (
            <div className="modal-scene-alternatives" aria-label="Matching shots">
              <span>Matching shots</span>
              <div>
                {sceneShots.map((item) => (
                  <button
                    key={item.unit_id}
                    type="button"
                    aria-pressed={item.unit_id === target.unit_id}
                    title={`Pick the shot at ${formatTime(momentOf(item))}`}
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
