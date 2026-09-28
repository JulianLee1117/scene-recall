"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { acquisitionRequest, messageOf } from "./api";
import { catalogChanged, isActive } from "./model";
import type { Acquisition, AcquisitionStatus } from "./types";

export function useAcquisitionQueue(onLibraryChange: () => Promise<unknown>) {
  const [items, setItems] = useState<Acquisition[]>([]);
  const [status, setStatus] = useState<AcquisitionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const alive = useRef(false);
  const previous = useRef<Acquisition[]>([]);
  const refreshController = useRef<AbortController | null>(null);
  const statusController = useRef<AbortController | null>(null);
  const mutationController = useRef<AbortController | null>(null);
  const catalogRevision = useRef(0);
  const syncedCatalogRevision = useRef(0);
  const catalogRefreshing = useRef(false);
  const libraryCallback = useRef(onLibraryChange);
  libraryCallback.current = onLibraryChange;

  const refreshLibrary = useCallback(function syncLibrary() {
    if (catalogRefreshing.current || syncedCatalogRevision.current === catalogRevision.current) return;
    catalogRefreshing.current = true;
    const revision = catalogRevision.current;
    let succeeded = false;
    void Promise.resolve().then(() => libraryCallback.current()).then(() => {
      if (!alive.current) return;
      succeeded = true;
      syncedCatalogRevision.current = revision;
      setLibraryError(null);
    }).catch(() => {
      if (alive.current) setLibraryError("The queue updated, but the library could not refresh. Try Refresh.");
    }).finally(() => {
      catalogRefreshing.current = false;
      // A new import may arrive while the previous library request is running.
      // Retry failures on the next queue refresh, without starting a tight loop.
      if (alive.current && succeeded && catalogRevision.current !== revision) syncLibrary();
    });
  }, []);

  const refreshStatus = useCallback(() => {
    // Health checks may wait on an unavailable download client. Keep one in
    // flight without delaying queue updates or restarting it on every poll.
    if (statusController.current) return;
    const controller = new AbortController();
    statusController.current = controller;
    void acquisitionRequest<AcquisitionStatus>("/status", { signal: controller.signal }).then((connection) => {
      if (!alive.current || controller.signal.aborted || statusController.current !== controller) return;
      setStatus(connection);
      setConnectionError(null);
    }).catch(() => {
      if (alive.current && !controller.signal.aborted && statusController.current === controller) {
        setConnectionError("Could not check download connections. Queue progress will keep updating.");
      }
    }).finally(() => {
      if (statusController.current === controller) statusController.current = null;
    });
  }, []);

  const refresh = useCallback(async () => {
    refreshController.current?.abort();
    const controller = new AbortController();
    refreshController.current = controller;
    const request = acquisitionRequest<{ items: Acquisition[] }>("", { signal: controller.signal });
    refreshStatus();
    try {
      const queue = await request;
      if (!alive.current || controller.signal.aborted || refreshController.current !== controller) return;
      if (catalogChanged(previous.current, queue.items)) catalogRevision.current += 1;
      previous.current = queue.items;
      setItems(queue.items);
      setRefreshError(null);
      refreshLibrary();
    } catch (problem) {
      if (alive.current && !controller.signal.aborted && refreshController.current === controller) setRefreshError(messageOf(problem));
    } finally {
      if (alive.current && !controller.signal.aborted && refreshController.current === controller) setLoading(false);
    }
  }, [refreshLibrary, refreshStatus]);

  useEffect(() => {
    alive.current = true;
    void refresh();
    const focus = () => { void refresh(); };
    window.addEventListener("focus", focus);
    return () => {
      alive.current = false;
      refreshController.current?.abort();
      statusController.current?.abort();
      statusController.current = null;
      mutationController.current?.abort();
      window.removeEventListener("focus", focus);
    };
  }, [refresh]);

  const active = items.some(isActive);
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      await refresh();
      if (!cancelled) timer = setTimeout(poll, 5000);
    };
    timer = setTimeout(poll, 5000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [active, refresh]);

  const execute = useCallback(async (key: string, path: string, body: object | FormData): Promise<boolean> => {
    if (mutationController.current) return false;
    const controller = new AbortController();
    mutationController.current = controller;
    setBusy(key);
    setMutationError(null);
    try {
      const response = await acquisitionRequest<{ item?: Acquisition }>(path, {
        method: "POST", body: body instanceof FormData ? body : JSON.stringify(body), signal: controller.signal,
      });
      if (!alive.current || controller.signal.aborted) return false;
      // A successful mutation is authoritative even if the follow-up GET fails.
      // In particular, do not offer another Cancel/Retry while cleanup is pending.
      if (response.item?.id) {
        refreshController.current?.abort();
        const item = response.item;
        const next = previous.current.some((old) => old.id === item.id)
          ? previous.current.map((old) => old.id === item.id ? item : old)
          : [...previous.current, item];
        if (catalogChanged(previous.current, next)) catalogRevision.current += 1;
        previous.current = next;
        setItems(next);
        refreshLibrary();
      }
      await refresh();
      return alive.current && !controller.signal.aborted;
    } catch (problem) {
      if (!controller.signal.aborted && alive.current) {
        setMutationError(messageOf(problem));
        // Revision conflicts refresh the authoritative queue before another choice.
        await refresh();
      }
      return false;
    } finally {
      if (mutationController.current === controller) {
        mutationController.current = null;
        if (alive.current) setBusy(null);
      }
    }
  }, [refresh, refreshLibrary]);

  const clearMutationError = useCallback(() => setMutationError(null), []);
  const error = mutationError ?? refreshError ?? libraryError ?? connectionError;
  return { items, status, loading, error, busy, refresh, execute, clearMutationError };
}
