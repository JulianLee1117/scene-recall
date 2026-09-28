"use client";

import { useEffect, useRef, useState } from "react";
import { matchJobActive, matchingRequest } from "@/lib/matching";
import type { MatchSearchJob, MatchSearchRequest, SceneMatch } from "@/types/matching";

type PendingPreview = { searchId: string; candidateId: string; job: MatchSearchJob };

export function useMatchSearch() {
  const [job, setJob] = useState<MatchSearchJob | null>(null);
  const [submitted, setSubmitted] = useState<MatchSearchRequest | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const [previewPollError, setPreviewPollError] = useState("");
  const [previews, setPreviews] = useState<Record<string, SceneMatch>>({});
  const [pendingPreview, setPendingPreview] = useState<PendingPreview | null>(null);
  const [preparing, setPreparing] = useState<string | null>(null);
  const [queuedPreview, setQueuedPreview] = useState<{ candidate: SceneMatch; force: boolean } | null>(null);
  const epoch = useRef(0);
  const liveJob = useRef(job);
  liveJob.current = job;

  useEffect(() => {
    if (!matchJobActive(job)) return;
    const controller = new AbortController();
    const generation = epoch.current;
    const timer = setTimeout(() => {
      matchingRequest<MatchSearchJob>(`/searches/${job!.id}`, { signal: controller.signal })
        .then((next) => { if (generation === epoch.current) { setJob(next); setPollError(""); if (next.error) setError(next.error); } })
        .catch((reason) => { if (!controller.signal.aborted && generation === epoch.current) { setPollError(reason.message); setJob((current) => current?.id === job!.id ? { ...current } : current); } });
    }, 1000);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [job]);

  useEffect(() => {
    if (!pendingPreview) return;
    if (!matchJobActive(pendingPreview.job)) {
      const candidate = pendingPreview.job.result?.candidate;
      if (pendingPreview.searchId === liveJob.current?.id) {
        if (pendingPreview.job.status === "completed" && candidate?.id === pendingPreview.candidateId && candidate.preview_ready)
          setPreviews((current) => ({ ...current, [candidate.id]: candidate }));
        else setError(pendingPreview.job.error ?? candidate?.preview_warning ?? "This cut could not be previewed. Try another result or retry.");
      }
      setPendingPreview(null);
      return;
    }
    const controller = new AbortController();
    const generation = epoch.current;
    const timer = setTimeout(() => {
      matchingRequest<MatchSearchJob>(`/searches/${pendingPreview.job.id}`, { signal: controller.signal })
        .then((next) => {
          if (generation === epoch.current && pendingPreview.searchId === liveJob.current?.id) {
            setPreviewPollError("");
            setPendingPreview({ ...pendingPreview, job: next });
          }
        })
        .catch((reason) => {
          if (!controller.signal.aborted && generation === epoch.current && pendingPreview.searchId === liveJob.current?.id) {
            setPreviewPollError(reason.message);
            setPendingPreview((current) => current?.job.id === pendingPreview.job.id ? { ...current } : current);
          }
        });
    }, 800);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [pendingPreview]);

  async function start(request: MatchSearchRequest) {
    const generation = ++epoch.current;
    const previous = liveJob.current;
    const previousPreview = pendingPreview?.job;
    setSending(true); setError(""); setPollError(""); setPreviewPollError(""); setPreviews({}); setPendingPreview(null); setPreparing(null); setQueuedPreview(null); setJob(null); setSubmitted(request);
    try {
      if (previousPreview && matchJobActive(previousPreview)) await matchingRequest(`/searches/${previousPreview.id}/cancel`, { method: "POST" });
      if (matchJobActive(previous)) await matchingRequest(`/searches/${previous!.id}/cancel`, { method: "POST" });
      if (generation !== epoch.current) return null;
      const next = await matchingRequest<MatchSearchJob>("/searches", { method: "POST", body: JSON.stringify(request) });
      if (generation === epoch.current) { setJob(next); if (next.error) setError(next.error); return next; }
      return null;
    } catch (reason) {
      if (generation === epoch.current) setError(reason instanceof Error ? reason.message : "Could not find match cuts.");
      return null;
    } finally { if (generation === epoch.current) setSending(false); }
  }

  async function restore(searchId: string): Promise<MatchSearchJob | null> {
    const generation = ++epoch.current;
    setSending(true); setError(""); setPollError(""); setPreviewPollError(""); setJob(null); setSubmitted(null); setPreviews({}); setPendingPreview(null); setPreparing(null); setQueuedPreview(null);
    try {
      const saved = await matchingRequest<MatchSearchJob>(`/searches/${encodeURIComponent(searchId)}`);
      if (generation !== epoch.current) return null;
      if (!saved.request || !saved.reference) throw new Error("This link does not contain a scene search. Choose a scene to start again.");
      setJob(saved); setSubmitted(saved.request);
      if (saved.error) setError(saved.error);
      return saved;
    } catch (reason) {
      if (generation === epoch.current) setError(reason instanceof Error ? reason.message : "This saved match search is unavailable.");
      return null;
    } finally { if (generation === epoch.current) setSending(false); }
  }

  async function cancel() {
    if (!job || !matchJobActive(job)) return;
    const id = job.id;
    try {
      await matchingRequest(`/searches/${id}/cancel`, { method: "POST" });
      const next = await matchingRequest<MatchSearchJob>(`/searches/${id}`);
      if (liveJob.current?.id === id) setJob(next);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not cancel matching."); }
  }

  async function prepare(candidate: SceneMatch, { force = false }: { force?: boolean } = {}) {
    if (!job || (!force && ((job.result?.candidates?.find((item) => item.id === candidate.id) ?? candidate).preview_ready || previews[candidate.id]?.preview_ready))) return;
    if (preparing || pendingPreview || matchJobActive(job)) { setQueuedPreview({ candidate, force }); return; }
    const searchId = job.id;
    const generation = epoch.current;
    setPreparing(candidate.id); setError(""); setPreviewPollError("");
    if (force) setPreviews((current) => ({ ...current, [candidate.id]: { ...candidate, preview_ready: false } }));
    try {
      const next = await matchingRequest<MatchSearchJob>(`/searches/${searchId}/candidates/${candidate.id}/preview`, { method: "POST" });
      if (generation === epoch.current && liveJob.current?.id === searchId) setPendingPreview({ searchId, candidateId: candidate.id, job: next });
      else if (matchJobActive(next)) await matchingRequest(`/searches/${next.id}/cancel`, { method: "POST" });
    } catch (reason) { if (liveJob.current?.id === searchId) setError(reason instanceof Error ? reason.message : "Could not prepare this cut."); }
    finally { if (liveJob.current?.id === searchId) setPreparing(null); }
  }

  useEffect(() => {
    if (!queuedPreview || preparing || pendingPreview || matchJobActive(job)) return;
    setQueuedPreview(null);
    void prepare(queuedPreview.candidate, { force: queuedPreview.force });
  }, [queuedPreview, preparing, pendingPreview, job]);

  function dismissPreview() { setQueuedPreview(null); }

  return { job, submitted, start, restore, cancel, prepare, dismissPreview, previews, error: error || pollError || previewPollError, busy: sending || matchJobActive(job), preparingId: preparing ?? pendingPreview?.candidateId ?? null };
}
