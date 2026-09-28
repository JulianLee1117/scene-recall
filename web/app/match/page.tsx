import { Suspense } from "react";
import MatchSearch from "@/features/matching/MatchSearch";

export default function Page() {
  return <Suspense fallback={<main style={{ padding: 32 }}>Opening match cuts…</main>}><MatchSearch /></Suspense>;
}
