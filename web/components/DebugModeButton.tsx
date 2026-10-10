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
      <svg className={styles.debugFrame} viewBox="0 0 240 88" preserveAspectRatio="none" fill="none" shapeRendering="crispEdges" aria-hidden="true">
        <path className={styles.frameLine} d="M18 7h204v4h7v7h4v52h-4v7h-7v4H18v-4h-7v-7H7V18h4v-7h7Z" stroke="currentColor" />
        <path className={styles.frameBrackets} d="M7 25V14h7V7h16M210 7h16v7h7v11M233 63v11h-7v7h-16M30 81H14v-7H7V63" stroke="currentColor" strokeWidth="2" />
        <path className={styles.sprockets} d="M12 30h3v5h-3Zm0 12h3v5h-3Zm0 12h3v5h-3ZM225 30h3v5h-3Zm0 12h3v5h-3Zm0 12h3v5h-3Z" fill="currentColor" />
        <g className={styles.chargeTop}>
          <path d="M0 7h8v2H0Z" fill="#bc6d3c" /><path d="M7 6h10v4H7Z" fill="#ffc873" /><path d="M16 5h6v6h-6Z" fill="#fff0c0" />
        </g>
        <g transform="translate(240 88) rotate(180)">
          <g className={styles.chargeBottom}>
            <path d="M0 7h8v2H0Z" fill="#39686b" /><path d="M7 6h10v4H7Z" fill="#76dacb" /><path d="M16 5h6v6h-6Z" fill="#d7fff0" />
          </g>
        </g>
        {/* The perimeter impact owns the full sequence, including its settle. */}
        <g className={styles.impact} data-debug-impact="true" onAnimationEnd={(event) => {
          if (event.target === event.currentTarget) finish();
        }}>
          <path className={styles.impactFrame} d="M18 7h204v4h7v7h4v52h-4v7h-7v4H18v-4h-7v-7H7V18h4v-7h7Z" stroke="#f9d693" strokeWidth="2" />
          <g className={styles.sparksTop}>
            <path d="M214 4h3v3h-3Zm11 10h4v3h-4Zm6 10h2v4h-2Z" fill="#ffdc91" />
            <path d="M226 3h2v3h-2Zm10 12h2v2h-2Z" fill="#9cf4da" />
          </g>
          <g className={styles.sparksBottom}>
            <path d="M23 81h3v3h-3Zm-12-10h4v3h-4Zm-4-11h2v4H7Z" fill="#9cf4da" />
            <path d="M12 82h2v3h-2ZM2 74h2v2H2Z" fill="#ffdc91" />
          </g>
        </g>
      </svg>
      <span className={styles.debugCopy}>
        <svg className={styles.debugCrest} width="18" height="18" viewBox="0 0 20 20" fill="none" shapeRendering="crispEdges" aria-hidden="true">
          <path d="M6 1h8v2h3v3h2v8h-2v3h-3v2H6v-2H3v-3H1V6h2V3h3Z" fill="currentColor" opacity=".25" />
          <path d="M7 2h6v3H7ZM3 6h4v4H3Zm10-1h4v5h-4ZM3 12h4v4H3Zm4 4h6v2H7Zm6-5h4v5h-4Z" fill="currentColor" />
          <path d="M8 7h4v1h1v4h-1v1H8v-1H7V8h1Z" fill="#f5ead2" />
        </svg>
        <span className={styles.debugTitle} aria-hidden="true">DEBUG MODE</span>
        <span id={`${id}-hint`} className={styles.debugHint}>Match scores &amp; descriptions</span>
      </span>
      <span className={styles.debugState} aria-hidden="true">{enabled ? "ON" : "OFF"}</span>
    </button>
  );
}
