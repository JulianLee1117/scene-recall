"use client";

import { useEffect, useState } from "react";
import { buildGuide } from "./guide";
import { buildModelGroups } from "./models";
import { FOOTAGE_LADDER, GLOSSARY } from "./glossary";
import type { GlossaryGroup, ProjectInfo } from "./types";
import styles from "./info.module.css";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

const TOPICS = [
  { id: "ingestion", title: "Ingestion", description: "Prepare the library", outcome: "Searchable shots, frames and dialogue" },
  { id: "search", title: "Search", description: "Find a moment", outcome: "Ranked moments linked to original footage" },
  { id: "editing", title: "Editing", description: "Build an edit", outcome: "An editable timeline and playable export" },
  { id: "storage", title: "Storage & services", description: "Keep sources and work", outcome: "Preserved sources and recoverable projects" },
  { id: "glossary", title: "Glossary", description: "Learn the vocabulary", outcome: "One shared set of words for the whole system" },
  { id: "models", title: "Models", description: "Current versions", outcome: "The model stack behind the app" },
] as const;

function filterGlossary(query: string): GlossaryGroup[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return GLOSSARY;
  return GLOSSARY
    .map((group) => ({ ...group, terms: group.terms.filter((item) => `${item.term} ${item.definition}`.toLowerCase().includes(needle)) }))
    .filter((group) => group.terms.length > 0);
}

function GlossaryArticle({ query, onQuery }: { query: string; onQuery: (value: string) => void }) {
  const groups = filterGlossary(query);
  const count = groups.reduce((total, group) => total + group.terms.length, 0);
  const filtering = query.trim().length > 0;
  return <article id="info-topic" className={styles.article} aria-labelledby="info-topic-heading">
    <header className={styles.topicHeader}>
      <div>
        <h2 id="info-topic-heading">Glossary</h2>
        <p className={styles.topicSummary}>The words this project uses, and what each one means here.</p>
      </div>
      <p className={styles.outcome}><span>What you get</span>One shared set of words for the whole system</p>
    </header>

    <figure className={styles.overview}>
      <figcaption>How footage is divided</figcaption>
      <ol className={styles.flow} aria-label="Footage hierarchy diagram">
        {FOOTAGE_LADDER.map((name, index) => <li key={name}>
          <span className={styles.flowMarker} aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
          <span>{name}</span>
        </li>)}
      </ol>
    </figure>

    <p className={styles.introduction}>Search, ingestion and editing share one vocabulary. Several everyday film words carry a narrower meaning here, most of all <strong>scene</strong>, <strong>unit</strong> and <strong>moment</strong>, so it is worth reading those first.</p>

    <div className={styles.glossaryTools}>
      <label className={styles.filter}>
        <span>Filter terms</span>
        <input type="search" value={query} placeholder="Try keyframe, fusion or lock" onChange={(event) => onQuery(event.target.value)} />
      </label>
      {filtering
        ? <p className={styles.filterCount} aria-live="polite">{count} {count === 1 ? "term" : "terms"}</p>
        : <nav className={styles.chips} aria-label="Glossary groups">
          {GLOSSARY.map((group) => <a key={group.id} href={`#info-glossary-${group.id}`}>{group.title}</a>)}
        </nav>}
    </div>

    {groups.map((group) => <section id={`info-glossary-${group.id}`} className={styles.termGroup} key={group.id} aria-labelledby={`info-glossary-heading-${group.id}`}>
      <header className={styles.termGroupHeader}>
        <h3 id={`info-glossary-heading-${group.id}`}>{group.title}</h3>
        <p>{group.summary}</p>
      </header>
      <dl className={styles.terms}>
        {group.terms.map((item) => <div className={styles.termRow} key={item.term}>
          <dt>{item.term}</dt>
          <dd>{item.definition}</dd>
        </div>)}
      </dl>
    </section>)}
    {count === 0 && <p className={styles.noTerms} aria-live="polite">No terms match “{query.trim()}”. Try a shorter word.</p>}
  </article>;
}

export default function InfoView() {
  const [settings, setSettings] = useState<ProjectInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [revision, setRevision] = useState(0);
  const [topicId, setTopicId] = useState<(typeof TOPICS)[number]["id"]>("ingestion");
  const [glossaryQuery, setGlossaryQuery] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(false);
    void fetch(`${API_URL}/project/info`, { signal: controller.signal, cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error("Settings unavailable");
        const value: ProjectInfo = await response.json();
        if (value.schema_version !== 1 || !value.models || !value.retrieval || !value.lab || !value.thresholds || !value.ingest) {
          throw new Error("Unsupported settings response");
        }
        if (!controller.signal.aborted) setSettings(value);
      })
      .catch(() => {
        if (!controller.signal.aborted) { setSettings(null); setError(true); }
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [revision]);

  const sections = buildGuide(settings);
  const isGlossary = topicId === "glossary";
  const section = sections.find((item) => item.id === topicId);
  const topic = TOPICS.find((item) => item.id === topicId)!;
  const isStorage = topicId === "storage";
  const DiagramList = isStorage ? "ul" : "ol";
  const modelGroups = buildModelGroups(settings);

  return <section className={styles.page} aria-labelledby="info-heading">
    <header className={styles.header}>
      <span className={styles.eyebrow}>INSIDE SCENE RECALL</span>
      <h1 id="info-heading">How Scene Recall works</h1>
      <p>From a film file to a finished edit, one part at a time, in plain language, with the models behind each.</p>
    </header>

    <nav className={styles.topics} aria-label="Guide topics">
      {TOPICS.map((item, index) => <button
        key={item.id}
        type="button"
        aria-current={topicId === item.id ? "page" : undefined}
        aria-controls="info-topic"
        onClick={() => setTopicId(item.id)}
      >
        <span className={styles.topicNumber} aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
        <span><strong>{item.title}</strong><span>{item.description}</span></span>
      </button>)}
    </nav>

    {topicId === "models" ? <article id="info-topic" className={styles.article} key="models" aria-labelledby="info-topic-heading">
      <header className={styles.topicHeader}>
        <div>
          <h2 id="info-topic-heading">Current models</h2>
          <p className={styles.topicSummary}>The model stack behind search, film evidence and editing.</p>
        </div>
      </header>
      <section className={styles.modelSnapshot} aria-label="Model versions" aria-busy={loading}>
        <div className={styles.modelSnapshotHeading}>
          <span>{loading ? "Reading API settings…" : error ? "API settings unavailable · implementation defaults shown" : "API settings + implementation defaults"}</span>
        </div>
        <div className={styles.modelGroups}>
          {modelGroups.map((group) => <div key={group.title}>
            <h3>{group.title}</h3>
            <ul className={styles.modelList}>
              {group.models.map((model) => <li key={model.role}><span>{model.role}</span><strong>{model.name}</strong></li>)}
            </ul>
          </div>)}
        </div>
        <p>Names reflect loaded API settings or implementation defaults, not model readiness. Film evidence keeps its own versions.</p>
      </section>
    </article> : isGlossary || !section ? <GlossaryArticle key="glossary" query={glossaryQuery} onQuery={setGlossaryQuery} /> : <article id="info-topic" className={styles.article} key={section.id} aria-labelledby="info-topic-heading">
      <header className={styles.topicHeader}>
        <div>
          <h2 id="info-topic-heading">{topic.title}</h2>
          <p className={styles.topicSummary}>{section.summary}</p>
        </div>
        <p className={styles.outcome}><span>{isStorage ? "Designed to preserve" : "What you get"}</span>{topic.outcome}</p>
      </header>

      <figure className={styles.overview}>
        <figcaption>{isStorage ? "Three independent layers" : "The process at a glance"}</figcaption>
        <DiagramList className={`${styles.flow} ${isStorage ? styles.layers : ""}`} aria-label={`${section.title} ${isStorage ? "ownership" : "process"} diagram`}>
          {section.steps.map((step, index) => <li key={step.id}>
            <span className={styles.flowMarker} aria-hidden="true">{isStorage ? "◇" : String(index + 1).padStart(2, "0")}</span>
            <span>{step.title}</span>
          </li>)}
        </DiagramList>
      </figure>

      <p className={styles.introduction}>{section.introduction}</p>

      <div className={styles.steps}>
        {section.steps.map((step, index) => <section id={`info-step-${step.id}`} className={styles.step} key={step.id} aria-labelledby={`info-step-heading-${step.id}`}>
          <div className={styles.stepMarker} aria-hidden="true">{isStorage ? "◇" : String(index + 1).padStart(2, "0")}</div>
          <div className={styles.stepContent}>
            <header className={styles.stepHeader}>
              <h3 id={`info-step-heading-${step.id}`}>{step.title}</h3>
              <p>{step.summary}</p>
            </header>
            {step.channels && <figure className={styles.branchDiagram} aria-label="Broad text search: parallel evidence channels merge through rank fusion">
              <figcaption>Broad text search · parallel evidence channels</figcaption>
              <ul>{step.channels.map((channel) => <li key={channel.title}><strong>{channel.title}</strong><span>{channel.description}</span></li>)}</ul>
              <div className={styles.mergeLine} aria-hidden="true" />
              <div className={styles.mergeLabel}>Weighted reciprocal-rank fusion</div>
            </figure>}
            <div className={styles.explanation}>{step.detail.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}</div>
            <aside className={styles.technical} aria-label={`${step.title} technical details`}>
              <dl className={styles.facts}>
                {step.model && <div className={styles.modelFact}><dt>Model</dt><dd>{step.model}</dd></div>}
                <div><dt>Method</dt><dd>{step.method}</dd></div>
                <div><dt>Output</dt><dd>{step.output}</dd></div>
              </dl>
            </aside>
          </div>
        </section>)}
      </div>

      <aside className={styles.note}>
        <span className={styles.noteIcon} aria-hidden="true">i</span>
        <div><h3>Worth understanding</h3><p>{section.note}</p></div>
      </aside>

      <section className={styles.references} aria-labelledby="info-references-heading">
        <h3 id="info-references-heading">In the code</h3>
        <p>The implementation behind each part of this topic.</p>
        <dl>{section.steps.map((step) => <div key={step.id}>
          <dt>{step.title}</dt>
          <dd>{step.sources.map((source) => <code key={source}>{source}</code>)}</dd>
        </div>)}</dl>
      </section>
    </article>}

    <footer className={styles.footer}>
      <div className={styles.settingsStatus}>
        <p role="status">{loading ? "Reading current model settings…" : error
          ? "Live settings unavailable. The architecture guide is still available."
          : "Model names and values reflect the API’s loaded configuration."}</p>
        <button type="button" disabled={loading} onClick={() => setRevision((value) => value + 1)}>{loading ? "Loading…" : "Refresh settings"}</button>
      </div>
      <p>Configuration is not per-film coverage. Derived data keeps its own model and version records.</p>
      <p>Maintained with <code>docs/search-architecture.md</code> and <code>README.md</code>. Reading this guide starts no jobs or model calls.</p>
    </footer>
  </section>;
}
