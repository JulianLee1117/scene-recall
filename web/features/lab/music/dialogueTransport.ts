import { mediaUrl } from "@/lib/lab";
import type { LabDialogueClip } from "@/types/lab";
import { dialogueAt, dialogueVolume } from "./dialogueAudio";
import { activateDialogueAudio, setDialogueGain } from "./dialogueGain";

const owners = new WeakMap<HTMLAudioElement, symbol>();

export async function resolveDialogueSource(clip: LabDialogueClip, _signal: AbortSignal): Promise<string> {
  const query = new URLSearchParams({ film_id: clip.film_id, source_start: String(clip.source_start),
    source_end: String(clip.source_end), source_audio_mode: clip.source_audio_mode ?? "original" });
  return mediaUrl(`/lab/dialogue-audio?${query}`);
}

/** An independent source deck follows the song clock, never the current B-roll. */
export function createDialogueTransport(
  audio: HTMLAudioElement,
  clips: LabDialogueClip[],
  resolve = resolveDialogueSource,
) {
  const owner = Symbol("dialogue-deck"); owners.set(audio, owner);
  let active: LabDialogueClip | null = null, controller: AbortController | null = null, audible = false;
  let loaded = false, disposed = false, requestedPlay = false, needsSeek = true, heldAtEnd = false;
  let failure = "", activationFailure = "", epoch = 0;
  let activated = false, activationReady = false, activation = 0;
  const urls = new Map<string, string>();
  const pause = () => { requestedPlay = false; audio.pause(); };
  function select(clip: LabDialogueClip | null) {
    pause(); controller?.abort(); controller = null;
    epoch += 1; active = clip; loaded = false; needsSeek = true; heldAtEnd = false; failure = "";
    if (!clip || disposed) return;
    const attempt = epoch;
    controller = new AbortController();
    const signal = controller.signal;
    void (async () => {
      try {
        const key = JSON.stringify([clip.film_id, clip.source_start, clip.source_end, clip.source_audio_mode ?? "original"]);
        const url = urls.get(key) ?? await resolve(clip, signal);
        if (disposed || signal.aborted || attempt !== epoch) return;
        urls.set(key, url);
        audio.src = url; audio.load(); loaded = true;
      } catch (error) {
        if (!disposed && !signal.aborted && attempt === epoch)
          failure = `Dialogue audio could not be loaded. ${error instanceof Error ? error.message : "Check that the film is available."}`;
      }
    })();
  }
  return {
    prepare(time: number, force = false) {
      if (disposed) return false;
      const current = dialogueAt(clips, time);
      audible = !!current;
      const clip = current ?? clips.find((item) => item.start > time && item.start - time <= 3) ?? null;
      if (clip?.id !== active?.id) select(clip);
      if (activationFailure) return false;
      if (!clip) return true;
      if (!audible) { pause(); setDialogueGain(audio, 0); return true; }
      if (activated && !activationReady) return false;
      const supportedGain = setDialogueGain(audio, dialogueVolume(clip, time));
      if (activated && !supportedGain) {
        failure = "This browser cannot amplify dialogue audio. Use a browser with Web Audio support or lower the voice to 0 dB.";
        return false;
      }
      if (!loaded) return false;
      if (audio.error) { failure = "Dialogue audio could not be played. Its prepared source may be unavailable; try again or choose another source."; return false; }
      if (audio.readyState < 1) return false;
      const duration = clip.source_end - clip.source_start;
      if (Number.isFinite(audio.duration) && duration > audio.duration + 0.05) {
        failure = "Prepared dialogue audio is shorter than the selected source. Shorten its source out or try again."; return false;
      }
      const target = time - clip.start;
      // A source can reach its last sample just ahead of the master clock.
      // Holding silence avoids play() restarting an ended film from zero.
      const mediaEnd = Number.isFinite(audio.duration) ? Math.min(duration, audio.duration) : duration;
      if (!force && !needsSeek && (audio.ended || audio.currentTime >= mediaEnd) && Math.abs(audio.currentTime - target) <= .085) {
        heldAtEnd = true; pause(); setDialogueGain(audio, 0); return true;
      }
      if (force || Math.abs(audio.currentTime - target) > .085) heldAtEnd = false;
      if (heldAtEnd) { setDialogueGain(audio, 0); return true; }
      if (!audio.seeking && (force || needsSeek || Math.abs(audio.currentTime - target) > 0.085)) {
        pause();
        try { audio.currentTime = target; needsSeek = false; } catch { return false; }
      }
      return !needsSeek && !audio.seeking && audio.readyState >= 3;
    },
    get error() { return activationFailure || (audible ? failure : ""); },
    get playing() { return !audible || heldAtEnd || !audio.paused; },
    activate() {
      if (!clips.length) return;
      activated = true;
      activationReady = false;
      activationFailure = "";
      const attempt = ++activation;
      try {
        void activateDialogueAudio(audio).then(() => {
          if (!disposed && attempt === activation) activationReady = true;
        }).catch(() => {
          if (!disposed && attempt === activation) activationFailure = "Dialogue audio could not start. Press Play again to enable audio.";
        });
      } catch {
        activationFailure = "Dialogue audio could not start. Press Play again to enable audio.";
      }
    },
    async play() {
      if (!audible || !active || heldAtEnd || !loaded || disposed || activated && !activationReady) return;
      requestedPlay = true;
      await audio.play();
      // A pending browser play promise must not restart a stopped/removed voice.
      if (owners.get(audio) === owner && (!requestedPlay || disposed)) audio.pause();
    },
    pause,
    retry() { if (failure || audio.error) select(active); },
    dispose() { disposed = true; pause(); controller?.abort(); epoch += 1; },
  };
}

/** One independent deck per retained voice permits bridges and overlapping lines. */
export function createDialoguePool(
  elements: Map<string, HTMLAudioElement>,
  clips: LabDialogueClip[],
  resolve = resolveDialogueSource,
) {
  const decks = clips.slice(0, 32).flatMap((clip) => {
    const audio = elements.get(clip.id);
    return audio ? [createDialogueTransport(audio, [clip], resolve)] : [];
  });
  return {
    prepare(time: number, force = false) {
      let ready = true;
      for (const deck of decks) if (!deck.prepare(time, force)) ready = false;
      return ready;
    },
    get error() { return decks.find((deck) => deck.error)?.error ?? ""; },
    get playing() { return decks.every((deck) => deck.playing); },
    activate() { decks.forEach((deck) => deck.activate()); },
    play: async () => { await Promise.all(decks.map((deck) => deck.play())); },
    pause() { decks.forEach((deck) => deck.pause()); },
    retry() { decks.forEach((deck) => deck.retry()); },
    dispose() { decks.forEach((deck) => deck.dispose()); },
  };
}
