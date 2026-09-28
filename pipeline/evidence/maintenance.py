"""Evidence storage hygiene: find and remove artifacts of superseded producer profiles.

Readers only use each producer's current profile, so older profiles (a changed
prompt, schema, proxy or setting) are dead weight once the current one exists.
Pruning is explicit: the default is a dry-run report. A superseded profile is
removed only when the film already has the current profile for that kind, so
nothing searchable is ever lost. Raw evidence (films, archived subtitle
downloads) lives elsewhere and is never touched.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from pipeline.evidence.library import FilmRef

_PROFILE = re.compile(r"^(?P<profile>[a-z0-9][a-z0-9-]*-v\d+-[0-9a-f]{10})(?P<rest>(\.[a-z.]+)?)$")


def current_profiles() -> dict[str, str]:
    from pipeline.evidence import hero, measure, metadata, speech, subtitles, synthesis, understanding
    return {"metadata": metadata.PRODUCER.profile_id, "audio": speech.PRODUCER.profile_id,
            "subtitles": subtitles.PRODUCER.profile_id, "understanding": understanding.producer().profile_id,
            "measure": measure.PRODUCER.profile_id, "hero": hero.PRODUCER.profile_id,
            "synthesis": synthesis.PRODUCER.profile_id}


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())


def prune(config: Any, films: list[FilmRef], *, apply: bool = False) -> dict[str, Any]:
    """Report (and with ``apply`` remove) superseded profiles; returns counts and bytes by kind."""
    current = current_profiles()
    report: dict[str, dict[str, int]] = {}
    for film in films:
        root = Path(config.paths.assets_dir) / film.film_id / "evidence"
        for kind, profile in current.items():
            directory = root / kind
            if not directory.is_dir():
                continue
            entries = list(directory.iterdir())
            has_current = any(entry.name in (f"{profile}.json", f"{profile}.json.gz") for entry in entries)
            if not has_current:
                continue                       # never remove the only evidence a film has
            for entry in entries:
                match = _PROFILE.match(entry.name)
                if match is None or match.group("profile") == profile:
                    continue
                stats = report.setdefault(kind, {"entries": 0, "bytes": 0})
                stats["entries"] += 1
                stats["bytes"] += _size(entry)
                if apply:
                    if entry.is_dir():
                        shutil.rmtree(entry)
                    else:
                        entry.unlink()
    return {"applied": apply, "kinds": report,
            "total_mb": round(sum(stats["bytes"] for stats in report.values()) / 1e6, 1)}
