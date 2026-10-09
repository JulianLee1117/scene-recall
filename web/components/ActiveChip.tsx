"use client";

/**
 * One active setting in the row under the toolbar ("Era 1990s, 2000s"):
 * the chip reopens its menu, its × clears it.
 */
export default function ActiveChip({
  label,
  value,
  onEdit,
  onClear,
}: {
  label: string;
  value: string;
  onEdit: () => void;
  onClear: () => void;
}) {
  return (
    <span className="active-chip">
      <button
        type="button"
        className="active-chip-edit"
        aria-label={`Edit ${label}: ${value}`}
        title={`${label}: ${value}`}
        onClick={onEdit}
      >
        <span className="clue-summary-label">{label}</span>
        <span className="clue-summary-value">{value}</span>
      </button>
      <button type="button" className="active-chip-clear" aria-label={`Clear ${label}`} title={`Clear ${label}`} onClick={onClear}>
        <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" aria-hidden="true">
          <path d="M6 6l12 12M18 6 6 18" />
        </svg>
      </button>
    </span>
  );
}
