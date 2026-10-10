"use client";

import type { ReactNode } from "react";
import { mediaUrl, OUTPUT_ASPECT, placement, type Crop, type OutputFormat, type Placement } from "@/lib/matchCuts";
import styles from "./matchCuts.module.css";

/**
 * A still of the content picture seen through a crop, in the output's shape
 * (letterboxed when needed). Children drawn over it can take the picture's
 * placement (and the overlay's), so they line up with whatever is shown.
 */
export function FramedImage({ src, crop, aspect, output, alt, overlay, overlayOpacity = 0.5, children, eager }: {
  src: string;
  crop: Crop | null;
  aspect: number;
  output: OutputFormat;
  alt: string;
  overlay?: { src: string; crop: Crop | null; aspect: number } | null;
  overlayOpacity?: number;
  children?: ReactNode | ((place: Placement, over: Placement | null) => ReactNode);
  eager?: boolean;
}) {
  const outputAspect = OUTPUT_ASPECT[output];
  const place = placement(crop, aspect, null, outputAspect);
  const over = overlay ? placement(overlay.crop, overlay.aspect, null, outputAspect) : null;
  return <div className={styles.framed} data-output={output} style={{ aspectRatio: String(outputAspect) }}>
    <img src={mediaUrl(src)} alt={alt} loading={eager ? "eager" : "lazy"} decoding="async" draggable={false}
      style={{ left: `${place.left}%`, top: `${place.top}%`, width: `${place.width}%`, height: `${place.height}%` }} />
    {overlay && over && <img className={styles.onion} src={mediaUrl(overlay.src)} alt="" aria-hidden="true" draggable={false}
      style={{ left: `${over.left}%`, top: `${over.top}%`, width: `${over.width}%`, height: `${over.height}%`, opacity: overlayOpacity }} />}
    {typeof children === "function" ? children(place, over) : children}
  </div>;
}
