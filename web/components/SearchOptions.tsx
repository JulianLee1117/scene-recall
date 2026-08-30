"use client";

interface SearchOptionsProps {
  showRankingDetails: boolean;
  onShowRankingDetailsChange: (enabled: boolean) => void;
}

export default function SearchOptions({
  showRankingDetails,
  onShowRankingDetailsChange,
}: SearchOptionsProps) {
  return (
    <button
      type="button"
      className="search-options-trigger"
      aria-label={
        showRankingDetails ? "Hide ranking details" : "Show ranking details"
      }
      aria-pressed={showRankingDetails}
      title={
        showRankingDetails ? "Hide ranking evidence" : "Show ranking evidence"
      }
      onClick={() => onShowRankingDetailsChange(!showRankingDetails)}
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
