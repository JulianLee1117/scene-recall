"use client";

import { useEffect, useState } from "react";
import { emptyNotebook, NOTEBOOK_KEY, parseNotebook, type Notebook } from "./notebook";

export default function useNotebook() {
  const [notebook, setNotebook] = useState<Notebook>(emptyNotebook);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    try { setNotebook(parseNotebook(window.localStorage.getItem(NOTEBOOK_KEY))); }
    catch { setError("Browser storage is unavailable. Notes stay in this open session."); }
    setReady(true);
  }, []);
  useEffect(() => {
    if (!ready) return;
    try { window.localStorage.setItem(NOTEBOOK_KEY, JSON.stringify(notebook)); }
    catch { setError("Browser storage is full or unavailable. Download recipes to keep a copy."); }
  }, [notebook, ready]);
  return { notebook, setNotebook, ready, error };
}
