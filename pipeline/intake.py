"""Shared local film intake rules, independent of HTTP and acquisition workers.

Manual import and managed acquisition use the same conservative source discovery,
canonical naming and non-destructive subtitle selection.
"""
from __future__ import annotations

import os
import hashlib
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pipeline.config import VIDEO_EXTENSIONS
from pipeline.ingest.subtitles import SubtitleValidation, validate_external_srt

_IMPORTED_RELEASE_MARKER = ".scene-recall-imported"
_WINDOWS_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)
_RELEASE_YEAR = re.compile(r"(?<!\d)((?:18|19|20)\d{2})(?!\d)")
_RELEASE_TECHNICAL = re.compile(
    r"\b(?:480p|576p|720p|1080[pi]|2160p|4k|uhd|bluray|blu-ray|brrip|"
    r"webrip|web-dl|webdl|dvdrip|hdtv|remux|x26[45]|h[.-]?26[45]|hevc|"
    r"av1|hdr10?|dolby[ .]?vision|aac|dts|truehd|atmos)\b",
    re.IGNORECASE,
)
_EDITION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bdirector'?s\s+cut\b", re.IGNORECASE), "Director's Cut"),
    (re.compile(r"\bextended(?:\s+(?:cut|edition))?\b", re.IGNORECASE), "Extended"),
    (re.compile(r"\bfinal\s+cut\b", re.IGNORECASE), "Final Cut"),
    (re.compile(r"\bcriterion(?:\s+collection)?\b", re.IGNORECASE), "Criterion"),
    (re.compile(r"\bunrated\b", re.IGNORECASE), "Unrated"),
    (re.compile(r"\btheatrical(?:\s+(?:cut|edition))?\b", re.IGNORECASE), "Theatrical"),
    (re.compile(r"\bspecial\s+edition\b", re.IGNORECASE), "Special Edition"),
    (re.compile(r"\bremaster(?:ed)?\b", re.IGNORECASE), "Remastered"),
)


def sanitize_filename_component(value: str, field: str) -> str:
    cleaned = value.replace(":", " - ")
    cleaned = _WINDOWS_INVALID_FILENAME.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        raise ValueError(f"{field} cannot be empty")
    if cleaned.upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def canonical_film_filename(
    title: str,
    year: int | None,
    edition: str | None,
    extension: str,
) -> str:
    clean_title = sanitize_filename_component(title, "title")
    clean_edition = (
        sanitize_filename_component(edition, "edition") if edition else None
    )
    if extension.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("unsupported video extension")
    stem = clean_title
    if year is not None:
        stem += f" ({year})"
    if clean_edition:
        stem += f" [{clean_edition}]"
    filename = stem + extension.lower()
    if len(filename) > 255:
        raise ValueError("canonical filename is longer than 255 characters")
    return filename


def release_suggestion(label: str) -> tuple[str, int | None, str | None]:
    """Parse only obvious year, quality, and edition markers from a release."""
    stem = Path(label).stem if Path(label).suffix.lower() in VIDEO_EXTENSIONS else label
    normalized = re.sub(r"[._]+", " ", stem)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    technical_match = _RELEASE_TECHNICAL.search(normalized)
    # Numeric film titles are common. The release year is conventionally the
    # last plausible year before quality/codec metadata, not the first number.
    year_search_end = technical_match.start() if technical_match else len(normalized)
    year_matches = list(_RELEASE_YEAR.finditer(normalized, 0, year_search_end))
    year_match = year_matches[-1] if year_matches else None
    year = int(year_match.group(1)) if year_match else None
    cut_at = len(normalized)
    if year_match:
        cut_at = min(cut_at, year_match.start())
    if technical_match:
        cut_at = min(cut_at, technical_match.start())
    raw_title = re.sub(r"[\[\](){}]+", " ", normalized[:cut_at])
    raw_title = re.sub(r"\s+", " ", raw_title).strip(" -")
    if not raw_title:
        raw_title = normalized
    if raw_title.islower() or raw_title.isupper():
        raw_title = raw_title.title()

    edition_tail = normalized[year_match.end():] if year_match else ""
    edition = next(
        (
            display
            for pattern, display in _EDITION_PATTERNS
            if pattern.search(edition_tail)
        ),
        None,
    )
    return raw_title, year, edition


def is_regular_video(path: Path) -> bool:
    try:
        return (
            not path.is_symlink()
            and path.is_file()
            and path.suffix.lower() in VIDEO_EXTENSIONS
        )
    except OSError:
        return False


def is_link_or_junction(path: Path) -> bool:
    """Keep discovery on the configured volume and out of reparse trees."""
    try:
        return path.is_symlink() or (
            hasattr(path, "is_junction") and path.is_junction()
        )
    except OSError:
        return True


def videos_in_release(root: Path, release: Path) -> list[Path]:
    videos: list[Path] = []
    for directory, child_dirs, filenames in os.walk(release, followlinks=False):
        directory_path = Path(directory)
        child_dirs[:] = [
            name
            for name in child_dirs
            if not is_link_or_junction(directory_path / name)
        ]
        for filename in filenames:
            path = directory_path / filename
            if (
                path.suffix.lower() in VIDEO_EXTENSIONS
                and is_regular_video(path)
                and path.resolve().is_relative_to(root)
            ):
                videos.append(path)
    return videos


def subtitle_files_in_release(root: Path, release: Path) -> list[Path]:
    """Return safe SRT candidates without following release reparse points."""
    subtitles: list[Path] = []
    for directory, child_dirs, filenames in os.walk(release, followlinks=False):
        directory_path = Path(directory)
        child_dirs[:] = [
            name
            for name in child_dirs
            if not is_link_or_junction(directory_path / name)
        ]
        for filename in filenames:
            path = directory_path / filename
            try:
                if (
                    path.suffix.lower() == ".srt"
                    and path.is_file()
                    and not path.is_symlink()
                    and path.resolve().is_relative_to(root)
                ):
                    subtitles.append(path)
            except OSError:
                continue
    return subtitles


@dataclass(frozen=True, slots=True)
class SubtitleReviewCandidate:
    path: Path
    excerpt: str
    validation: SubtitleValidation


def resolve_external_sidecars(
    incoming_root: Path,
    source: Path,
    release_dir: Path | None,
    *,
    media_duration: float | None = None,
    allow_generic_in_root: bool = False,
) -> tuple[Path | None, list[SubtitleReviewCandidate]]:
    """Resolve an automatic English sidecar or safe candidates for review."""
    if release_dir is None:
        candidates = [
            source.with_name(source.stem + suffix)
            for suffix in (".en.srt", ".eng.srt", ".english.srt", ".srt")
        ]
        candidates = [
            candidate
            for candidate in candidates
            if candidate.is_file() and not candidate.is_symlink()
        ]
    else:
        candidates = subtitle_files_in_release(incoming_root, release_dir)

    def label_tokens(label: str) -> set[str]:
        return set(re.findall(r"[^\W_]+", label.casefold()))

    english_markers = {"en", "eng", "english"}
    alternate_markers = {"commentary", "forced"}
    accessibility_markers = {"cc", "sdh", "hoh", "hearing", "impaired"}
    extra_markers = {
        "bonus",
        "deleted",
        "extra",
        "extras",
        "featurette",
        "featurettes",
        "interview",
        "sample",
        "trailer",
    }
    foreign_markers = {
        "arabic", "ara",
        "chinese", "chi", "zho",
        "czech", "cze", "ces",
        "danish", "dan",
        "dutch", "dut", "nld",
        "finnish", "fin",
        "french", "fre", "fra",
        "german", "ger", "deu",
        "greek", "gre", "ell",
        "hebrew", "heb",
        "hindi", "hin",
        "hungarian", "hun",
        "indonesian", "ind",
        "italian", "ita",
        "japanese", "jpn",
        "korean", "kor",
        "norwegian", "nor",
        "polish", "pol",
        "portuguese", "por",
        "romanian", "rom", "rum", "ron",
        "russian", "rus",
        "spanish", "spa",
        "swedish", "swe",
        "thai", "tha",
        "turkish", "tur",
        "ukrainian", "ukr",
        "vietnamese", "vie",
    }
    insignificant_title_tokens = {"a", "an", "and", "of", "the", "to"}
    source_title, source_year, _source_edition = release_suggestion(source.name)
    source_identity = (
        label_tokens(source_title) - insignificant_title_tokens
    )
    if (source_year is None and source_identity
            and source_identity <= {"video", "movie", "film", "feature", "main"}
            and release_dir is not None):
        # A generic video name may be wrapped in Video/ beneath its dated film
        # folder. Associate subtitles with that nearest clear release identity.
        for parent in source.parents:
            if not parent.is_relative_to(release_dir):
                break
            parent_title, parent_year, _ = release_suggestion(parent.name)
            if parent_year is not None:
                source_identity = label_tokens(parent_title) - insignificant_title_tokens
                source_year = parent_year
                break
    if not source_identity:
        return None, []
    release_matches_source = False
    if release_dir is not None:
        release_title, _release_year, _release_edition = release_suggestion(
            release_dir.name
        )
        release_identity = (
            label_tokens(release_title) - insignificant_title_tokens
        )
        release_matches_source = release_identity == source_identity
        # A download may wrap the real release in one or more outer folders.
        # Generic ENG.srt belongs only to the matching film's subtree.
        matching_parent = next((parent for parent in source.parents
            if parent.is_relative_to(release_dir)
            and (label_tokens(release_suggestion(parent.name)[0]) - insignificant_title_tokens) == source_identity), None)
    else:
        matching_parent = None

    generic_track_tokens = english_markers | accessibility_markers | {
        "default",
        "full",
        "sub",
        "subs",
        "subtitle",
        "subtitles",
    }

    review_candidates: list[SubtitleReviewCandidate] = []
    descriptors_by_path: dict[Path, set[str]] = {}
    automatic_paths: set[Path] = set()
    for path in sorted(set(candidates), key=lambda item: str(item).casefold()):
        filename_tokens = label_tokens(path.stem)
        filename_associated = source_identity.issubset(filename_tokens)
        generic_label_tokens = {
            token for token in filename_tokens if not token.isdigit()
        }
        generic_english_label = bool(generic_label_tokens & english_markers) and (
            generic_label_tokens <= generic_track_tokens
        )
        if not filename_associated and not (
            generic_english_label and (release_matches_source
                or (matching_parent is not None and path.is_relative_to(matching_parent))
                or (allow_generic_in_root and source.parent == release_dir))
        ):
            continue
        candidate_years = {
            int(token)
            for token in filename_tokens
            if re.fullmatch(r"(?:18|19|20)\d{2}", token)
        }
        if (
            source_year is not None
            and candidate_years
            and source_year not in candidate_years
        ):
            continue
        if release_dir is None:
            context_tokens = filename_tokens
        else:
            relative_label = " ".join(path.relative_to(release_dir).parts)
            context_tokens = label_tokens(relative_label)
        descriptors = context_tokens - source_identity
        if descriptors & (alternate_markers | extra_markers | foreign_markers):
            continue
        inspection = validate_external_srt(path, media_duration)
        if not inspection.valid:
            continue
        review_candidates.append(
            SubtitleReviewCandidate(path=path, excerpt=inspection.excerpt, validation=inspection)
        )
        descriptors_by_path[path] = descriptors
        if inspection.automatic_eligible:
            automatic_paths.add(path)

    english_candidates = [
        candidate
        for candidate in review_candidates
        if candidate.path in automatic_paths
    ]
    automatic: Path | None = None
    # Apply the ordinary-track preference before comparing content. Identical
    # validated copies are one choice, without merging files or review handles.
    standard = [
        candidate for candidate in english_candidates
        if not (descriptors_by_path[candidate.path] & accessibility_markers)
    ]
    preferred = standard or english_candidates
    if preferred:
        digest = preferred[0].validation.sha256
        if len(preferred) == 1 or (digest and all(
            candidate.validation.sha256 == digest for candidate in preferred
        )):
            automatic = preferred[0].path

    # Keep eligible alternatives available for an explicit selection even when
    # one track is preferred automatically (for example an ordinary vs SDH pair).
    return automatic, review_candidates


def probe_intake_duration(source: Path) -> float | None:
    """Bounded metadata-only probe on import, never on inventory polling."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(source)],
            capture_output=True, text=True, check=True, timeout=15,
        )
        duration = float(result.stdout.strip())
        return duration if math.isfinite(duration) and duration > 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        # Unknown duration means automatic subtitle selection must abstain.
        return None


def copy_file_no_replace(source: Path, destination: Path, *, expected_sha256: str | None = None) -> None:
    """Copy a small raw sidecar without replacing an existing peer."""
    destination_created = False
    try:
        with source.open("rb") as source_file, destination.open("xb") as target_file:
            destination_created = True
            if expected_sha256 is None:
                shutil.copyfileobj(source_file, target_file)
            else:
                digest = hashlib.sha256()
                while block := source_file.read(1024 * 1024):
                    digest.update(block)
                    target_file.write(block)
                if digest.hexdigest() != expected_sha256:
                    raise ValueError("Verified subtitle changed before import")
        shutil.copystat(source, destination)
    except Exception:
        if destination_created:
            destination.unlink(missing_ok=True)
        raise


def move_file_no_replace(source: Path, destination: Path) -> None:
    """Move a same-volume regular file without ever replacing a peer."""
    if os.name == "nt":
        # MoveFileEx without MOVEFILE_REPLACE_EXISTING is the behavior exposed
        # by Path.rename on Windows.
        source.rename(destination)
        return

    # POSIX rename replaces an existing destination, so create the destination
    # link exclusively and then remove the incoming name.
    os.link(source, destination, follow_symlinks=False)
    try:
        source.unlink()
    except OSError:
        destination.unlink(missing_ok=True)
        raise


