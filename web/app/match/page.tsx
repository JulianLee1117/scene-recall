import { Suspense } from "react";
import MatchCuts from "@/features/lab/matching/MatchCuts";

export default function Page() {
  return <Suspense fallback={<main style={{ padding: 32 }}>Opening match cuts…</main>}><MatchCuts /></Suspense>;
}
