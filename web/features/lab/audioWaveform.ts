/** Five-millisecond measured peaks retain attacks when a timeline is zoomed in. */
export const WAVEFORM_PEAKS_PER_SECOND = 200;
export const WAVEFORM_MAX_SECONDS = 900;
export const WAVEFORM_DECODE_SAMPLE_RATE = 16_000;

type DecodedAudio = Pick<AudioBuffer, "duration" | "length" | "numberOfChannels" | "getChannelData">;

export function audioPeaks(buffer: DecodedAudio): number[] {
  if (!buffer.length || !buffer.numberOfChannels || buffer.duration <= 0) return [];
  const count = Math.min(buffer.length, Math.ceil(Math.min(buffer.duration, WAVEFORM_MAX_SECONDS) * WAVEFORM_PEAKS_PER_SECOND));
  const peaks = new Array<number>(count).fill(0);
  for (let channel = 0; channel < buffer.numberOfChannels; channel++) {
    const samples = buffer.getChannelData(channel);
    for (let index = 0; index < count; index++) {
      const from = Math.floor(index * samples.length / count);
      const to = Math.floor((index + 1) * samples.length / count);
      let peak = peaks[index];
      // Examine every decoded sample: subsampling can miss the piano's attacks.
      for (let sample = from; sample < to; sample++) {
        peak = Math.max(peak, Math.abs(samples[sample]));
      }
      peaks[index] = Math.min(1, peak);
    }
  }
  return peaks;
}

/** Aggregate actual peaks over the visible interval, with at most one bar/pixel. */
export function waveformPath(peaks: readonly number[], trackDuration: number, start: number, end: number, width: number): string {
  if (!peaks.length || trackDuration <= 0 || end <= start || width <= 0) return "";
  const count = Math.min(4096, Math.ceil(width));
  return Array.from({ length: count }, (_, index) => {
    // Avoid duplicating attacks across aligned bins because of float rounding.
    const from = Math.max(0, Math.floor((start + (index / count) * (end - start)) / trackDuration * peaks.length + 1e-7));
    const to = Math.min(peaks.length, Math.ceil((start + ((index + 1) / count) * (end - start)) / trackDuration * peaks.length - 1e-7));
    let peak = 0;
    for (let sample = from; sample < to; sample++) peak = Math.max(peak, peaks[sample]);
    return `M${((index + .5) * width / count).toFixed(2)},${(36 - peak * 31).toFixed(2)}v${Math.max(1, peak * 62).toFixed(2)}`;
  }).join(" ");
}
