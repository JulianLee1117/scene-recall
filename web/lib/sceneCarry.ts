/**
 * The scene a pointer drag carries: the picture itself, lifted off its card.
 * It rises from the card's own picture, shrinks to hand size around the point
 * that was pressed, follows the pointer opaque under a shadow, tucks in
 * smaller over a target that will take it (the target marks it `is-over`),
 * and at the end either collapses into what took it or flies back home.
 * Its size is the CSS variable `--carry-scale`; its states are classes.
 */
export const SCENE_CARRY_WIDTH = 168;
const UNFRAMED = { width: 96, height: 54 };
const TAKEN_MS = 220;
const RETURN_MS = 260;

export interface SceneCarry {
  move(x: number, y: number): void;
  /** `taken`: a target accepted the drop; `returned`: it goes back to its picture. */
  settle(outcome: "taken" | "returned"): void;
  /** Remove at once, with no animation. */
  dispose(): void;
}

const clamp01 = (value: number) => Math.min(1, Math.max(0, value));

export function liftScene({ picture, imageUrl, grab }: {
  /** The picture on the pressed element, if it has one; the carry starts from its frame. */
  picture: HTMLElement | null;
  /** The picture to show when the element has none of its own. */
  imageUrl?: string;
  /** Where the press landed: the point of the picture that stays under the pointer. */
  grab: { x: number; y: number };
}): SceneCarry {
  const frame = picture?.getBoundingClientRect();
  const framed = Boolean(frame && frame.width > 0 && frame.height > 0);
  const width = framed ? frame!.width : UNFRAMED.width;
  const height = framed ? frame!.height : UNFRAMED.height;
  const gx = framed ? clamp01((grab.x - frame!.left) / frame!.width) : 0.5;
  const gy = framed ? clamp01((grab.y - frame!.top) / frame!.height) : 0.5;
  const own = picture as Partial<HTMLImageElement> | null;
  const src = own?.currentSrc || own?.src || imageUrl;

  const element = document.createElement("div");
  element.className = "scene-carry";
  element.setAttribute("aria-hidden", "true");
  if (src) {
    const image = document.createElement("img");
    image.src = src;
    image.alt = "";
    image.draggable = false;
    element.append(image);
  }
  const style = element.style;
  style.width = `${width}px`;
  style.height = `${height}px`;
  style.transformOrigin = `${gx * 100}% ${gy * 100}%`;
  style.setProperty("--carry-scale", String(Math.min(1, SCENE_CARRY_WIDTH / width)));
  const place = (x: number, y: number) => {
    style.left = `${x - gx * width}px`;
    style.top = `${y - gy * height}px`;
  };
  place(grab.x, grab.y);
  document.body.append(element);
  // Laid out at the picture's own size first, so the shrink and the shadow animate from there.
  element.getBoundingClientRect();
  element.classList.add("is-up");

  let settled = false;
  const finish = (after: number) => {
    settled = true;
    window.setTimeout(() => element.remove(), after);
  };
  return {
    move(x, y) {
      if (!settled) place(x, y);
    },
    settle(outcome) {
      if (settled) return;
      if (outcome === "taken") {
        element.classList.add("is-taken");
        finish(TAKEN_MS);
        return;
      }
      const home = picture?.getBoundingClientRect();
      element.classList.remove("is-up", "is-over", "is-removing");
      element.classList.add("is-returning");
      if (home && home.width > 0) {
        style.left = `${home.left}px`;
        style.top = `${home.top}px`;
      }
      finish(RETURN_MS);
    },
    dispose() {
      settled = true;
      element.remove();
    },
  };
}
