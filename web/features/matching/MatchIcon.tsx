import DirectionIcon from "@/components/DirectionIcon";

export default function MatchIcon({ name }: { name: "search" | "play" | "adjust" | "close" | "chevron" | "back" }) {
  if (name === "chevron" || name === "back") {
    return <DirectionIcon name={name === "chevron" ? "chevron-down" : "arrow-left"} />;
  }
  const paths = {
    search: <><circle cx="7" cy="7" r="4.5" /><path d="m10.5 10.5 3 3" /></>,
    play: <path d="m5 3 8 5-8 5Z" />,
    adjust: <><path d="M3 4h10M3 8h10M3 12h10" /><path d="M6 2v4M10 6v4M6 10v4" /></>,
    close: <path d="m4 4 8 8M12 4l-8 8" />,
  };
  return <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
