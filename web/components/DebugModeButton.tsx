"use client";

import { useEffect, useId, useRef, useState } from "react";
import { CinemaFrame } from "./DebugCinemaArt";
import styles from "./viewMenu.module.css";

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";
type Motion = "on" | "off";

/** View owns the setting. A click directs one short premiere or shutdown;
 * hover never starts it, and rapid clicks always follow the latest intent. */
export default function DebugModeButton({ enabled, onChange }: {
  enabled: boolean;
  onChange: (enabled: boolean) => void;
}) {
  const id = useId();
  const [motion, setMotion] = useState<Motion | null>(null);
  const motionRef = useRef<Motion | null>(null);
  const finish = (direction: Motion) => {
    if (motionRef.current === direction) { motionRef.current = null; setMotion(null); }
  };

  useEffect(() => {
    const preference = window.matchMedia(REDUCED_MOTION);
    const change = () => {
      if (preference.matches) { motionRef.current = null; setMotion(null); }
    };
    preference.addEventListener("change", change);
    return () => preference.removeEventListener("change", change);
  }, []);

  return (
    <button
      type="button"
      className={styles.debugButton}
      aria-label="Debug mode"
      aria-pressed={enabled}
      aria-describedby={`${id}-hint`}
      data-motion={motion || undefined}
      onClick={() => {
        const next = window.matchMedia(REDUCED_MOTION).matches ? null : enabled ? "off" : "on";
        motionRef.current = next;
        setMotion(next);
        onChange(!enabled);
      }}
    >
      <CinemaFrame id={id} />
      <svg className={styles.debugFrame} viewBox="0 0 240 88" preserveAspectRatio="none" fill="none" shapeRendering="crispEdges" aria-hidden="true">
        <g className={styles.chargeTop}>
          <path d="M0 5h12v2H0Z" fill="#976f3f" /><path d="M12 4h14v4H12Z" fill="#eac789" /><path d="M24 3h5v6h-5Z" fill="#fff4d8" />
        </g>
        <g transform="translate(240 88) rotate(180)">
          <g className={styles.chargeBottom}>
            <path d="M0 5h12v2H0Z" fill="#b47737" /><path d="M12 4h14v4H12Z" fill="#ffbf6f" /><path d="M24 3h5v6h-5Z" fill="#fff4cb" />
          </g>
        </g>
        {/* Each terminal outlasts its children. Stale/bubbling events cannot
            finish the opposite direction after a quick change of mind. */}
        <g className={styles.impact} data-debug-impact="on" onAnimationEnd={(event) => {
          if (event.target === event.currentTarget) finish("on");
        }}>
          <path className={styles.impactFrame} d="M12 4h216v4h8v72h-8v4H12v-4H4V8h8Z" stroke="#fff0ce" strokeWidth="2" />
          <g className={styles.sparksTop}>
            <path d="M18 9h2V5h2v4h4v2h-4v4h-2v-4h-2Zm200 0h2V5h2v4h4v2h-4v4h-2v-4h-2Z" fill="#ffe1a3" />
            <path d="M7 21h3v3H7Zm23-17h2v2h-2Zm199 17h3v3h-3Zm-20-17h2v2h-2Z" fill="#fff4d8" />
          </g>
          <g className={styles.sparksBottom}>
            <path d="M18 78h2v-4h2v4h4v2h-4v4h-2v-4h-2Zm200 0h2v-4h2v4h4v2h-4v4h-2v-4h-2Z" fill="#fff4d8" />
            <path d="M7 64h3v3H7Zm23 20h2v2h-2Zm199-20h3v3h-3Zm-20 20h2v2h-2Z" fill="#ffe1a3" />
          </g>
        </g>
        <g className={styles.shutdown} data-debug-impact="off" onAnimationEnd={(event) => {
          if (event.target === event.currentTarget) finish("off");
        }}>
          <g className={styles.shutdownRails}>
            <path d="M12 5h216v2H12ZM12 81h216v2H12Z" fill="#d5b379" />
            <path d="M42 6h156v1H42ZM42 81h156v1H42Z" fill="#fff1cf" />
          </g>
          <path className={styles.lastFrame} d="M117 5h6v2h-6Zm0 76h6v2h-6Z" fill="#ffd28a" />
        </g>
      </svg>
      <span className={styles.debugCopy}>
        <span className={styles.debugHero}>
          <span className={styles.debugTitle} aria-hidden="true">DEBUG MODE</span>
          <span className={styles.debugState} data-debug-state="true" aria-hidden="true">
            <span className={styles.stateLamp} />{enabled ? "ON" : "OFF"}
          </span>
        </span>
        <span id={`${id}-hint`} className={styles.debugHint}>Match scores &amp; descriptions</span>
      </span>
    </button>
  );
}
