export interface ReleaseMetadata {
  title: string;
  year: string;
  quality: string[];
}

/** Filename hints only: the user confirms identity before anything is queued. */
export function releaseMetadata(name: string): ReleaseMetadata {
  const normalized = name.replace(/\.(?:torrent|mkv|mp4|avi|mov)$/i, "").replace(/[._]+/g, " ").trim();
  const years = [...normalized.matchAll(/\b(?:18|19|20|21)\d{2}\b/g)].filter((match) => (
    (match.index ?? 0) > 0 && Number(match[0]) >= 1888 && Number(match[0]) <= 2100
  ));
  // These short source tokens can also be titles (for example, Cam), so only
  // read them from the release details after a year, never from title words.
  const releaseDetails = years.length ? normalized.slice((years[years.length - 1].index ?? 0) + 4) : "";
  const quality: string[] = [];
  const resolution = normalized.match(/\b(4320p|2160p|1080[pi]|720p|576p|480p)\b/i)?.[1];
  if (resolution) quality.push(resolution.toLowerCase());
  if (/\b(?:HD[ -]?)?CAM\b/i.test(releaseDetails)) quality.push("Camera recording");
  if (/\b(?:(?:HD[ -]?)?TS|telesync)\b/i.test(releaseDetails)) quality.push("Telesync");
  if (/\bWEB[ -]?DL\b/i.test(normalized)) quality.push("WEB-DL");
  else if (/\bWEB[ -]?Rip\b/i.test(normalized)) quality.push("WEBRip");
  else if (/\bBlu[ -]?Ray\b/i.test(normalized)) quality.push("Blu-ray");
  else if (/\b(?:BDRip|BRRip)\b/i.test(normalized)) quality.push("Blu-ray rip");
  else if (/\bDVDRip\b/i.test(normalized)) quality.push("DVD rip");
  if (/\b(?:HEVC|[xh][ -]?265)\b/i.test(normalized)) quality.push("HEVC");
  else if (/\b(?:AVC|[xh][ -]?264)\b/i.test(normalized)) quality.push("H.264");
  if (/\bHDR10\+?/i.test(normalized)) quality.push(normalized.match(/\bHDR10\+?/i)![0].toUpperCase());
  else if (/\bHDR\b/i.test(normalized)) quality.push("HDR");

  // A year at the start may be part of a numeric title (1917, 2001, 1984).
  // Multiple later years, release-group prefixes and TV/collection names are
  // deliberately left for the user instead of choosing a likely identity.
  if (years.length !== 1 || /\b(?:S\d{1,2}(?:E\d{1,3})?|season|collection|trilogy|complete)\b/i.test(normalized)) {
    return { title: "", year: "", quality };
  }
  const year = years[0];
  const title = normalized.slice(0, year.index).replace(/[\s([\-]+$/, "").trim();
  if (!title || title.length > 180 || /[\[\]{}\\/]|https?:|\b(?:1080p|2160p|720p|WEB-DL|BluRay)\b/i.test(title)) {
    return { title: "", year: "", quality };
  }
  return { title, year: year[0], quality };
}
