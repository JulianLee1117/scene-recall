import type { ReactNode } from "react";
import DirectionIcon from "@/components/DirectionIcon";

const paths = {
  play: <path d="m9 5 11 7-11 7Z" fill="currentColor" stroke="none" />,
  pause: <path d="M8 5v14M16 5v14" strokeWidth="3" />,
  start: <><path d="M5 5v14" /><path d="m18 6-8 6 8 6Z" /></>,
  end: <><path d="M19 5v14" /><path d="m6 6 8 6-8 6Z" /></>,
  previous: <><path d="m15 6-6 6 6 6M5 6v12" /></>,
  next: <><path d="m9 6 6 6-6 6M19 6v12" /></>,
  volume: <><path d="m11 5-5 4H3v6h3l5 4ZM15 8a6 6 0 0 1 0 8M18 5a10 10 0 0 1 0 14" /></>,
  muted: <><path d="m11 5-5 4H3v6h3l5 4ZM16 9l6 6M22 9l-6 6" /></>,
  scissors: <><circle cx="6" cy="6" r="3" /><circle cx="6" cy="18" r="3" /><path d="m8.5 8.5 12 12M8.5 15.5l12-12" /></>,
  magnet: <><path d="M5 4v9a7 7 0 0 0 14 0V4h-5v9a2 2 0 0 1-4 0V4ZM5 9h5M14 9h5" /></>,
  beats: <path d="M3 12h3l3-7 5 14 4-9 2 2h1" />,
  minus: <path d="M5 12h14" />,
  plus: <path d="M12 5v14M5 12h14" />,
  fit: <><path d="M4 5v14M20 5v14M8 12h8M10 9l-3 3 3 3M14 9l3 3-3 3" /></>,
  expand: <path d="M8 3H3v5M16 3h5v5M21 16v5h-5M8 21H3v-5" />,
  collapse: <path d="M3 8h5V3M21 8h-5V3M16 21v-5h5M8 21v-5H3" />,
  more: <><circle cx="5" cy="12" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="19" cy="12" r="1" /></>,
  close: <path d="m6 6 12 12M18 6 6 18" />,
  save: <><path d="M4 3h13l4 4v14H3V3ZM7 3v6h10V3M7 21v-8h10v8" /></>,
  trash: <><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7" /></>,
  search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></>,
  sparkles: <><path d="m12 3 2.3 6.7L21 12l-6.7 2.3L12 21l-2.3-6.7L3 12l6.7-2.3ZM20 2v4M18 4h4" /></>,
  settings: <><path d="M4 7h16M4 17h16M8 4v6M16 14v6" /><circle cx="8" cy="7" r="2" /><circle cx="16" cy="17" r="2" /></>,
  music: <><path d="M9 18V5l11-2v13M9 9l11-2" /><ellipse cx="6" cy="18" rx="3" ry="2.5" /><ellipse cx="17" cy="16" rx="3" ry="2.5" /></>,
  lock: <><rect x="5" y="10" width="14" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3" /></>,
  undo: <path d="m8 4-5 5 5 5M3 9h10a6 6 0 0 1 0 12" />,
  film: <><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M7 3v18M17 3v18M3 8h4M3 16h4M17 8h4M17 16h4M7 12h10" /></>,
} satisfies Record<string, ReactNode>;

export type EditorIconName = keyof typeof paths | "back";

/** Decorative icons: the enclosing control supplies its accessible name. */
export default function EditorIcon({
  name,
  size = 16,
}: {
  name: EditorIconName;
  size?: number;
}) {
  if (name === "back") return <DirectionIcon name="arrow-left" size={size} />;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.65"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {paths[name]}
    </svg>
  );
}
