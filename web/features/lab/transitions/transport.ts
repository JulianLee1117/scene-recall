/** Wait for the requested media position to have a decoded frame before starting a shared clock. */
export function waitForMedia(media: HTMLMediaElement, timeoutMs = 4000, signal?: AbortSignal, minimumReadyState = 2): Promise<void> {
  return new Promise((resolve, reject) => {
    let timer: ReturnType<typeof setTimeout>;
    const events = ["seeked", "loadeddata", "canplay"];
    const cleanup = () => { clearTimeout(timer); events.forEach((name) => media.removeEventListener(name, check)); media.removeEventListener("error", fail); signal?.removeEventListener("abort", abort); };
    const check = () => { if (!media.seeking && media.readyState >= minimumReadyState) { cleanup(); resolve(); } };
    const fail = () => { cleanup(); reject(new Error("The preview is still loading. Try Play again after it loads.")); };
    const abort = () => { cleanup(); reject(new Error("Playback wait cancelled.")); };
    if (signal?.aborted) { abort(); return; }
    events.forEach((name) => media.addEventListener(name, check));
    media.addEventListener("error", fail);
    signal?.addEventListener("abort", abort, { once: true });
    timer = setTimeout(fail, timeoutMs);
    check();
  });
}
