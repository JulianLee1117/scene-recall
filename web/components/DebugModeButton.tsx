"use client";

import { useEffect, useId, useRef, useState } from "react";
import { CinemaFrame, CinemaSlate } from "./DebugCinemaArt";
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
      <CinemaFrame id={id} />
      <svg className={styles.debugFrame} viewBox="0 0 240 104" preserveAspectRatio="none" fill="none" shapeRendering="crispEdges" aria-hidden="true">
        <g className={styles.chargeTop}>
          <path d="M0 5h12v2H0Z" fill="#916443" /><path d="M12 4h14v4H12Z" fill="#ffc873" /><path d="M24 3h5v6h-5Z" fill="#fff0c0" />
        </g>
        <g transform="translate(240 104) rotate(180)">
          <g className={styles.chargeBottom}>
            <path d="M0 5h12v2H0Z" fill="#39686b" /><path d="M12 4h14v4H12Z" fill="#76dacb" /><path d="M24 3h5v6h-5Z" fill="#d7fff0" />
          </g>
        </g>
        {/* The perimeter impact owns the full sequence, including its settle. */}
        <g className={styles.impact} data-debug-impact="true" onAnimationEnd={(event) => {
          if (event.target === event.currentTarget) finish();
        }}>
          <path className={styles.impactFrame} d="M12 4h216v4h8v88h-8v4H12v-4H4V8h8Z" stroke="#f9d693" strokeWidth="2" />
          <g className={styles.sparksTop}>
            <path d="M18 9h2V5h2v4h4v2h-4v4h-2v-4h-2Zm200 0h2V5h2v4h4v2h-4v4h-2v-4h-2Z" fill="#ffe1a3" />
            <path d="M7 21h3v3H7Zm23-17h2v2h-2Zm199 17h3v3h-3Zm-20-17h2v2h-2Z" fill="#a4f4df" />
          </g>
          <g className={styles.sparksBottom}>
            <path d="M18 94h2v-4h2v4h4v2h-4v4h-2v-4h-2Zm200 0h2v-4h2v4h4v2h-4v4h-2v-4h-2Z" fill="#a4f4df" />
            <path d="M7 80h3v3H7Zm23 20h2v2h-2Zm199-20h3v3h-3Zm-20 20h2v2h-2Z" fill="#ffe1a3" />
          </g>
        </g>
      </svg>
      <span className={styles.debugCopy}>
        <CinemaSlate />
        <span className={styles.debugTitle} aria-hidden="true">DEBUG MODE</span>
        <span id={`${id}-hint`} className={styles.debugHint}>Match scores &amp; descriptions</span>
      </span>
      <span className={styles.debugState} aria-hidden="true">{enabled ? "ON" : "OFF"}</span>
    </button>
  );
}
