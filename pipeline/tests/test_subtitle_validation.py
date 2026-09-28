"""Exercise real subtitle checks and selection without media or model inference."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline.ingest.subtitles import (
    SUBTITLE_VALIDATION_PROFILE,
    inspect_external_srt,
    validate_external_srt,
)
from pipeline.intake import (
    copy_file_no_replace,
    probe_intake_duration,
    resolve_external_sidecars,
)


DURATION = 600.0
_OBJECTS = ("letter", "photograph", "book", "key", "ticket", "coat", "map", "box")
_PLACES = ("kitchen", "garden", "station", "bedroom", "office", "hallway",
           "car", "attic", "cellar", "shop", "hotel", "school")


def _english(index: int) -> str:
    return (
        f"You said the {_OBJECTS[index % 8]} was here in the {_PLACES[index // 8 % 12]}, "
        "but I can't find it. Where should we go now?"
    )


def _spanish(index: int) -> str:
    return (
        f"Dijiste que la carta estaba en la habitación número {index}, "
        "pero no puedo encontrarla. ¿Dónde debemos buscar ahora?"
    )


def _entries(count: int = 96, *, start: float = 3, step: float = 6):
    return [(start + index * step, start + index * step + 4, _english(index))
            for index in range(count)]


def _timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def _srt(entries=None) -> str:
    entries = _entries() if entries is None else entries
    return "".join(
        f"{index}\n{_timestamp(start)} --> {_timestamp(end)}\n{text}\n\n"
        for index, (start, end, text) in enumerate(entries, 1)
    )


def _write(path: Path, content: str | bytes | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _srt() if content is None else content
    path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    return path


@pytest.fixture
def release(tmp_path):
    incoming = tmp_path / "incoming"
    folder = incoming / "The.Letter.2020.1080p"
    source = _write(folder / "The.Letter.2020.mkv", b"film fixture, never probed")
    return incoming, folder, source


def _resolve(release, *, duration=DURATION):
    incoming, folder, source = release
    return resolve_external_sidecars(incoming.resolve(), source, folder,
                                     media_duration=duration)


def test_full_english_track_passes_without_changing_raw_evidence(tmp_path):
    path = _write(tmp_path / "film.srt")
    original = path.read_bytes()

    result = validate_external_srt(path, DURATION)

    assert result.valid and result.automatic_eligible, result.reasons
    assert result.reasons == ()
    assert result.profile == SUBTITLE_VALIDATION_PROFILE
    assert result.sha256 == hashlib.sha256(original).hexdigest()
    assert result.cue_count == 96
    assert result.english_sections == 3 and result.occupied_sections == 10
    assert result.first_start == 3 and result.last_end == 577
    assert 0 < len(result.excerpt) <= 240
    assert "Audio synchronization is not verified" in result.summary
    assert path.read_bytes() == original


@pytest.mark.parametrize("encoding", ["utf-8-sig", "cp1252"])
def test_common_encodings_crlf_and_formatting_still_pass(tmp_path, encoding):
    content = _srt().replace("You said", "<i>You said</i>").replace("find it", "find it near the café")
    raw = content.replace("\n", "\r\n").encode(encoding)
    path = _write(tmp_path / "film.srt", raw)

    result = validate_external_srt(path, DURATION)

    assert result.automatic_eligible, result.reasons
    assert "<i>" not in result.excerpt
    assert path.read_bytes() == raw


@pytest.mark.parametrize("duration", [None, float("nan"), float("inf"), 0, -1])
def test_unknown_or_invalid_duration_keeps_candidate_reviewable(tmp_path, duration):
    result = validate_external_srt(_write(tmp_path / "film.srt"), duration)
    assert result.valid and not result.automatic_eligible
    assert any("duration" in reason for reason in result.reasons)


@pytest.mark.parametrize("bad_cue", [
    "97\nthis is not a timestamp\nThe missing dialogue should not disappear.\n\n",
    "97\n00:09:40,000 --> 00:09:39,000\nThe interval runs backwards.\n\n",
    "97\n00:09:40,000 --> 00:09:40,000\nThe interval is empty.\n\n",
    "97\n00:60:00,000 --> 01:00:01,000\nThe minute field is invalid.\n\n",
    "97\n00:09:60,000 --> 00:10:01,000\nThe second field is invalid.\n\n",
    "97\n00:09:40,000 --> 00:10:05,000\nThis extends beyond the film.\n\n",
    "97\n00:09:40,000 --> 00:09:44,000\n<i></i>\n\n",
    "97\n00:09:40,000 --> 00:09:44,000\nFirst line.\n00:09:45,000 --> 00:09:46,000\nSecond cue without separator.\n\n",
])
def test_hidden_bad_cue_invalidates_the_whole_track(tmp_path, bad_cue):
    path = _write(tmp_path / "film.en.srt", _srt() + bad_cue)
    original = path.read_bytes()

    result = validate_external_srt(path, DURATION)

    assert not result.valid and not result.automatic_eligible
    assert result.reasons
    assert result.sha256 == hashlib.sha256(original).hexdigest()
    assert path.read_bytes() == original


def test_stricter_intake_does_not_change_legacy_canonical_content_floor(tmp_path):
    path = _write(tmp_path / "film.en.srt", _srt() + "97\nmalformed timestamp\nHidden text\n\n")
    assert inspect_external_srt(path) is not None
    assert not validate_external_srt(path, DURATION).valid


@pytest.mark.parametrize("corruption", ["\x00", "\x1b", "\x7f", "\x85", "\ufffd"])
def test_corrupt_controls_or_replacement_characters_are_rejected(tmp_path, corruption):
    path = _write(tmp_path / "film.srt", _srt().replace("You said", f"You {corruption}said", 1))
    result = validate_external_srt(path, DURATION)
    assert not result.valid
    assert "control characters" in result.summary


@pytest.mark.parametrize("content", [b"", b"\x81", b"not a subtitle"])
def test_empty_undecodable_or_non_srt_files_are_rejected(tmp_path, content):
    assert not validate_external_srt(_write(tmp_path / "film.srt", content), DURATION).valid


def test_oversize_missing_and_directory_inputs_are_rejected(tmp_path):
    path = tmp_path / "large.srt"
    with path.open("wb") as handle:
        handle.seek(16 * 1024 * 1024)
        handle.write(b"x")
    assert not validate_external_srt(path, DURATION).valid
    assert not validate_external_srt(tmp_path / "missing.srt", DURATION).valid
    assert not validate_external_srt(tmp_path, DURATION).valid


def test_small_duration_rounding_tolerance_does_not_reject_a_track(tmp_path):
    entries = _entries()
    entries[-1] = (598, 601, entries[-1][2])
    result = validate_external_srt(_write(tmp_path / "film.srt", _srt(entries)), DURATION)
    assert result.automatic_eligible, result.reasons


@pytest.mark.parametrize("language", ["spanish", "mixed", "non_latin"])
def test_english_filename_cannot_override_language_evidence(tmp_path, language):
    entries = _entries()
    for index, (start, end, text) in enumerate(entries):
        if language == "spanish" or language == "mixed" and 32 <= index < 64:
            text = _spanish(index)
        elif language == "non_latin":
            text += " 这是一个很长的中文句子我们应该仔细检查这些字幕中的每一个词语。"
        entries[index] = (start, end, text)
    result = validate_external_srt(_write(tmp_path / "film.en.srt", _srt(entries)), DURATION)
    assert result.valid and not result.automatic_eligible
    assert result.english_sections == (2 if language == "mixed" else 0)
    assert "English dialogue could not be established" in result.summary


@pytest.mark.parametrize("entries,reason", [
    (_entries(12, step=50), "sparse"),
    (_entries(step=2), "cover enough"),
    (_entries(start=120, step=4.7), "cover enough"),
])
def test_sparse_or_truncated_tracks_remain_reviewable(tmp_path, entries, reason):
    result = validate_external_srt(_write(tmp_path / "film.srt", _srt(entries)), DURATION)
    assert result.valid and not result.automatic_eligible
    assert reason in result.summary


def test_repeated_text_cannot_manufacture_a_complete_track(tmp_path):
    entries = [(start, end, _english(0)) for start, end, _ in _entries()]
    result = validate_external_srt(_write(tmp_path / "film.srt", _srt(entries)), DURATION)
    assert result.valid and not result.automatic_eligible
    assert "repeats" in result.summary


def test_a_few_simultaneous_speakers_are_allowed(tmp_path):
    entries = _entries()
    for index in (12, 48, 84):
        entries[index] = (entries[index - 1][1] - .5, entries[index][1], entries[index][2])
    result = validate_external_srt(_write(tmp_path / "film.srt", _srt(entries)), DURATION)
    assert result.automatic_eligible, result.reasons


@pytest.mark.parametrize("change,reason", [("disorder", "out of order"), ("overlaps", "overlap")])
def test_disorder_or_frequent_overlaps_prevent_automatic_selection(tmp_path, change, reason):
    entries = _entries()
    if change == "disorder":
        entries[30], entries[31] = entries[31], entries[30]
    else:
        for index in range(10, 20):
            start, _, text = entries[index]
            entries[index] = (start, entries[index + 1][0] + 1, text)
    result = validate_external_srt(_write(tmp_path / "film.srt", _srt(entries)), DURATION)
    assert result.valid and not result.automatic_eligible
    assert reason in result.summary


@pytest.mark.parametrize("relative", ["The.Letter.2020.srt", "Subs/ENG.srt"])
def test_full_english_unmarked_or_release_scoped_track_is_automatic(release, relative):
    _, folder, _ = release
    path = _write(folder / relative)
    automatic, candidates = _resolve(release)
    assert automatic == path
    assert [candidate.path for candidate in candidates] == [path]
    assert candidates[0].validation.automatic_eligible
    automatic, candidates = _resolve(release, duration=None)
    assert automatic is None
    assert [candidate.path for candidate in candidates] == [path]
    assert candidates[0].validation.valid
    assert candidates[0].excerpt == candidates[0].validation.excerpt


def test_flat_incoming_source_can_select_matching_unmarked_track(tmp_path):
    source = _write(tmp_path / "The.Letter.2020.mkv", b"film")
    sidecar = _write(tmp_path / "The.Letter.2020.srt")
    automatic, candidates = resolve_external_sidecars(tmp_path.resolve(), source, None,
                                                     media_duration=DURATION)
    assert automatic == sidecar
    assert [candidate.path for candidate in candidates] == [sidecar]
    assert candidates[0].validation.automatic_eligible


def test_conflicting_full_tracks_remain_separate_choices(release):
    _, folder, _ = release
    paths = [_write(folder / "The.Letter.2020.en.srt"),
             _write(folder / "The.Letter.2020.eng.srt", _srt().replace("go now", "go tomorrow"))]
    automatic, candidates = _resolve(release)
    assert automatic is None
    assert {candidate.path for candidate in candidates} == set(paths)
    assert all(candidate.validation.automatic_eligible for candidate in candidates)


@pytest.mark.parametrize("accessibility", [False, True])
def test_byte_identical_eligible_tracks_are_one_automatic_choice_without_removing_copies(release, accessibility):
    _, folder, _ = release
    marker = ".SDH" if accessibility else ""
    paths = [_write(folder / f"The.Letter.2020.en{marker}.srt"),
             _write(folder / f"Subs/ENG{marker}.srt")]
    originals = {path: path.read_bytes() for path in paths}
    automatic, candidates = _resolve(release)
    assert automatic == min(paths, key=lambda path: str(path).casefold())
    assert {candidate.path for candidate in candidates} == set(paths)
    assert all(candidate.validation.automatic_eligible for candidate in candidates)
    assert {candidate.validation.sha256 for candidate in candidates} == {hashlib.sha256(originals[paths[0]]).hexdigest()}
    assert {path: path.read_bytes() for path in paths} == originals
    assert _resolve(release)[0] == automatic  # deterministic representative
    assert _resolve(release, duration=None)[0] is None  # copies cannot bypass validation


def test_duplicate_ordinary_tracks_still_outrank_a_different_accessibility_track(release):
    _, folder, _ = release
    ordinary = [_write(folder / "The.Letter.2020.en.srt"), _write(folder / "Subs/ENG.srt")]
    sdh = _write(folder / "0/ENG.SDH.srt", _srt().replace("go now", "go tomorrow"))
    originals = {path: path.read_bytes() for path in [*ordinary, sdh]}
    automatic, candidates = _resolve(release)
    assert automatic == min(ordinary, key=lambda path: str(path).casefold())
    assert {candidate.path for candidate in candidates} == {*ordinary, sdh}
    assert all(candidate.validation.automatic_eligible for candidate in candidates)
    assert len({candidate.validation.sha256 for candidate in candidates}) == 2
    assert {path: path.read_bytes() for path in originals} == originals


@pytest.mark.parametrize("different_content", ["dialogue", "line_endings"])
def test_duplicate_copies_do_not_hide_a_conflicting_ordinary_track(release, different_content):
    _, folder, _ = release
    ordinary = [_write(folder / "The.Letter.2020.en.srt"), _write(folder / "Subs/ENG.srt")]
    raw = _srt().replace("go now", "go tomorrow") if different_content == "dialogue" else _srt().replace("\n", "\r\n")
    other = _write(folder / "The.Letter.2020.eng.srt", raw)
    automatic, candidates = _resolve(release)
    assert automatic is None
    assert {candidate.path for candidate in candidates} == {*ordinary, other}
    assert all(candidate.validation.automatic_eligible for candidate in candidates)
    assert len({candidate.validation.sha256 for candidate in candidates}) == 2


def test_one_ordinary_track_is_preferred_over_accessibility_variants(release):
    _, folder, _ = release
    ordinary = _write(folder / "The.Letter.2020.srt")
    sdh = _write(folder / "Subs/ENG.SDH.srt", _srt().replace("go now", "go tomorrow"))
    cc = _write(folder / "The.Letter.2020.en.CC.srt", _srt().replace("go now", "go tonight"))
    automatic, candidates = _resolve(release)
    assert automatic == ordinary
    assert {candidate.path for candidate in candidates} == {ordinary, sdh, cc}
    assert all(candidate.validation.automatic_eligible for candidate in candidates)


@pytest.mark.parametrize("relative", [
    "Different.Movie.2020.en.srt", "The.Letter.1999.en.srt",
    "The.Letter.2020.en.forced.srt", "The.Letter.2020.en.commentary.srt",
    "The.Letter.2020.en.French.srt", "The.Letter.2020.en.Spanish.srt",
    "Featurettes/ENG.srt", "Extras/The.Letter.2020.en.srt", "Subs/random.srt",
])
def test_name_and_release_exclusions_survive_passing_content(release, relative):
    _, folder, _ = release
    path = _write(folder / relative)
    assert validate_external_srt(path, DURATION).automatic_eligible
    assert _resolve(release) == (None, [])


def test_generic_label_requires_a_matching_release(release):
    incoming, _, _ = release
    folder = incoming / "Different.Movie.2020"
    source = _write(folder / "The.Letter.2020.mkv", b"film")
    _write(folder / "Subs/ENG.srt")
    assert _resolve((incoming, folder, source)) == (None, [])


def test_malformed_track_is_excluded_but_mislabeled_spanish_remains_reviewable(release):
    _, folder, _ = release
    _write(folder / "The.Letter.2020.srt", _srt() + "broken cue\n\n")
    spanish = _write(folder / "The.Letter.2020.en.srt", _srt([
        (start, end, _spanish(index)) for index, (start, end, _) in enumerate(_entries())
    ]))
    automatic, candidates = _resolve(release)
    assert automatic is None
    assert [candidate.path for candidate in candidates] == [spanish]
    assert not candidates[0].validation.automatic_eligible


@pytest.mark.parametrize("output", ["600.25\n", "N/A", "nan", "inf", "0", "-1"])
def test_duration_probe_is_bounded_metadata_only_and_fails_closed(monkeypatch, tmp_path, output):
    observed = []

    def run(command, **kwargs):
        observed.append((command, kwargs))
        return SimpleNamespace(stdout=output)

    monkeypatch.setattr("pipeline.intake.subprocess.run", run)
    source = tmp_path / "The Letter (2020).mkv"
    result = probe_intake_duration(source)
    assert result == (600.25 if output == "600.25\n" else None)
    command, options = observed[0]
    assert len(observed) == 1 and command[0] == "ffprobe"
    assert command[-1] == str(source)
    assert "format=duration" in command
    assert command[command.index("-protocol_whitelist") + 1] == "file,pipe"
    assert options["check"] is True and 0 < options["timeout"] <= 30
    assert options.get("shell", False) is False


@pytest.mark.parametrize("failure", [FileNotFoundError(), subprocess.TimeoutExpired("ffprobe", 15),
                                      subprocess.CalledProcessError(1, "ffprobe")])
def test_duration_probe_failures_abstain_without_retry(monkeypatch, tmp_path, failure):
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        raise failure

    monkeypatch.setattr("pipeline.intake.subprocess.run", run)
    assert probe_intake_duration(tmp_path / "film.mkv") is None
    assert len(calls) == 1


def test_hash_checked_copy_preserves_exact_bytes_and_source(tmp_path):
    source = _write(tmp_path / "release.srt", _srt().replace("\n", "\r\n").encode("utf-8-sig"))
    raw = source.read_bytes()
    destination = tmp_path / "canonical.en.srt"
    copy_file_no_replace(source, destination, expected_sha256=hashlib.sha256(raw).hexdigest())
    assert source.read_bytes() == destination.read_bytes() == raw


def test_changed_subtitle_cannot_leave_a_canonical_copy(tmp_path):
    source = _write(tmp_path / "release.srt")
    digest = validate_external_srt(source, DURATION).sha256
    source.write_bytes(b"changed after validation")
    destination = tmp_path / "canonical.en.srt"
    with pytest.raises(ValueError, match="changed"):
        copy_file_no_replace(source, destination, expected_sha256=digest)
    assert not destination.exists()
    assert source.read_bytes() == b"changed after validation"


def test_hash_checked_copy_never_overwrites_an_existing_destination(tmp_path):
    source = _write(tmp_path / "release.srt")
    destination = _write(tmp_path / "canonical.en.srt", b"existing evidence")
    with pytest.raises(FileExistsError):
        copy_file_no_replace(source, destination,
                             expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    assert destination.read_bytes() == b"existing evidence"
