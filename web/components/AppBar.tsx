"use client";

import Link from "next/link";
import styles from "./appBar.module.css";

export type AppTab = "search" | "saved" | "films" | "info" | "lab";

/** The app's places, in the order the bar shows them. Lab sits apart, at the end. */
export const APP_TABS: ReadonlyArray<{ id: AppTab; label: string; href: string }> = [
  { id: "search", label: "Search", href: "/" },
  { id: "saved", label: "Saved", href: "/?tab=saved" },
  { id: "films", label: "Films", href: "/?tab=films" },
  { id: "info", label: "Info", href: "/?tab=info" },
  { id: "lab", label: "Lab", href: "/lab" },
];

/**
 * One bar over every page. On the home page the four views switch in place
 * through `onSelect` (Lab stays a link, and is reported just before it
 * navigates); everywhere else each place is a link. A screen with unsaved
 * work passes `onNavigate` and decides itself when to leave.
 */
export default function AppBar({ active, onSelect, onNavigate }: {
  active: AppTab;
  onSelect?: (tab: AppTab) => void;
  onNavigate?: (href: string) => void;
}) {
  return (
    <nav className={styles.bar} aria-label="Scene Recall">
      {APP_TABS.map((tab) => {
        const current = tab.id === active;
        const className = [styles.tab, tab.id === "lab" ? styles.lab : "", current ? styles.current : ""].filter(Boolean).join(" ");
        if (onSelect && tab.id !== "lab") return (
          <button key={tab.id} type="button" className={className} aria-current={current ? "page" : undefined}
            onClick={() => onSelect(tab.id)}>{tab.label}</button>
        );
        return (
          <Link key={tab.id} href={tab.href} className={className} aria-current={current ? "page" : undefined}
            onClick={(event) => {
              onSelect?.(tab.id);
              if (onNavigate) { event.preventDefault(); onNavigate(tab.href); }
            }}>{tab.label}</Link>
        );
      })}
    </nav>
  );
}
