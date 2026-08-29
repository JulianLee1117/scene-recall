import type { RecipeMatchFacet } from "@/types/api";

interface FacetIconProps {
  facet: RecipeMatchFacet;
  size?: number;
}

export default function FacetIcon({ facet, size = 16 }: FacetIconProps) {
  const shared = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.65,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };

  if (facet === "scene") {
    return (
      <svg {...shared}>
        <rect x="3" y="5" width="18" height="14" rx="2" />
        <path d="m8 5 2-3M14 5l2-3M3 10h18" />
      </svg>
    );
  }

  if (facet === "words") {
    return (
      <svg {...shared}>
        <path d="M5 17.5A7.5 7.5 0 1 1 19 14l1 5-5-1a7.5 7.5 0 0 1-10-0.5Z" />
        <path d="M8 10h8M8 14h5" />
      </svg>
    );
  }

  if (facet === "look") {
    return (
      <svg {...shared}>
        <path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" />
        <circle cx="12" cy="12" r="2.5" />
      </svg>
    );
  }

  if (facet === "composition") {
    return (
      <svg {...shared}>
        <path d="M8 3H3v5M16 3h5v5M21 16v5h-5M8 21H3v-5" />
        <circle cx="12" cy="12" r="2.5" />
      </svg>
    );
  }

  return (
    <svg {...shared}>
      <path d="M12 3c1.4 3.2 3.2 5 6.5 6.5C15.2 11 13.4 12.8 12 16c-1.4-3.2-3.2-5-6.5-6.5C8.8 8 10.6 6.2 12 3Z" />
      <path d="M18.5 15.5c.6 1.4 1.4 2.2 2.8 2.8-1.4.6-2.2 1.4-2.8 2.8-.6-1.4-1.4-2.2-2.8-2.8 1.4-.6 2.2-1.4 2.8-2.8Z" />
    </svg>
  );
}
