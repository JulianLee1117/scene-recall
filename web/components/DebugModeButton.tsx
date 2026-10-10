"use client";

import { useEffect, useId, useRef, useState } from "react";
import styles from "./viewMenu.module.css";

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

/** A complete, non-restarting shot on activation. View owns the setting;
 * this mounted button owns only its short decorative animation. */
export default function DebugModeButton({ enabled, onChange }: {
  enabled: boolean;
  onChange: (enabled: boolean) => void;
}) {
  const id = useId();
  const [firing, setFiring] = useState(false);
  const firingRef = useRef(false);
  const finish = () => { firingRef.current = false; setFiring(false); };

  useEffect(() => {
    const motion = window.matchMedia(REDUCED_MOTION);
    const change = () => {
      if (motion.matches) { firingRef.current = false; setFiring(false); }
    };
    motion.addEventListener("change", change);
    return () => motion.removeEventListener("change", change);
  }, []);

  return (
    <button
      type="button"
      className={styles.debugButton}
      aria-label="Debug mode"
      aria-pressed={enabled}
      aria-describedby={`${id}-hint`}
      data-firing={firing || undefined}
      onClick={() => {
        if (!enabled && !firingRef.current && !window.matchMedia(REDUCED_MOTION).matches) {
          firingRef.current = true;
          setFiring(true);
        }
        onChange(!enabled);
      }}
    >
      <svg className={styles.cinema} width="96" height="44" viewBox="0 0 96 44" fill="none" shapeRendering="crispEdges" aria-hidden="true">
        <defs>
          {/* The tail is revealed from the lens, never drawn over the camera. */}
          <clipPath id={`${id}-beam`}><path d="M35 0h61v44H35Z" /></clipPath>
        </defs>
        <path className={styles.projection} d="M35 23h8v-2h10v-2h10v-2h7v18h-7v-2H53v-2H43v-2h-8Z" fill="#f0cd87" />
        <path d="M2 39h33v1H2Zm68 2h28v1H68Z" fill="#2b3936" />
        <g transform="translate(0 6)">
          <path d="M5 2h6v2h2v6h-2v2H5v-2H3V4h2Zm13 0h6v2h2v6h-2v2h-6v-2h-2V4h2Z" fill="#758d8b" />
          <path d="M5 2h6v2H5Zm13 0h6v2h-6Z" fill="#c3d7cb" />
          <path className={styles.reelHoles} d="M7 4h2v2H7Zm-2 3h2v2H5Zm4 1h2v2H9Zm11-4h2v2h-2Zm-2 3h2v2h-2Zm4 1h2v2h-2Z" fill="#182326" />
          <path d="M5 13h21v13H5Z" fill="#53696b" />
          <path d="M5 13h21v3H5Z" fill="#b0c6b9" />
          <path d="M5 24h21v2H5Z" fill="#304747" />
          <path d="M8 18h7v5H8Z" fill="#25383b" />
          <path d="M9 18h1v4H9Zm3 0h1v4h-1Z" fill="#829995" />
          <path d="M19 18h4v5h-4Z" fill="#d19a5e" />
          <path d="M20 18h3v2h-3Z" fill="#f7d99b" />
          <path d="M26 17h3v-2h4v10h-4v-2h-3Z" fill="#8da99e" />
          <path d="M29 15h4v3h-4Z" fill="#d8e7d4" />
          <path className={styles.lens} d="M33 17h2v6h-2Z" fill="#d9995b" />
          <path d="M13 26h5v3h5v2H8v-2h5Z" fill="#84999a" />
        </g>
        <path d="M79 36h4v4h4v1H75v-1h4Z" fill="#536861" />
        <path className={styles.screenFrame} d="M68 14h26v22H68Z" fill="#536861" />
        <path d="M70 16h22v18H70Z" fill="#111c1d" />
        <path d="M70 16h2v2h-2Zm20 0h2v2h-2Zm-20 16h2v2h-2Zm20 0h2v2h-2Z" fill="#3d5049" />
        <g className={styles.screenImage}>
          <path d="M72 18h18v14H72Z" fill="#233e44" />
          <path d="M85 19h3v3h-3Z" fill="#ffe1a0" />
          <path d="M72 27h2v-2h2v-2h2v2h2v3h4v-2h2v2h4v4H72Z" fill="#739c8b" />
          <path d="M72 30h11v-2h3v-2h2v3h2v3H72Z" fill="#456960" />
        </g>
        <g clipPath={`url(#${id}-beam)`}>
          <g className={styles.fireball}>
            <path d="M17 26h3v2h-3Zm5-2h5v3h-5Z" fill="#b35942" />
            <path d="M27 23h5v1h4v5h-4v1h-5v-2h-3v-2h3Z" fill="#ed864c" />
            <path d="M32 22h6v2h3v4h-3v2h-6v-2h-3v-4h3Z" fill="#ffc16a" />
            <path d="M35 24h4v4h-4Z" fill="#fff0b6" />
          </g>
          {/* This is the last animation to finish, so it releases the lock. */}
          <g className={styles.impact} data-debug-impact="true" onAnimationEnd={(event) => {
            if (event.target === event.currentTarget) finish();
          }}>
            <path d="M80 22h4v8h-4Zm-2 2h8v4h-8Z" fill="#fff0b6" />
            <path d="M75 18h2v2h-2Zm12 1h2v3h-2Zm-13 12h3v2h-3Zm12 3h2v2h-2Z" fill="#f1b06c" />
            <path d="M79 15h2v2h-2Zm11 14h2v2h-2Z" fill="#85d6bd" />
          </g>
        </g>
      </svg>
      <span className={styles.debugCopy}>
        <span className={styles.debugTitle} aria-hidden="true">DEBUG MODE</span>
        <span id={`${id}-hint`} className={styles.debugHint}>Match scores &amp; descriptions</span>
      </span>
      <span className={styles.debugState} aria-hidden="true">{enabled ? "ON" : "OFF"}</span>
    </button>
  );
}
