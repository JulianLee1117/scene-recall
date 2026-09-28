"""Bounded, immutable JSON context artifacts with one atomic manifest per film.

The caller supplies verified source identity. This module never opens raw films,
reads an index, calls a model, or decides whether a clip is legally selectable.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from pathlib import Path
from typing import Literal

from filelock import FileLock
from pydantic import Field, JsonValue, TypeAdapter

from pipeline.context.schema import (
    MAX_ACTIVE_ARTIFACTS, MAX_ARTIFACT_BYTES, MAX_MANIFEST_BYTES, SCHEMA_VERSION,
    ContextArtifact, ContextRecord, ContextSource, Derivation, Identifier, ProfileId,
    SHA256, StrictModel, TimeRange, intersect_ranges, merge_ranges, ranges_json,
    subtract_ranges,
)


MAX_LOOKUP_ARTIFACTS = 32
MAX_LOOKUP_BYTES = 8 * 1024 * 1024
MAX_LOOKUP_RANGES = 128
MAX_PROFILE_BYTES = 64 * 1024


class ContextStoreError(ValueError):
    """Context storage is invalid, corrupt or unavailable."""


class _ReadFailure(ContextStoreError):
    def __init__(self, message: str, bytes_read: int):
        super().__init__(message)
        self.bytes_read = bytes_read


class _RecordRef(StrictModel):
    level: Literal["film", "sequence", "local"]
    parent_refs: list[Identifier] = Field(max_length=8)


class _Entry(StrictModel):
    artifact_id: SHA256
    coverage: list[TimeRange] = Field(min_length=1, max_length=64)
    applicability: list[TimeRange] = Field(max_length=128 * 64)
    records: dict[Identifier, _RecordRef] = Field(max_length=128)
    input_dependencies: dict[Identifier, SHA256] = Field(max_length=256)


class _Manifest(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    profile_id: ProfileId
    profile_key: dict[str, JsonValue]
    source: ContextSource
    entries: list[_Entry] = Field(max_length=MAX_ACTIVE_ARTIFACTS)
    generation: SHA256


class _Profile(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    profile_id: ProfileId
    profile_key: dict[str, JsonValue]
    identity: SHA256


def _bytes(value) -> bytes:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value) -> str:
    return hashlib.sha256(_bytes(value)).hexdigest()


def _profile_document(profile_id: str, derivation: Derivation) -> _Profile:
    body = {"schema_version": SCHEMA_VERSION, "profile_id": profile_id,
            "profile_key": derivation.profile_key()}
    profile = _Profile(**body, identity=_digest(body))
    if len(_bytes(profile)) > MAX_PROFILE_BYTES:
        raise ContextStoreError("Context producer profile exceeds its bounded size")
    return profile


def _absolute(path: Path) -> Path:
    value = os.path.abspath(path)
    # Content hashes make legitimate nested artifact paths long. Explicit
    # extended paths work even when Windows' optional long-path policy is off.
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        value = "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
    return Path(value)


def _safe(path: Path) -> Path:
    path = _absolute(path)
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ContextStoreError("Context storage path contains a link")
    return path


def _read(path: Path, maximum: int) -> tuple[dict, int]:
    path = _safe(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
        raise ContextStoreError("Context document is not an ordinary bounded file")
    with path.open("rb") as handle:
        payload = handle.read(maximum + 1)
    if len(payload) > maximum:
        raise _ReadFailure("Context document exceeds the read limit", len(payload))
    try:
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError("Context document must be an object")
        _bytes(value)  # Reject non-finite JSON values before trusting any receipt.
    except (ValueError, TypeError) as exc:
        raise _ReadFailure("Context document is not valid finite JSON", len(payload)) from exc
    return value, len(payload)


def _entry(artifact: ContextArtifact, artifact_id: str) -> _Entry:
    return _Entry(artifact_id=artifact_id, coverage=merge_ranges(artifact.coverage),
                  applicability=merge_ranges(bounds for record in artifact.records for bounds in record.applicability),
                  records={record.record_id: _RecordRef(level=record.level, parent_refs=record.parent_refs)
                           for record in artifact.records},
                  input_dependencies=artifact.derivation.input_dependencies)


def _record_map(entries: list[_Entry]) -> dict:
    result = {}
    for entry in entries:
        for identity, record in entry.records.items():
            if identity in result:
                raise ContextStoreError("Duplicate active context record ID")
            result[identity] = (entry.artifact_id, record)
    levels = {"film": 0, "sequence": 1, "local": 2}
    for _, record in result.values():
        for parent_id in record.parent_refs:
            if parent_id not in result:
                raise ContextStoreError("Context parent reference is missing")
            if levels[result[parent_id][1].level] >= levels[record.level]:
                raise ContextStoreError("A parent must refer to a broader context level")
    return result


def _coverage(requested, covered=(), stale=(), unavailable=()) -> dict:
    requested = merge_ranges(requested)
    good = intersect_ranges(requested, covered)
    old = subtract_ranges(intersect_ranges(requested, stale), good)
    bad = subtract_ranges(intersect_ranges(requested, unavailable), [*good, *old])
    missing = subtract_ranges(requested, [*good, *old, *bad])
    return {"requested": ranges_json(requested), "covered": ranges_json(good),
            "missing": ranges_json(missing), "stale": ranges_json(old),
            "unavailable": ranges_json(bad), "complete": not (missing or old or bad)}


def _status(coverage: dict) -> str:
    if coverage["complete"]:
        return "available"
    if coverage["covered"]:
        return "partial"
    if coverage["unavailable"]:
        return "unavailable"
    if coverage["stale"]:
        return "stale"
    return "missing"


class ContextStore:
    def __init__(self, assets_dir: Path):
        self.root = _absolute(assets_dir) / "context"

    def _directory(self, film_id: str, profile_id: str) -> Path:
        TypeAdapter(SHA256).validate_python(film_id)
        return _safe(self._profile_directory(profile_id) / film_id)

    def _profile_directory(self, profile_id: str) -> Path:
        TypeAdapter(ProfileId).validate_python(profile_id)
        return _safe(self.root / profile_id)

    def _profile(self, profile_id: str) -> _Profile:
        value, _ = _read(self._profile_directory(profile_id) / "profile.json", MAX_PROFILE_BYTES)
        profile = _Profile.model_validate(value)
        if profile.profile_id != profile_id or profile.identity != _digest(
            profile.model_dump(mode="json", exclude={"identity"})
        ):
            raise ContextStoreError("Context producer profile identity is corrupt")
        return profile

    def has_profile(self, film_id: str, profile_id: str) -> bool:
        """Cheap presence hint, not a claim of freshness or validity."""
        return _safe(self._directory(film_id, profile_id) / "manifest.json").is_file()

    def validate_profile(self, film_id: str, profile_id: str, derivation) -> None:
        """Check film-scoped profile compatibility before an explicit model call.

        This is a read-only preflight hint; publish repeats it under its lock.
        A missing manifest does not create or reserve a profile.
        """
        derivation = Derivation.model_validate(derivation)
        expected = _profile_document(profile_id, derivation)
        directory = self._directory(film_id, profile_id)
        try:
            profile = self._profile(profile_id)
        except FileNotFoundError:
            if _safe(directory / "manifest.json").exists():
                raise ContextStoreError("Context producer profile is missing")
            return
        if profile.identity != expected.identity:
            raise ContextStoreError("Changed model, prompt or settings require a new context profile")
        try:
            previous = self._manifest(directory, film_id, profile_id)
        except FileNotFoundError:
            return
        if previous.profile_key != derivation.profile_key():
            raise ContextStoreError("Changed model, prompt or settings require a new context profile")

    def _manifest(self, directory: Path, film_id: str, profile_id: str) -> _Manifest:
        value, _ = _read(directory / "manifest.json", MAX_MANIFEST_BYTES)
        manifest = _Manifest.model_validate(value)
        if manifest.profile_id != profile_id or manifest.source.film_id != film_id:
            raise ContextStoreError("Context manifest belongs to another source or profile")
        if manifest.generation != _digest(manifest.model_dump(mode="json", exclude={"generation"})):
            raise ContextStoreError("Context manifest generation does not match its contents")
        try:
            profile = self._profile(profile_id)
        except FileNotFoundError as exc:
            raise ContextStoreError("Context producer profile is missing") from exc
        if _digest(profile.profile_key) != _digest(manifest.profile_key):
            raise ContextStoreError("Film manifest differs from the context producer profile")
        if len({entry.artifact_id for entry in manifest.entries}) != len(manifest.entries):
            raise ContextStoreError("Duplicate active artifact IDs")
        for entry in manifest.entries:
            if any(bounds.end > manifest.source.duration for bounds in [*entry.coverage, *entry.applicability]):
                raise ContextStoreError("Manifest bounds exceed source duration")
        _record_map(manifest.entries)
        return manifest

    def read_artifact(self, film_id: str, profile_id: str, artifact_id: str) -> dict:
        """Read one explicit historical artifact; never follow a stored path."""
        TypeAdapter(SHA256).validate_python(artifact_id)
        directory = self._directory(film_id, profile_id)
        value, _ = _read(directory / "artifacts" / f"{artifact_id}.json", MAX_ARTIFACT_BYTES)
        artifact = ContextArtifact.model_validate(value)
        if _digest(artifact) != artifact_id or artifact.profile_id != profile_id or artifact.source.film_id != film_id:
            raise ContextStoreError("Context artifact identity does not match its contents")
        return artifact.model_dump(mode="json")

    @staticmethod
    def _write(path: Path, payload: bytes, *, immutable=False) -> None:
        path = _safe(path)
        if immutable and path.exists():
            with path.open("rb") as handle:
                previous = handle.read(len(payload) + 1)
            if previous != payload:
                raise ContextStoreError("An immutable context artifact is corrupt")
            return
        temporary = path.with_name(f".{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if immutable:
                temporary.rename(path)
            else:
                os.replace(temporary, path)
        finally:
            try:
                _safe(temporary).unlink(missing_ok=True)
            except OSError:
                pass

    def publish(self, source, evidence, records, derivation, profile_id: str, *, coverage=None) -> dict:
        """Publish one coverage slot, preserving disjoint slots and old artifacts.

        Repeating the same covered window replaces its active derivation. Prompt,
        model and settings changes require a different explicitly selected profile.
        """
        source = ContextSource.model_validate(source)
        records = [ContextRecord.model_validate(record) for record in records]
        derivation = Derivation.model_validate(derivation)
        coverage = merge_ranges(coverage if coverage is not None else
                                [bounds for record in records for bounds in record.applicability])
        artifact = ContextArtifact(profile_id=profile_id, source=source, evidence=evidence,
                                   records=records, derivation=derivation, coverage=coverage)
        payload = _bytes(artifact)
        if len(payload) > MAX_ARTIFACT_BYTES:
            raise ContextStoreError("Context artifact exceeds its bounded size")
        artifact_id = _digest(artifact)
        producer = _profile_document(profile_id, derivation)
        directory = self._directory(source.film_id, profile_id)
        directory.mkdir(parents=True, exist_ok=True)
        # Profile-before-film lock order prevents two first publications on
        # different films from assigning incompatible producers to one profile.
        with (
            FileLock(_safe(directory.parent / ".profile.lock"), timeout=30, preserve_lock_file=True),
            FileLock(_safe(directory / ".publish.lock"), timeout=30, preserve_lock_file=True),
        ):
            self.validate_profile(source.film_id, profile_id, derivation)
            try:
                previous = self._manifest(directory, source.film_id, profile_id)
            except FileNotFoundError:
                previous = None
            if previous is not None and previous.profile_key != derivation.profile_key():
                raise ContextStoreError("Changed model, prompt or settings require a new context profile")
            entries = previous.entries if previous is not None and previous.source.identity_key() == source.identity_key() else []
            retained = [entry for entry in entries if ranges_json(entry.coverage) != ranges_json(coverage)]
            entry = _entry(artifact, artifact_id)
            entries = sorted([*retained, entry], key=lambda value: (value.coverage[0].start, value.artifact_id))
            _record_map(entries)
            body = {"schema_version": SCHEMA_VERSION, "profile_id": profile_id,
                    "profile_key": derivation.profile_key(), "source": source.model_dump(mode="json"),
                    "entries": [item.model_dump(mode="json") for item in entries]}
            manifest = _Manifest(**body, generation=_digest(body))
            manifest_bytes = _bytes(manifest)
            if len(manifest_bytes) > MAX_MANIFEST_BYTES:
                raise ContextStoreError("Context manifest exceeds its bounded size")
            artifacts = _safe(directory / "artifacts")
            artifacts.mkdir(exist_ok=True)
            self._write(directory.parent / "profile.json", _bytes(producer), immutable=True)
            self._write(artifacts / f"{artifact_id}.json", payload, immutable=True)
            self._write(directory / "manifest.json", manifest_bytes)
            return {"film_id": source.film_id, "profile_id": profile_id, "artifact_id": artifact_id,
                    "artifact_ids": [artifact_id], "manifest_generation": manifest.generation,
                    "coverage": ranges_json(coverage), "record_ids": [record.record_id for record in records]}

    def lookup(self, film_id, source_identity, start, end, profile_id, *, input_dependencies=None) -> dict:
        return self.lookup_many(film_id, source_identity, [{"start": start, "end": end}], profile_id,
                                input_dependencies=input_dependencies)

    def lookup_many(self, film_id, source_identity, ranges, profile_id, *, input_dependencies=None) -> dict:
        """Read a bounded candidate cohort from exactly one manifest generation."""
        source = ContextSource.model_validate(source_identity)
        if source.film_id != film_id:
            raise ValueError("Lookup source does not match its film ID")
        if not 1 <= len(ranges) <= MAX_LOOKUP_RANGES:
            raise ValueError("Context lookup requires one to 128 explicit candidate ranges")
        requested = [TimeRange.model_validate(item) for item in ranges]
        if any(item.end > source.duration for item in requested):
            raise ValueError("Lookup range exceeds source duration")
        if input_dependencies is not None:
            input_dependencies = TypeAdapter(dict[Identifier, SHA256]).validate_python(input_dependencies)
        result = {"film_id": film_id, "profile_id": profile_id, "manifest_generation": None,
                  "dependency_status": "unchecked", "artifact_ids": [], "records": [], "evidence": [],
                  "legal_clip_authority": False, "warnings": []}
        good, stale, unavailable = [], [], []
        selected = []
        try:
            directory = self._directory(film_id, profile_id)
            manifest = self._manifest(directory, film_id, profile_id)
        except FileNotFoundError:
            manifest = None
        except (OSError, ValueError, TypeError) as exc:
            manifest = None
            unavailable = requested
            result["warnings"].append(f"Context manifest unavailable: {type(exc).__name__}")
        if manifest is not None:
            result["manifest_generation"] = manifest.generation
            if manifest.source.identity_key() != source.identity_key():
                stale = intersect_ranges(requested, [bounds for entry in manifest.entries for bounds in entry.coverage])
            else:
                references = _record_map(manifest.entries)
                overlapping = [entry for entry in manifest.entries if intersect_ranges(requested, entry.coverage)]
                consumed = 0
                dependency_states = []
                for number, entry in enumerate(overlapping):
                    if input_dependencies is not None and any(
                        key in input_dependencies and input_dependencies[key] != value
                        for key, value in entry.input_dependencies.items()
                    ):
                        stale.extend(entry.coverage)
                        dependency_states.append("stale")
                        continue
                    dependency_states.append("matched" if input_dependencies is not None and
                                             all(input_dependencies.get(key) == value for key, value in entry.input_dependencies.items())
                                             else "unchecked")
                    try:
                        if number >= MAX_LOOKUP_ARTIFACTS:
                            raise ContextStoreError("Context lookup artifact limit reached")
                        path = directory / "artifacts" / f"{entry.artifact_id}.json"
                        value, size = _read(path, min(MAX_ARTIFACT_BYTES, MAX_LOOKUP_BYTES - consumed))
                        consumed += size
                        artifact = ContextArtifact.model_validate(value)
                        if _digest(artifact) != entry.artifact_id or _entry(artifact, entry.artifact_id) != entry:
                            raise ContextStoreError("Artifact or manifest evidence identity mismatch")
                        if artifact.source.identity_key() != source.identity_key() or artifact.profile_id != profile_id:
                            raise ContextStoreError("Artifact source or profile mismatch")
                        if artifact.derivation.profile_key() != manifest.profile_key:
                            raise ContextStoreError("Artifact derivation differs from its selected profile")
                        good.extend(entry.coverage)
                        records = [record for record in artifact.records if intersect_ranges(requested, record.applicability)]
                        selected.append((entry.artifact_id, records, artifact.evidence))
                        for record in records:
                            result["records"].append({**record.model_dump(mode="json"), "artifact_id": entry.artifact_id,
                                "parents": [{"record_id": identity, "artifact_id": references[identity][0]}
                                            for identity in record.parent_refs]})
                    except (OSError, ValueError, TypeError) as exc:
                        consumed += getattr(exc, "bytes_read", 0)
                        unavailable.extend(entry.coverage)
                        result["warnings"].append(f"Artifact {entry.artifact_id} unavailable: {type(exc).__name__}")
                result["dependency_status"] = "stale" if "stale" in dependency_states else (
                    "matched" if dependency_states and all(value == "matched" for value in dependency_states) else "unchecked")
        result["artifact_ids"] = sorted(identity for identity, _, _ in selected)
        result["records"].sort(key=lambda record: (record["artifact_id"], record["record_id"]))
        for identity, records, evidence in selected:
            cited = {reference for record in records for claim in record.claims for reference in claim.evidence_refs}
            result["evidence"].extend({**item.model_dump(mode="json"), "artifact_id": identity}
                                      for item in evidence if item.evidence_id in cited)
        result["evidence"].sort(key=lambda item: (item["artifact_id"], item["evidence_id"]))
        result["coverage"] = _coverage(requested, good, stale, unavailable)
        result["status"] = _status(result["coverage"])
        result["ranges"] = []
        for bounds in requested:
            coverage = _coverage([bounds], good, stale, unavailable)
            refs = [{"artifact_id": record["artifact_id"], "record_id": record["record_id"]}
                    for record in result["records"] if intersect_ranges([bounds], record["applicability"])]
            result["ranges"].append({**bounds.model_dump(mode="json"), "status": _status(coverage),
                                      "record_refs": refs, "coverage": coverage})
        return result
