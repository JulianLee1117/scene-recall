"use client";

import type { RankingPreset } from "@/types/api";

const PRESETS: ReadonlyArray<{ value: RankingPreset; label: string; title: string }> = [
  {
    value: "balanced",
    label: "Balanced",
    title: "Relevance first; iconic and well-made shots rise a little",
  },
  {
    value: "famous",
    label: "Famous",
    title: "Favour iconic, widely known moments among relevant shots",
  },
  {
    value: "gems",
    label: "Hidden gems",
    title: "Favour well-made shots people rarely see",
  },
];

interface RankingPresetControlProps {
  value: RankingPreset;
  onChange: (preset: RankingPreset) => void;
}

export default function RankingPresetControl({
  value,
  onChange,
}: RankingPresetControlProps) {
  return (
    <div className="ranking-preset" role="radiogroup" aria-label="Ranking">
      {PRESETS.map((preset) => (
        <button
          key={preset.value}
          type="button"
          role="radio"
          aria-checked={value === preset.value}
          className={value === preset.value ? "is-active" : undefined}
          title={preset.title}
          onClick={() => {
            if (preset.value !== value) onChange(preset.value);
          }}
        >
          {preset.label}
        </button>
      ))}
    </div>
  );
}
