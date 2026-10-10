"use client";

import { useEffect, useId, useState } from "react";
import { formatTime } from "@/lib/format";
import { matchedWordsEvidence } from "@/lib/matchReasons";
import type { SearchResult } from "@/types/api";

interface DialogueLine {
  line_id: string;
  t_start: number;
  t_end: number;
  text: string;
  source: string | null;
}

interface ShotDialogueResponse {
  unit_id: string;
  film_id: string;
  status: "available" | "unavailable";
  lines: DialogueLine[];
  truncated: boolean;
}

interface Passage {
  id: string;
  text: string;
  start?: number;
  matched: boolean;
}

const normalized = (text: string) => text.toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();

/** Source dialogue stays attached to the retrieved shot, not the moving playhead. */
export default function ShotDialogue({ shot, onSeek, canSeek }: {
  shot: SearchResult;
  onSeek: (time: number) => void;
  canSeek: boolean;
}) {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "";
  const listId = useId();
  const identity = `${shot.film_id}:${shot.unit_id}:${shot.t_start}:${shot.t_end}`;
  const [loaded, setLoaded] = useState<{ identity: string; data?: ShotDialogueResponse; error?: boolean } | null>(null);
  const [expandedFor, setExpandedFor] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const current = loaded?.identity === identity ? loaded : null;
  const expanded = expandedFor === identity;
  const evidence = matchedWordsEvidence(shot);
  const spoken = evidence?.kind === "Spoken" ? evidence : null;

  useEffect(() => {
    const controller = new AbortController();
    void fetch(`${apiUrl}/library/shot/${encodeURIComponent(shot.unit_id)}/dialogue`, {
      signal: controller.signal, cache: "no-store",
    }).then(async (response) => {
      if (!response.ok) throw new Error("Dialogue unavailable");
      const data: ShotDialogueResponse = await response.json();
      if (data.unit_id !== shot.unit_id || data.film_id !== shot.film_id || !Array.isArray(data.lines)) {
        throw new Error("Dialogue does not belong to this shot");
      }
      if (!controller.signal.aborted) setLoaded({ identity, data });
    }).catch(() => {
      if (!controller.signal.aborted) setLoaded({ identity, error: true });
    });
    return () => controller.abort();
  }, [apiUrl, identity, shot.unit_id, shot.film_id, attempt]);

  const lines = (current?.data?.lines ?? []).filter((line) => line.text?.trim()
    && Number.isFinite(line.t_start) && line.t_start >= 0 && Number.isFinite(line.t_end) && line.t_end > line.t_start);
  const matchedText = spoken ? normalized(spoken.text) : "";
  const timedMatch = spoken && typeof spoken.t_start === "number" && typeof spoken.t_end === "number";
  const passages: Passage[] = lines.filter((line) => !timedMatch
    || line.t_end <= spoken.t_start! || line.t_start >= spoken.t_end!).map((line) => {
    const text = normalized(line.text);
    return { id: line.line_id, text: line.text, start: line.t_start,
      matched: Boolean(matchedText && text && (` ${matchedText} `.includes(` ${text} `) || ` ${text} `.includes(` ${matchedText} `))) };
  });
  // A quote can span a cut. Keep the whole matched passage and replace its
  // overlapping cues, so context never repeats the same words underneath it.
  if (spoken && (timedMatch || !passages.some((line) => line.matched))) {
    passages.push({ id: "matched", text: spoken.text, start: spoken.t_start, matched: true });
  }
  passages.sort((a, b) => (a.start ?? shot.t_start) - (b.start ?? shot.t_start));
  const matchedIndex = passages.findIndex((line) => line.matched);
  const first = Math.max(0, Math.min(matchedIndex - 1, passages.length - 3));
  const visible = expanded ? passages : passages.slice(first, first + 3);
  const canExpand = passages.length > 3 || passages.some((line) => line.text.length > 200);

  return <>
    {(passages.length > 0 || current?.error) && <div>
      <dt>Dialogue</dt>
      <dd>
        {passages.length > 0 && <ol id={listId} className={`modal-dialogue${expanded ? " is-expanded" : ""}`} aria-label="Dialogue in this result">
          {visible.map((line) => <li key={line.id} className={line.matched ? "is-match" : undefined}>
            {typeof line.start === "number" ? <button type="button" className="modal-dialogue-time"
              disabled={!canSeek} onClick={() => onSeek(line.start!)}
              aria-label={`Play dialogue at ${formatTime(line.start)}`}>{formatTime(line.start)}</button> : <span />}
            <span className={`modal-dialogue-text${line.text.length > 200 ? " is-long" : ""}`}>{line.matched ? <mark>{line.text}</mark> : line.text}</span>
          </li>)}
        </ol>}
        {canExpand && <button type="button" className="modal-dialogue-expand" aria-expanded={expanded} aria-controls={listId}
          onClick={() => setExpandedFor(expanded ? null : identity)}>{expanded ? "Show less" : "Show all dialogue"}</button>}
        {expanded && current?.data?.truncated && <p className="modal-dialogue-note">Showing the first 200 lines in this shot.</p>}
        {current?.error && <p className="modal-dialogue-note">Dialogue could not be loaded. <button type="button"
          className="modal-dialogue-expand" onClick={() => { setLoaded(null); setAttempt((value) => value + 1); }}>Retry</button></p>}
      </dd>
    </div>}
    {evidence?.kind === "On screen" && <div><dt>On screen</dt><dd><p className="modal-story-action">“{evidence.text}”</p></dd></div>}
  </>;
}
