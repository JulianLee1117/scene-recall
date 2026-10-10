"use client";

import { useEffect, useId, useMemo, useState } from "react";
import { matchRequest, type Placement } from "@/lib/matchCuts";
import { visionShapes, visionSource, type MomentVision, type VisionLayer } from "@/lib/matchVision";
import styles from "./matchCuts.module.css";

const cache = new Map<string, MomentVision>();

/**
 * What the match index saw at the analysed instant nearest *time*, while
 * Vision is on. Scrubbing settles before a request; a cached instant shows at
 * once, and nothing shows until the instant asked for has arrived.
 */
export function useVision(unitId: string | null, time: number | null, enabled: boolean): MomentVision | null {
  const key = enabled && unitId && time !== null ? `${unitId}@${time.toFixed(2)}` : null;
  const [loaded, setLoaded] = useState<{ key: string; vision: MomentVision } | null>(null);
  useEffect(() => {
    if (!key || !unitId || time === null || cache.has(key)) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      matchRequest<MomentVision>(`/vision?unit_id=${encodeURIComponent(unitId)}&time=${time.toFixed(3)}`, { signal: controller.signal })
        .then((vision) => { cache.set(key, vision); setLoaded({ key, vision }); })
        .catch(() => { /* not in the index yet, or superseded: no overlay */ });
    }, 120);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [key, unitId, time]);
  if (!key) return null;
  return cache.get(key) ?? (loaded?.key === key ? loaded.vision : null);
}

/**
 * The layers drawn over a frame, at the frame's own placement (so crops and
 * formats line up). Shapes scale with the picture; labels stay a readable size.
 */
export function VisionOverlay({ vision, visible, place, tone = "in" }: {
  vision: MomentVision;
  visible: ReadonlySet<string>;
  place: Placement;
  /** "out" draws the outgoing frame's layers in white over the incoming frame. */
  tone?: "in" | "out";
}) {
  const arrow = useId();
  const { width, height, shapes } = useMemo(() => visionShapes(vision, visible), [vision, visible]);
  return <div className={styles.vision} data-tone={tone} aria-hidden="true"
    style={{ left: `${place.left}%`, top: `${place.top}%`, width: `${place.width}%`, height: `${place.height}%` }}>
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
    <defs>
      <marker id={arrow} viewBox="0 0 6 6" refX="5" refY="3" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
        <path d="M0 0 6 3 0 6z" fill="currentColor" />
      </marker>
    </defs>
    {shapes.map((shape, index) => {
      switch (shape.kind) {
        case "cell": {
          // The outgoing frame's light or colour sits inset in the incoming frame's
          // cells, so where they agree and where they differ shows at a glance.
          const inset = tone === "out" && shape.area ? 0.3 : 0;
          return <rect key={index} data-layer={shape.layer} data-area={shape.area || undefined}
            x={shape.x + shape.w * inset} y={shape.y + shape.h * inset} width={shape.w * (1 - 2 * inset)} height={shape.h * (1 - 2 * inset)}
            fill={shape.fill} fillOpacity={inset ? 1 : shape.opacity} />;
        }
        case "box":
          return <rect key={index} className={styles.visionBox} x={shape.x} y={shape.y} width={shape.w} height={shape.h} stroke={shape.tint} />;
        case "label":
          return null;
        case "line":
          return <line key={index} className={styles.visionLine} data-layer={shape.layer} x1={shape.x1} y1={shape.y1} x2={shape.x2} y2={shape.y2}
            strokeOpacity={shape.strength === undefined ? undefined : 0.35 + 0.55 * Math.min(1, shape.strength)}
            markerEnd={shape.arrow ? `url(#${arrow})` : undefined} />;
        case "dot":
          return <circle key={index} className={shape.ring ? styles.visionRing : styles.visionDot} cx={shape.x} cy={shape.y} r={shape.r} />;
      }
    })}
    </svg>
    {shapes.map((shape, index) => shape.kind === "label" &&
      <span key={index} className={styles.visionLabel} style={{ left: `${shape.x / width * 100}%`, top: `${shape.y / height * 100}%`, color: shape.tint }}>
        {shape.text}
      </span>)}
  </div>;
}

/**
 * Why a cut matched, drawn: one reason's layers on the incoming frame, and the
 * outgoing frame's (dashed) where it is overlaid.
 */
export function CutVision({ incoming, outgoing, reason, place, over }: {
  incoming: MomentVision | null;
  outgoing: MomentVision | null;
  reason: { layers?: string[] } | null;
  place: Placement;
  over: Placement | null;
}) {
  const visible = useMemo(() => new Set(reason?.layers ?? []), [reason]);
  if (!visible.size) return null;
  return <>
    {incoming && <VisionOverlay vision={incoming} visible={visible} place={place} />}
    {outgoing && over && <VisionOverlay vision={outgoing} visible={visible} place={over} tone="out" />}
  </>;
}

/** Vision on or off: one remembered switch, shown wherever a frame can draw it. */
export function VisionSwitch({ on, onToggle, hint }: { on: boolean; onToggle: (on: boolean) => void; hint?: string }) {
  return <label className={styles.check}>
    <input type="checkbox" checked={on} onChange={(event) => onToggle(event.target.checked)} />
    <span>Vision{hint && <small>{hint}</small>}</span>
  </label>;
}

/** The Vision switch, its layer chips and where the layers came from. */
export function VisionControls({ on, onToggle, layers, visible, onVisibleChange, vision }: {
  on: boolean;
  onToggle: (on: boolean) => void;
  layers: ReadonlyArray<Pick<VisionLayer, "key" | "label">>;
  visible: ReadonlySet<string>;
  onVisibleChange: (visible: ReadonlySet<string>) => void;
  vision: MomentVision | null;
}) {
  const toggle = (key: string) => {
    const next = new Set(visible);
    if (next.has(key)) next.delete(key); else next.add(key);
    onVisibleChange(next);
  };
  return <div className={styles.visionControls}>
    <VisionSwitch on={on} onToggle={onToggle} hint="What the matcher sees at this instant" />
    {on && <div className={styles.chips} aria-label="Vision layers">{layers.map((layer) =>
      <button key={layer.key} aria-pressed={visible.has(layer.key)} onClick={() => toggle(layer.key)}>{layer.label}</button>)}</div>}
    {on && vision && <p className={styles.visionSource} title="The models and producer version behind these layers">
      {vision.usable ? "" : "Not a usable cut point · "}{visionSource(vision)}</p>}
  </div>;
}
