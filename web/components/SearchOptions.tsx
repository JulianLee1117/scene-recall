"use client";

interface SearchOptionsProps {
  showDetails: boolean;
  onShowDetailsChange: (enabled: boolean) => void;
}

/** Shows each scene's description and, in plain words, why it matched. */
export default function SearchOptions({
  showDetails,
  onShowDetailsChange,
}: SearchOptionsProps) {
  return (
    <button
      type="button"
      className="search-options-trigger"
      aria-label={showDetails ? "Hide details" : "Show details"}
      aria-pressed={showDetails}
      title={showDetails ? "Hide descriptions" : "Show each scene's description and why it matched"}
      onClick={() => onShowDetailsChange(!showDetails)}
    >
      <svg
        width="13"
        height="13"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        aria-hidden="true"
      >
        <path d="M4 6h16M7 12h10M10 18h4" />
        <circle cx="9" cy="6" r="1.6" fill="currentColor" stroke="none" />
        <circle cx="15" cy="12" r="1.6" fill="currentColor" stroke="none" />
        <circle cx="12" cy="18" r="1.6" fill="currentColor" stroke="none" />
      </svg>
      <span>Details</span>
    </button>
  );
}
