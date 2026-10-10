import type { LabDocument } from "@/types/lab";
import { seconds } from "@/lib/lab";
import { songMeaning } from "./songMeaning";
import EditorIcon from "@/features/lab/kit/EditorIcon";
import styles from "./musicWorkspace.module.css";

export default function MusicAnalysisDetails({ document, working, onAnalyze }: {
  document: LabDocument; working: boolean; onAnalyze: () => void;
}) {
  const meaning = songMeaning(document);
  return <div className={styles.musicAnalysis}>
    {meaning ? <>
      <strong>Song meaning</strong>
      <small>{meaning.vocal_status === "understood" ? "AI interpretation of heard vocals" :
        meaning.vocal_status === "partly_understood" ? "Vocals partly understood" :
        meaning.vocal_status === "unclear" ? "Vocal meaning unclear" : "No clear vocals"}</small>
      <p>{meaning.summary}</p>
      {meaning.themes.length > 0 && <small>{meaning.themes.join(" · ")}</small>}
      {meaning.uncertainty && <small>{meaning.uncertainty}</small>}
      {meaning.cues.length > 0 && <details>
        <summary>What the model heard</summary>
        {meaning.cues.map((cue, index) => <p key={index}><small>Song {seconds(cue.start)} – {seconds(cue.end)} · {cue.confidence} confidence</small><br />{cue.paraphrase}</p>)}
        <small>Paraphrases and approximate times, not verified lyrics.</small>
      </details>}
    </> : document.analysis && <small>This saved analysis has no separate vocal meaning. Listen again to check it.</small>}
    {typeof document.analysis?.summary === "string" ? <details open={!meaning}>
      <summary>Musical atmosphere</summary><p>{document.analysis.summary}</p>
    </details> : <p>Generate edit listens to this section automatically. You can also listen first and inspect the interpretation here.</p>}
    <button disabled={working} onClick={onAnalyze}><EditorIcon name="music" /> {document.analysis ? "Listen again" : "Analyze music"}</button>
    <small>To guide its interpretation, use Edit settings → Song notes & lyric cues. Beat guides are beside the timeline.</small>
  </div>;
}
