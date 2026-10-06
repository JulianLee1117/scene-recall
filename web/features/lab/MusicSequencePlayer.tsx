"use client";

import { useEffect, useImperativeHandle, useMemo, useRef, useState, type CSSProperties, type Ref } from "react";
import { experimentName, mediaUrl, seconds } from "@/lib/lab";
import { isPlaybackSpace } from "@/lib/playbackShortcut";
import { directionOf } from "./musicEdit";
import { slotAt } from "./sequencePosition";
import { musicVolume } from "./dialogueAudio";
import { createDialoguePool } from "./dialogueTransport";
import { releaseDialogueAudio } from "./dialogueGain";
import EditorIcon from "./EditorIcon";
import type { LabClip, LabDocument, MusicSlot } from "@/types/lab";
import styles from "./sequencePlayer.module.css";

export interface MusicSequenceHandle {
  seek: (time: number) => void;
  pause: () => void;
}

interface Props {
  ref?: Ref<MusicSequenceHandle>;
  document: LabDocument;
  slots: MusicSlot[];
  playhead: number;
  onTimeChange: (time: number) => void;
  filmTitles?: Record<string, string>;
  onPlayingChange?: (playing: boolean) => void;
  onSelectSlot?: (id: string) => void;
  suspended?: boolean;
  compactTransport?: boolean;
  /** Opens the latest render; the preview plays cuts, so render effects only show there (ADR-0106). */
  onShowExport?: () => void;
}

interface Deck {
  slot: MusicSlot;
  clip: LabClip;
  ratio: number;
}

interface Transport {
  toggle: () => void;
  pause: () => void;
  seek: (time: number) => void;
  audioReady: () => void;
  videoReady: (index: number) => void;
  waiting: (index?: number) => void;
  error: (index?: number) => void;
}

const clamp = (value: number, start: number, end: number) =>
  Math.max(start, Math.min(end, value));

/** Two source decks follow one audio clock. Rendering remains the export path. */
export default function MusicSequencePlayer({
  ref,
  document,
  slots,
  playhead,
  onTimeChange,
  filmTitles = {},
  onPlayingChange,
  onSelectSlot,
  suspended = false,
  onShowExport,
  compactTransport = false,
}: Props) {
  const audio = useRef<HTMLAudioElement>(null);
  const dialogueAudio = useRef(new Map<string, HTMLAudioElement>());
  const dialogueRefs = useRef(new Map<string, (node: HTMLAudioElement | null) => void>());
  function dialogueRef(id: string) {
    let callback = dialogueRefs.current.get(id);
    if (!callback) {
      callback = (node) => {
        const previous = dialogueAudio.current.get(id);
        if (previous && previous !== node) { previous.pause(); releaseDialogueAudio(previous); }
        if (node) dialogueAudio.current.set(id, node);
        else dialogueAudio.current.delete(id);
      };
      dialogueRefs.current.set(id, callback);
    }
    return callback;
  }
  const videos = useRef<Array<HTMLVideoElement | null>>([null, null]);
  const transport = useRef<Transport | null>(null);
  // A scrub is a playback command. Sending it through an effect would turn
  // every pointer move into another effect-driven round of media/state updates.
  useImperativeHandle(ref, () => ({ seek: (time) => transport.current?.seek(time), pause: () => transport.current?.pause() }), []);
  const suspendedRef = useRef(suspended);
  suspendedRef.current = suspended;
  const latest = useRef({ playhead, onTimeChange, onSelectSlot, onPlayingChange });
  latest.current = { playhead, onTimeChange, onSelectSlot, onPlayingChange };
  const [decks, setDecks] = useState<Array<Deck | null>>([null, null]);
  const [activeDeck, setActiveDeck] = useState<number | null>(null);
  const [position, setPosition] = useState(playhead);
  const [playing, setPlaying] = useState(false);
  const [buffering, setBuffering] = useState(false);
  const [error, setError] = useState("");
  const [muted, setMuted] = useState(false);
  const { start, end } = document.passage;
  const trackId = document.track?.id;
  const mixRef = useRef(document);
  mixRef.current = document;
  // Playback position updates must not rebuild the media decks. Only editorial
  // changes reset the transport; the next play then uses the revised sequence.
  const planKey = useMemo(() => JSON.stringify({
    dialogue: document.dialogue_clips,
    slots: slots.map(({ id, start, end, clip_id }) => ({
      id,
      start,
      end,
      clip_id,
    })),
    clips: document.clips.map(
      ({ id, film_id, source_start, source_end, crop }) => ({
        id,
        film_id,
        source_start,
        source_end,
        crop,
      }),
    ),
  }), [slots, document.clips, document.dialogue_clips]);

  useEffect(() => {
    const music = audio.current;
    if (!music) return;
    const voice = createDialoguePool(dialogueAudio.current, document.dialogue_clips ?? []);
    const sourceVideos = videos.current;
    const assignments: Array<Deck | null> = [null, null];
    const pendingSeek: Array<number | null> = [null, null];
    const heldAtEnd = [false, false];
    const clipById = new Map(document.clips.map((clip) => [clip.id, clip]));
    const ordered = [...slots].sort((a, b) => a.start - b.start);
    let active: number | null = null;
    let visible: number | null = null;
    let slotIndex = -2;
    let wanted = false;
    let waiting = false;
    let destroyed = false;
    let starting = false;
    let generation = 0;
    let lastReported = -Infinity;
    let lastReportedTime = NaN;
    let targetTime = clamp(latest.current.playhead, start, end);
    let reportedPlaying: boolean | null = null;

    setPlaying(false);
    setBuffering(false);
    setError("");
    setDecks([null, null]);
    setActiveDeck(null);

    function reportPlaying(value: boolean) {
      if (reportedPlaying === value) return;
      reportedPlaying = value;
      latest.current.onPlayingChange?.(value);
    }
    function showDeck(index: number | null) {
      if (visible === index) return;
      visible = index;
      // Hide the previous deck before reusing it for preload; React's next paint
      // also receives this state, but must not expose an in-flight source seek.
      sourceVideos.forEach((video, i) => {
        if (video?.parentElement) video.parentElement.style.visibility = i === index ? "visible" : "hidden";
      });
      if (!destroyed) setActiveDeck(index);
    }
    function lastFrame(clip: LabClip) {
      return Math.max(clip.source_start, clip.source_end - 1 / document.fps);
    }
    function pauseMedia() {
      music!.pause();
      voice?.pause();
      reportPlaying(false);
      sourceVideos.forEach((video, index) => {
        video?.pause();
        const clip = assignments[index]?.clip;
        if (
          video &&
          clip &&
          video.readyState >= 1 &&
          video.currentTime >= clip.source_end
        ) {
          pendingSeek[index] = lastFrame(clip);
          seekVideo(index);
        }
      });
    }
    function setWaiting(value: boolean) {
      if (waiting === value) return;
      waiting = value;
      setBuffering(value);
    }
    function report(time: number, force = false) {
      const now = performance.now();
      if (
        !force &&
        slotAt(time, ordered, end)?.id === slotAt(lastReportedTime, ordered, end)?.id &&
        (now - lastReported < 50 || Math.abs(time - lastReportedTime) < 0.0001)
      )
        return;
      lastReported = now;
      lastReportedTime = time;
      setPosition(time);
      latest.current.onTimeChange(time);
    }
    function stop() {
      generation += 1;
      starting = false;
      wanted = false;
      pauseMedia();
      setPlaying(false);
      setWaiting(false);
    }
    function fail(message: string) {
      stop();
      setError(message);
    }
    function seekVideo(index: number) {
      const video = sourceVideos[index];
      const requested = pendingSeek[index];
      const clip = assignments[index]?.clip;
      if (!video || !clip || requested === null || video.readyState < 1) return;
      // HTML media rounds to microseconds. Seeking an exact native cut can land
      // just before its first legal frame; a 1ms inset stays inside that frame.
      const inset = Math.min(0.001, (clip.source_end - clip.source_start) / 4);
      const target = clamp(requested + inset, clip.source_start + inset, clip.source_end - inset);
      if (Number.isFinite(video.duration) && target >= video.duration) {
        if (index === active)
          fail(
            "This clip starts beyond the available source. Choose another suggestion.",
          );
        return;
      }
      try {
        if (Math.abs(video.currentTime - target) > 0.000001) {
          if (visible === index) showDeck(null);
          video.currentTime = target;
        }
        pendingSeek[index] = null;
      } catch {
        // loadedmetadata/canplay retries a seek once the media is available.
      }
    }
    function assign(
      index: number,
      slot: MusicSlot,
      clip: LabClip,
      sourceTime: number,
    ) {
      const video = sourceVideos[index];
      if (!video) return;
      const previous = assignments[index];
      const sameSource = previous?.clip.film_id === clip.film_id;
      const ratio = sameSource ? previous.ratio : 16 / 9;
      if (visible === index) showDeck(null);
      assignments[index] = { slot, clip, ratio };
      heldAtEnd[index] = false;
      setDecks([...assignments]);
      video.pause();
      pendingSeek[index] = sourceTime;
      if (!sameSource || video.error) {
        video.src = mediaUrl(`/video/${encodeURIComponent(clip.film_id)}`);
        video.load();
      }
      seekVideo(index);
    }
    function prepare(time: number, force = false) {
      const slot = slotAt(time, ordered, end);
      const nextIndex = slot ? ordered.indexOf(slot) : -1;
      if (nextIndex === slotIndex && !force) return;
      // Freeze the master clock at a cut until its incoming deck can play.
      // Otherwise a slow play()/decode would silently skip the clip's opening.
      generation += 1;
      starting = false;
      pauseMedia();
      slotIndex = nextIndex;
      const clip = slot?.clip_id ? clipById.get(slot.clip_id) : undefined;
      if (slot && clip) {
        let index = assignments.findIndex((deck) => deck?.slot.id === slot.id);
        if (index < 0) index = active === 0 ? 1 : 0;
        if (active !== index) sourceVideos[active ?? -1]?.pause();
        active = index;
        const sourceTime = clamp(
          clip.source_start + time - slot.start,
          clip.source_start,
          clip.source_end - 0.001,
        );
        if (assignments[index]?.slot.id !== slot.id)
          assign(index, slot, clip, sourceTime);
        else if (
          force ||
          Math.abs((sourceVideos[index]?.currentTime ?? 0) - sourceTime) > 0.08
        ) {
          sourceVideos[index]?.pause();
          heldAtEnd[index] = false;
          pendingSeek[index] = sourceTime;
          seekVideo(index);
        }
      } else {
        sourceVideos.forEach((video) => video?.pause());
        active = null;
        showDeck(null);
      }
      preloadNext(time);
    }
    function preloadNext(time: number) {
      // Preparing the next filled slot also covers a gap in the sequence.
      const upcoming = ordered.find(
        (item, index) =>
          index > slotIndex &&
          item.start >= time &&
          item.clip_id &&
          clipById.has(item.clip_id),
      );
      if (upcoming?.clip_id) {
        const index = active === 0 ? 1 : 0;
        // A valid outgoing frame stays on screen while its replacement seeks.
        if (index === visible) return;
        if (assignments[index]?.slot.id !== upcoming.id) {
          assign(
            index,
            upcoming,
            clipById.get(upcoming.clip_id)!,
            clipById.get(upcoming.clip_id)!.source_start,
          );
        }
      }
    }
    function synchronize() {
      if (destroyed) return;
      if (suspendedRef.current) {
        if (wanted) stop();
        return;
      }
      const time = clamp(music!.currentTime, start, end);
      targetTime = time;
      prepare(time);
      sourceVideos.forEach((video, index) => {
        if (index !== active && video && !video.paused) video.pause();
      });
      music!.volume = musicVolume(mixRef.current, time);
      const voiceReady = voice?.prepare(time) ?? true;
      report(time);
      const video = active === null ? null : sourceVideos[active];
      const deck = active === null ? null : assignments[active];
      // An inactive preload may have failed before becoming the selected deck.
      // Surface that failure before readiness checks can leave it buffering.
      if (music!.error) {
        fail(
          "The music could not be played. Check the uploaded track and try again.",
        );
        return;
      }
      if (voice?.error) { fail(voice.error); return; }
      if (video?.error) {
        fail(
          "This source could not be played. Choose another suggestion or check that the original film is available.",
        );
        return;
      }
      if (video && deck && video.readyState >= 2 && !video.seeking && pendingSeek[active!] === null &&
        !heldAtEnd[active!] && video.currentTime >= lastFrame(deck.clip)) {
        // Hold one legal output frame through the remaining audio. Restarting
        // this source would leak the next shot or repeatedly correct its end.
        video.pause();
        heldAtEnd[active!] = true;
        if (video.currentTime >= deck.clip.source_end) {
          pendingSeek[active!] = lastFrame(deck.clip);
          seekVideo(active!);
        }
      }
      const frameReady = !video || !!deck && video.readyState >= 2 && !video.seeking &&
        pendingSeek[active!] === null && video.currentTime >= deck.clip.source_start && video.currentTime < deck.clip.source_end;
      if (frameReady) {
        showDeck(active);
        preloadNext(time);
      } else {
        if (wanted) pauseMedia();
        setWaiting(true);
        return;
      }
      if (!wanted) setWaiting(false);
      if (!wanted) return;
      if (time >= end - 0.001) {
        stop();
        music!.currentTime = end;
        prepare(end, true);
        report(end, true);
        return;
      }
      if (video && deck && !heldAtEnd[active!]) {
        const desired = clamp(
          deck.clip.source_start + time - deck.slot.start,
          deck.clip.source_start,
          deck.clip.source_end - 0.001,
        );
        if (Math.abs(video.currentTime - desired) > 0.085 && !video.seeking) {
          pauseMedia();
          pendingSeek[active!] = desired;
          seekVideo(active!);
        }
      }
      const ready =
        voiceReady &&
        music!.readyState >= 3 &&
        !music!.seeking &&
        (!video ||
          (video.readyState >= (heldAtEnd[active!] ? 2 : 3) &&
            !video.seeking &&
            pendingSeek[active!] === null));
      if (!ready) {
        pauseMedia();
        setWaiting(true);
        return;
      }
      if (starting) return;
      if (!music!.paused && (!voice || voice.playing) && (!video || heldAtEnd[active!] || !video.paused)) {
        setWaiting(false);
        reportPlaying(true);
        return;
      }
      const attempt = ++generation;
      starting = true;
      void (async () => {
        try {
          if (video && !heldAtEnd[active!]) await video.play();
          if (destroyed || !wanted || attempt !== generation) return;
          await voice?.play();
          if (destroyed || !wanted || attempt !== generation) return;
          await music!.play();
          if (destroyed || !wanted || attempt !== generation) return;
          setWaiting(false);
          reportPlaying(!music!.paused && (!video || heldAtEnd[active!] || !video.paused));
        } catch (reason) {
          if (destroyed || !wanted || attempt !== generation) return;
          if (
            !(reason instanceof DOMException && reason.name === "AbortError")
          ) {
            fail("Playback could not start. Press Play to try again.");
          }
        } finally {
          if (attempt === generation) starting = false;
        }
      })();
    }
    function seek(time: number) {
      generation += 1;
      starting = false;
      pauseMedia();
      targetTime = clamp(time, start, end);
      try {
        music!.currentTime = targetTime;
      } catch {
        /* Set again after metadata loads. */
      }
      prepare(targetTime, true);
      voice?.prepare(targetTime, true);
      report(targetTime, true);
      if (targetTime >= end) stop();
      else setWaiting(wanted);
      synchronize();
    }
    transport.current = {
      toggle() {
        if (suspendedRef.current) return;
        if (wanted) {
          stop();
          return;
        }
        setError("");
        voice?.retry();
        voice?.activate();
        if (!trackId) return;
        if (music!.error) music!.load();
        if (active !== null && sourceVideos[active]?.error) {
          const deck = assignments[active]!;
          assign(
            active,
            deck.slot,
            deck.clip,
            deck.clip.source_start + targetTime - deck.slot.start,
          );
        }
        wanted = true;
        setPlaying(true);
        if (targetTime >= end - 0.02) seek(start);
        else synchronize();
      },
      pause() {
        stop();
        report(clamp(music!.currentTime, start, end), true);
      },
      seek,
      audioReady() {
        if (
          music!.readyState >= 1 &&
          (music!.currentTime < start ||
            music!.currentTime > end ||
            (Math.abs(music!.currentTime - targetTime) > 0.1 && !wanted))
        ) {
          music!.currentTime = targetTime;
        }
        synchronize();
      },
      videoReady(index) {
        const video = sourceVideos[index];
        const deck = assignments[index];
        if (
          video &&
          deck &&
          Number.isFinite(video.duration) &&
          deck.clip.source_end > video.duration + 0.05 &&
          index === active
        ) {
          fail(
            "This clip extends beyond the available source. Choose another suggestion or shorten it.",
          );
          return;
        }
        if (video && deck && video.videoWidth && video.videoHeight) {
          const ratio = video.videoWidth / video.videoHeight;
          if (deck.ratio !== ratio) {
            assignments[index] = { ...deck, ratio };
            setDecks([...assignments]);
          }
        }
        seekVideo(index);
        synchronize();
      },
      waiting(index) {
        if (!wanted || (index !== undefined && index !== active)) return;
        pauseMedia();
        setWaiting(true);
      },
      error(index) {
        if (index !== undefined && index !== active) return;
        fail(
          index === undefined
            ? "The music could not be played. Check the uploaded track and try again."
            : "This source could not be played. Choose another suggestion or check that the original film is available.",
        );
      },
    };
    pauseMedia();
    try {
      music.currentTime = targetTime;
    } catch {
      /* Wait for loadedmetadata. */
    }
    prepare(targetTime, true);
    report(targetTime, true);
    // Chromium may throttle animation frames for an occluded window without
    // hiding its document. A media-clock timer keeps cuts independent of paint.
    const clockTimer = window.setInterval(synchronize, 16);
    // Background tabs throttle timers too, so stop when the user leaves this
    // tab instead of letting an unsupervised preview drift past its cuts.
    const onVisibility = () => {
      if (window.document.hidden && wanted) {
        stop();
        report(clamp(music.currentTime, start, end), true);
      }
    };
    window.document.addEventListener("visibilitychange", onVisibility);
    return () => {
      destroyed = true;
      generation += 1;
      window.clearInterval(clockTimer);
      window.document.removeEventListener("visibilitychange", onVisibility);
      pauseMedia();
      voice?.dispose();
      showDeck(null);
      transport.current = null;
    };
    // Snapshot editorial state without restarting for audio-clock renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trackId, start, end, planKey]);

  useEffect(() => {
    if (suspended) transport.current?.pause();
  }, [suspended]);

  useEffect(() => {
    function handleSpace(event: KeyboardEvent) {
      if (
        !trackId ||
        suspendedRef.current ||
        window.document.querySelector("dialog[open]") ||
        !isPlaybackSpace(event)
      )
        return;
      event.preventDefault();
      // Holding Space should neither toggle repeatedly nor scroll the page.
      if (!event.repeat) transport.current?.toggle();
    }
    window.addEventListener("keydown", handleSpace);
    return () => window.removeEventListener("keydown", handleSpace);
  }, [trackId]);

  const slot = slotAt(position, slots, end);
  const clip = slot?.clip_id
    ? document.clips.find((item) => item.id === slot.clip_id)
    : null;
  const gapPrompt = slot ? directionOf(document, slot).query : "";
  const filmTitle = clip ? filmTitles[clip.film_id]?.trim() || "Film title unavailable" : null;
  const ratio = document.aspect_ratio === "9:16" ? 9 / 16 : 16 / 9;

  function seekManually(value: number) {
    transport.current?.seek(value);
    const selected = slotAt(value, slots, end);
    if (selected) latest.current.onSelectSlot?.(selected.id);
  }

  function seekCut(direction: -1 | 1) {
    const margin = 0.5 / document.fps;
    const cuts = [...new Set([start, ...slots.map((item) => item.start), end])]
      .filter((time) => time >= start && time <= end)
      .sort((a, b) => a - b);
    const target =
      direction < 0
        ? cuts.filter((time) => time < position - margin).at(-1) ?? start
        : cuts.find((time) => time > position + margin) ?? end;
    seekManually(target);
  }

  function timecode(time: number) {
    const fps = Math.max(1, Math.round(document.fps));
    const frames = Math.max(0, Math.round(time * fps));
    return [
      Math.floor(frames / (fps * 60)),
      Math.floor(frames / fps) % 60,
      frames % fps,
    ]
      .map((value) => String(value).padStart(2, "0"))
      .join(":");
  }

  const transportDisabled = !trackId || suspended;

  return (
    <section className={styles.player} aria-label="Music sequence preview">
      <div className={styles.heading}>
        <div className={styles.previewTitle}>
          <span>Preview</span>
          {filmTitle && <strong title={filmTitle} aria-label={`Current film: ${filmTitle}`}>{filmTitle}</strong>}
        </div>
        <span>
          {!!document.effects?.length && (onShowExport
            ? <button type="button" className={styles.effectsNote} onClick={onShowExport}
              title="This preview plays the cuts. The edit's render effects show in its latest export.">Effects play in the export</button>
            : <span className={styles.effectsNote}
              title="This preview plays the cuts. Export the video to see the edit's render effects.">Effects show in an export</span>)}
          {slot
            ? `${slots.indexOf(slot) + 1} / ${slots.length}`
            : experimentName("music-sketch")}
        </span>
      </div>
      <div className={styles.monitor}>
        <div
          className={styles.frame}
          style={
            { "--preview-ratio": ratio, aspectRatio: ratio } as CSSProperties
          }
        >
          {[0, 1].map((index) => {
            const deck = decks[index];
            const crop = deck?.clip.crop || { x: 0, y: 0, width: 1, height: 1 };
            const croppedRatio =
              ((deck?.ratio || 16 / 9) * crop.width) / crop.height;
            const width =
              croppedRatio >= ratio ? 100 : (croppedRatio / ratio) * 100;
            const height =
              croppedRatio >= ratio ? (ratio / croppedRatio) * 100 : 100;
            return (
              <div
                key={index}
                className={styles.cropViewport}
                aria-hidden={activeDeck !== index}
                style={{
                  width: `${width}%`,
                  height: `${height}%`,
                  visibility: activeDeck === index ? "visible" : "hidden",
                }}
              >
                <video
                  ref={(element) => {
                    videos.current[index] = element;
                  }}
                  muted
                  playsInline
                  preload="auto"
                  aria-label={
                    activeDeck === index
                      ? "Sequence video"
                      : "Next scene preload"
                  }
                  style={{
                    width: `${100 / crop.width}%`,
                    height: `${100 / crop.height}%`,
                    left: `${(-crop.x / crop.width) * 100}%`,
                    top: `${(-crop.y / crop.height) * 100}%`,
                  }}
                  onLoadedMetadata={() => transport.current?.videoReady(index)}
                  onCanPlay={() => transport.current?.videoReady(index)}
                  onSeeked={() => transport.current?.videoReady(index)}
                  onWaiting={() => transport.current?.waiting(index)}
                  onError={() => transport.current?.error(index)}
                />
              </div>
            );
          })}
          {!clip && (
            <div className={styles.gap}>
              <span aria-hidden="true">{slot ? "+" : "♪"}</span>
              <strong>
                {slot
                  ? "A scene goes here"
                  : document.track
                    ? "Your music, ready to play"
                    : "Choose your music"}
              </strong>
              <p>
                {gapPrompt ||
                  (slot
                    ? "Fill this space with a suggestion, or choose a scene."
                    : "The sequence will appear here as you add scenes.")}
              </p>
            </div>
          )}
          {buffering && (
            <div className={styles.buffering} role="status">
              <i aria-hidden="true" />
              Preparing playback…
            </div>
          )}
        </div>
      </div>
      <audio
        key={trackId || "no-track"}
        ref={audio}
        src={
          trackId
            ? mediaUrl(`/lab/tracks/${encodeURIComponent(trackId)}/audio`)
            : undefined
        }
        preload="auto"
        muted={muted}
        onLoadedMetadata={() => transport.current?.audioReady()}
        onCanPlay={() => transport.current?.audioReady()}
        onSeeked={() => transport.current?.audioReady()}
        onWaiting={() => transport.current?.waiting()}
        onEnded={() => transport.current?.seek(end)}
        onError={() => {
          if (trackId) transport.current?.error();
        }}
      />
      {(document.dialogue_clips ?? []).slice(0, 32).map((clip) => <audio key={clip.id} ref={dialogueRef(clip.id)}
        data-dialogue-id={clip.id} aria-label="Dialogue audio" crossOrigin="anonymous" preload="auto" muted={muted} />)}
      <div
        className={`${styles.transport} ${compactTransport ? styles.compactTransport : ""}`}
        role="group"
        aria-label="Preview playback controls"
      >
        <div className={styles.playbackButtons}>
          <button
            type="button"
            disabled={transportDisabled || position <= start}
            onClick={() => seekManually(start)}
            aria-label="Go to start"
            title="Go to start"
          >
            <EditorIcon name="start" />
          </button>
          <button
            type="button"
            disabled={transportDisabled || position <= start}
            onClick={() => seekCut(-1)}
            aria-label="Previous cut"
            title="Previous cut"
          >
            <EditorIcon name="previous" />
          </button>
          <button
            type="button"
            className={styles.play}
            disabled={transportDisabled}
            onClick={() => transport.current?.toggle()}
            title={playing ? "Pause (Space)" : "Play (Space)"}
            aria-keyshortcuts="Space"
            aria-label={playing ? "Pause preview" : "Play preview"}
          >
            <EditorIcon name={playing ? "pause" : "play"} size={18} />
          </button>
          <button
            type="button"
            disabled={transportDisabled || position >= end}
            onClick={() => seekCut(1)}
            aria-label="Next cut"
            title="Next cut"
          >
            <EditorIcon name="next" />
          </button>
          <button
            type="button"
            disabled={transportDisabled || position >= end}
            onClick={() => seekManually(end)}
            aria-label="Go to end"
            title="Go to end"
          >
            <EditorIcon name="end" />
          </button>
        </div>
        {!compactTransport && (
          <input
            type="range"
            aria-label="Preview playhead"
            min={start}
            max={end}
            step={1 / document.fps}
            value={clamp(position, start, end)}
            disabled={transportDisabled}
            onChange={(event) => seekManually(Number(event.target.value))}
          />
        )}
        <span
          className={styles.time}
          title={`Minutes : seconds : frames · ${document.fps} fps`}
          aria-label={`${seconds(position - start)} of ${seconds(end - start)}`}
        >
          {timecode(position - start)}
          <span>/ {timecode(end - start)}</span>
        </span>
        <button
          type="button"
          className={styles.mute}
          disabled={transportDisabled}
          aria-label={muted ? "Unmute preview" : "Mute preview"}
          title={muted ? "Unmute preview" : "Mute preview"}
          aria-pressed={muted}
          onClick={() => setMuted((value) => !value)}
        >
          <EditorIcon name={muted ? "muted" : "volume"} />
        </button>
      </div>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      <p className={styles.caption}>
        {clip?.title || (slot ? "Unfilled scene" : document.track?.name || "")}
      </p>
    </section>
  );
}
