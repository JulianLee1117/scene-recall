const paths = {
  "arrow-left": "M19 12H5m6-6-6 6 6 6",
  "arrow-right": "M5 12h14m-6-6 6 6-6 6",
  "arrow-up": "M12 19V5m-6 6 6-6 6 6",
  "arrow-down": "M12 5v14m-6-6 6 6 6-6",
  "arrow-up-right": "M6 18 18 6M6 6h12v12",
  "chevron-left": "m15 6-6 6 6 6",
  "chevron-right": "m9 6 6 6-6 6",
  "chevron-down": "m6 9 6 6 6-6",
} as const;

/** Decorative directions: each enclosing control supplies its accessible name. */
export default function DirectionIcon({
  name,
  size = 16,
  className,
}: {
  name: keyof typeof paths;
  size?: number;
  className?: string;
}) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      style={{ display: "inline-block", flexShrink: 0, verticalAlign: "middle" }}
    >
      <path d={paths[name]} />
    </svg>
  );
}
