/**
 * A scene's look, read from its own picture: how much colour it has, which
 * hue, and how light it is. Enough to lay a board out as a gradient: the
 * colours around the wheel, then the greys from light to dark. Looks are
 * measured in the browser from the thumbnails the board already shows, and
 * remembered in local storage by scene.
 */
export interface SceneLook {
  /** Degrees around the wheel, red at 0, weighted by each pixel's colour. */
  hue: number;
  /** Mean colourfulness, 0 for black and white. */
  chroma: number;
  /** Mean lightness, 0 to 1. */
  lightness: number;
}

/** Below this a scene reads as black and white or near it. */
export const GREY_CHROMA = 0.06;
const SAMPLE = { width: 24, height: 14 };
const STORAGE_KEY = "scene-recall.looks";
const STORAGE_VERSION = 1;
const STORAGE_LIMIT = 2000;

/** The look of a picture from its RGBA bytes, any size. */
export function lookFromPixels(data: ArrayLike<number>): SceneLook {
  let x = 0;
  let y = 0;
  let weight = 0;
  let light = 0;
  let colour = 0;
  let count = 0;
  for (let i = 0; i + 3 < data.length; i += 4) {
    const r = data[i] / 255;
    const g = data[i + 1] / 255;
    const b = data[i + 2] / 255;
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    const chroma = max - min;
    if (chroma > 0) {
      const sector = max === r ? ((g - b) / chroma + 6) % 6 : max === g ? (b - r) / chroma + 2 : (r - g) / chroma + 4;
      const radians = (sector * Math.PI) / 3;
      x += Math.cos(radians) * chroma;
      y += Math.sin(radians) * chroma;
      weight += chroma;
    }
    light += (max + min) / 2;
    colour += chroma;
    count += 1;
  }
  if (!count) return { hue: 0, chroma: 0, lightness: 0 };
  const hue = weight > 0 ? ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360 : 0;
  return { hue, chroma: colour / count, lightness: light / count };
}

/** Reads a picture's look by drawing it small. Rejects when the picture cannot be read. */
export function measureLook(src: string): Promise<SceneLook> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    if (/^https?:/.test(src) && !src.startsWith(window.location.origin)) image.crossOrigin = "anonymous";
    image.decoding = "async";
    image.onload = () => {
      try {
        const canvas = document.createElement("canvas");
        canvas.width = SAMPLE.width;
        canvas.height = SAMPLE.height;
        const context = canvas.getContext("2d", { willReadFrequently: true });
        if (!context) throw new Error("no canvas");
        context.drawImage(image, 0, 0, SAMPLE.width, SAMPLE.height);
        resolve(lookFromPixels(context.getImageData(0, 0, SAMPLE.width, SAMPLE.height).data));
      } catch (error) {
        reject(error);
      }
    };
    image.onerror = () => reject(new Error(`could not load ${src}`));
    image.src = src;
  });
}

type Stored = { v: number; looks: Record<string, [number, number, number]> };

function readStore(): Stored {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "null");
    if (parsed && typeof parsed === "object" && (parsed as Stored).v === STORAGE_VERSION && typeof (parsed as Stored).looks === "object")
      return parsed as Stored;
  } catch {
    // Unreadable: start over.
  }
  return { v: STORAGE_VERSION, looks: {} };
}

/** Looks remembered in this browser, by scene. */
export function loadLooks(): Map<string, SceneLook> {
  const looks = new Map<string, SceneLook>();
  for (const [unitId, [hue, chroma, lightness]] of Object.entries(readStore().looks)) {
    if ([hue, chroma, lightness].every(Number.isFinite)) looks.set(unitId, { hue, chroma, lightness });
  }
  return looks;
}

/** Remembers looks, keeping the store bounded. */
export function saveLooks(looks: Map<string, SceneLook>): void {
  try {
    const store = readStore();
    for (const [unitId, look] of looks) {
      store.looks[unitId] = [Math.round(look.hue), Number(look.chroma.toFixed(3)), Number(look.lightness.toFixed(3))];
    }
    const ids = Object.keys(store.looks);
    if (ids.length > STORAGE_LIMIT) {
      for (const unitId of ids.slice(0, ids.length - STORAGE_LIMIT)) delete store.looks[unitId];
    }
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
  } catch {
    // Storage blocked: looks last for this visit only.
  }
}
