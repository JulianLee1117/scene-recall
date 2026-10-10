"use client";

import { useEffect, useState } from "react";
import { mediaUrl } from "@/lib/lab";
import type { LabDocument } from "@/types/lab";
import { audioPeaks, WAVEFORM_DECODE_SAMPLE_RATE, WAVEFORM_MAX_SECONDS } from "./audioWaveform";

const cache = new Map<string, number[]>();

export function useAudioWaveform(track: LabDocument["track"]) {
  const [peaks, setPeaks] = useState<number[]>([]);
  const [status, setStatus] = useState("");
  useEffect(() => {
    setPeaks([]);
    if (!track) {
      setStatus("");
      return;
    }
    const saved = cache.get(track.id);
    if (saved) {
      setPeaks(saved);
      setStatus("");
      return;
    }
    if (track.duration > WAVEFORM_MAX_SECONDS) {
      setStatus(
        "Waveform unavailable for tracks over 15 minutes. Playback and timing remain available.",
      );
      return;
    }
    const controller = new AbortController();
    let context: AudioContext | null = null;
    setStatus("Reading waveform…");
    async function decode() {
      try {
        // A native audio range response may be cached without CORS headers.
        // Decode a fresh complete response; successful peaks are cached above.
        const response = await fetch(
          mediaUrl(`/lab/tracks/${track!.id}/audio`),
          { signal: controller.signal, cache: "no-store" },
        );
        if (!response.ok) throw new Error("Audio unavailable");
        const reader = response.body?.getReader();
        const limit = 32 * 1024 * 1024;
        if (!reader || Number(response.headers.get("Content-Length")) > limit) {
          await reader?.cancel();
          throw new Error(
            "Waveform unavailable for files over 32 MiB. Playback and timing remain available.",
          );
        }
        const chunks: Uint8Array[] = [];
        let size = 0;
        while (true) {
          const part = await reader.read();
          if (part.done) break;
          size += part.value.byteLength;
          if (size > limit) {
            await reader.cancel();
            throw new Error(
              "Waveform unavailable for files over 32 MiB. Playback and timing remain available.",
            );
          }
          chunks.push(part.value);
        }
        if (controller.signal.aborted) return;
        const data = new Uint8Array(size);
        let offset = 0;
        for (const chunk of chunks) {
          data.set(chunk, offset);
          offset += chunk.byteLength;
        }
        // Decode at a bounded rate; retain only compact peaks after this effect.
        context = new AudioContext({ sampleRate: WAVEFORM_DECODE_SAMPLE_RATE });
        const buffer = await context.decodeAudioData(data.buffer);
        if (controller.signal.aborted) return;
        const values = audioPeaks(buffer);
        cache.set(track!.id, values);
        if (cache.size > 8) cache.delete(cache.keys().next().value!);
        setPeaks(values);
        setStatus("");
      } catch (error) {
        if (!controller.signal.aborted)
          setStatus(
            error instanceof Error && error.message.startsWith("Waveform")
              ? error.message
              : "Waveform unavailable. Playback and timing remain available.",
          );
      } finally {
        void context?.close();
      }
    }
    void decode();
    return () => controller.abort();
  }, [track?.id, track?.duration]);
  return { peaks, status };
}
