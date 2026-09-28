/** One Web Audio source per media element; editorial changes reuse its graph. */
let context: AudioContext | null = null;
const graphs = new WeakMap<HTMLAudioElement, { source: MediaElementAudioSourceNode; gain: GainNode; connected: boolean }>();
let connected = 0;

export function activateDialogueAudio(audio: HTMLAudioElement): Promise<void> {
  if (typeof AudioContext === "undefined") return Promise.resolve();
  let graph = graphs.get(audio);
  if (!graph) {
    context ??= new AudioContext();
    const source = context.createMediaElementSource(audio), gain = context.createGain();
    graph = { source, gain, connected: false }; graphs.set(audio, graph);
  }
  if (!graph.connected) {
    graph.source.connect(graph.gain); graph.gain.connect(context!.destination);
    graph.connected = true; connected += 1;
  }
  // Called directly from Play/Space, before any source or video await.
  return context!.resume();
}

export function setDialogueGain(audio: HTMLAudioElement, amplitude: number) {
  const graph = graphs.get(audio);
  if (graph) {
    audio.volume = 1;
    graph.gain.gain.value = audio.muted ? 0 : amplitude;
    return true;
  }
  audio.volume = Math.min(1, amplitude);
  return amplitude <= 1;
}

export function releaseDialogueAudio(audio: HTMLAudioElement) {
  const graph = graphs.get(audio);
  if (!graph?.connected) return;
  graph.source.disconnect();
  graph.gain.disconnect();
  graph.connected = false; connected -= 1;
  if (!connected) void context!.suspend().catch(() => {});
  // Keep the node association: Strict Mode may reuse this exact DOM element.
  // A second createMediaElementSource for it is forbidden, even after close().
}
