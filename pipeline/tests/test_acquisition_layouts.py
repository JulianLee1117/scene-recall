"""Release-layout coverage through the managed download/import state machine.

The downloader and media probe are mocked; these tests exercise file selection,
review gates, and preservation, rather than claiming codec or film recognition.
"""

from pathlib import Path

import pytest

from pipeline.tests.test_acquisition_engine_review import (
    SRT,
    current,
    detach_download,
    fill_download,
    managed,  # noqa: F401 - shared pytest fixture
    validate_download,
)


FILM = b"complete-main-film-fixture" * 1000


def complete_and_validate(managed, files):
    """Finish the fake client download without assuming the resulting status."""
    root = fill_download(managed, files)
    managed.runner.tick()
    assert current(managed)["status"] == "validating"
    managed.client.record["state"] = "stoppedUP"
    managed.runner.tick()
    return root


def assert_no_import(managed):
    assert not managed.inspected
    assert not any(call[0] == "remove" for call in managed.client.calls)
    assert not list(managed.config.paths.films_dir.glob("*"))
    assert not current(managed).get("import_plan")
    assert not current(managed).get("ingest_job_id")


@pytest.mark.parametrize("extension", [
    ".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm", ".MKV", ".MP4",
])
def test_flat_supported_containers_import_without_changing_bytes(managed, extension):
    relative = "Mirror.1975" + extension
    root = validate_download(managed, {relative: FILM})
    assert current(managed)["import_plan"]["source"] == relative
    assert managed.inspected == [root / relative]
    detach_download(managed)
    managed.runner.tick()
    item = current(managed)
    assert item["status"] == "ingest_queued"
    canonical = Path(item["film_path"])
    assert canonical.name == "Mirror (1975)" + extension.lower()
    assert canonical.read_bytes() == FILM
    assert not (root / relative).exists()


@pytest.mark.parametrize("relative", [
    "Mirror.1975.1080p/Mirror.1975.mkv",
    "Release/Video/Mirror.1975.mkv",
    "Collection/Mirror.1975.1080p/Video/Mirror.1975.mkv",
    "Mirror.1975.1080p/video.mkv",
    "Collection/Mirror.1975.1080p/Video/video.mkv",
])
def test_nested_release_finds_named_or_unambiguous_parent_named_film(managed, relative):
    root = validate_download(managed, {relative: FILM, "release.nfo": b"provenance"})
    assert current(managed)["import_plan"]["source"] == relative
    assert managed.inspected == [root / relative]


@pytest.mark.parametrize(("video", "subtitle"), [
    ("Mirror.1975.mkv", "Subs/English.srt"),
    ("Mirror.1975.1080p/Mirror.1975.mkv", "Mirror.1975.1080p/Subs/English.srt"),
    ("Collection/Mirror.1975.1080p/Video/Mirror.1975.mkv",
     "Collection/Mirror.1975.1080p/Subtitles/ENG.srt"),
    ("Release/Video/Mirror.1975.mkv", "Release/Subs/Mirror.1975.en.srt"),
    ("Wrapper/Mirror.1975/video.mkv", "Wrapper/Mirror.1975/Subs/ENG.srt"),
])
def test_nested_full_english_sidecar_is_copied_and_raw_track_preserved(managed, video, subtitle):
    root = validate_download(managed, {video: FILM, subtitle: SRT})
    assert current(managed)["import_plan"]["subtitle"] == subtitle
    detach_download(managed)
    managed.runner.tick()
    item = current(managed)
    assert item["status"] == "ingest_queued"
    assert Path(item["film_path"]).with_suffix(".en.srt").read_bytes() == SRT
    assert (root / subtitle).read_bytes() == SRT


def test_samples_trailers_and_larger_bonus_are_preserved_beside_main(managed):
    files = {
        "Mirror.1975/Mirror.1975.mkv": FILM,
        "Mirror.1975/Sample/Mirror.1975.sample.mkv": FILM[:100],
        "Mirror.1975/Mirror.1975.trailer.mp4": FILM[:200],
        "Mirror.1975/Extras/Long.featurette.mkv": FILM * 3,
        "Mirror.1975/Extras/Bonus.mp4": FILM * 2,
        "Mirror.1975/release.nfo": b"release notes",
    }
    root = validate_download(managed, files)
    selected = "Mirror.1975/Mirror.1975.mkv"
    assert current(managed)["import_plan"]["source"] == selected
    detach_download(managed)
    managed.runner.tick()
    assert Path(current(managed)["film_path"]).read_bytes() == FILM
    for relative, data in files.items():
        if relative != selected:
            assert (root / relative).read_bytes() == data


def test_requested_title_beats_larger_unmarked_other_film(managed):
    files = {"Mirror.1975.mkv": FILM, "Another.Movie.1975.mkv": FILM * 3}
    root = validate_download(managed, files)
    assert current(managed)["import_plan"]["source"] == "Mirror.1975.mkv"
    assert managed.inspected == [root / "Mirror.1975.mkv"]
    assert (root / "Another.Movie.1975.mkv").read_bytes() == FILM * 3


@pytest.mark.parametrize("names", [
    ("Mirror.1975.S01E01.mkv", "Mirror.1975.S01E02.mkv"),
    ("Mirror.1975.CD1.mkv", "Mirror.1975.CD2.mkv"),
    ("Mirror.1975/Disc.1/video.mkv", "Mirror.1975/Disc.2/video.mkv"),
    ("Mirror.1975.Part.1.mkv", "Mirror.1975.Part.2.mkv"),
    ("Mirror.1975.1080p.mkv", "Mirror.1975.2160p.mkv"),
])
def test_multiple_plausible_features_require_review_even_with_unequal_sizes(managed, names):
    files = {names[0]: FILM, names[1]: FILM * 3}
    root = complete_and_validate(managed, files)
    item = current(managed)
    assert item["status"] == "needs_review", item
    assert {v["relative_path"] for v in item["review"]["videos"]} == set(names)
    assert item["review"]["selected_video"] is None
    assert_no_import(managed)
    assert all((root / relative).read_bytes() == data for relative, data in files.items())


@pytest.mark.parametrize("relative", [
    "Mirror.1975.CD1.mkv", "Mirror.1975.S01E01.mkv", "Mirror.1975/Part.1/video.mkv",
    "Unrelated.Movie.1975.mkv", "Mirror.2016.mkv", "video.mkv",
    "Mirror.1975/Unrelated.Movie.1975.mkv",
    "Collection/Mirror.1975/Different.Movie.mkv",
    "Mirror.2016/Mirror.1975.mkv",
])
def test_lone_partial_wrong_or_unclear_title_requires_review(managed, relative):
    complete_and_validate(managed, {relative: FILM})
    assert current(managed)["status"] == "needs_review", current(managed)
    assert_no_import(managed)


def test_distinct_non_latin_titles_do_not_collapse_into_matching_empty_tokens(managed):
    managed.service.store.patch(managed.identity, title="Сталкер")
    complete_and_validate(managed, {"Зеркало.1975.mkv": FILM})
    assert current(managed)["status"] == "needs_review", current(managed)
    assert_no_import(managed)


def test_matching_non_latin_title_can_auto_import(managed):
    managed.service.store.patch(managed.identity, title="Зеркало")
    validate_download(managed, {"Зеркало.1975.mkv": FILM})
    assert current(managed)["import_plan"]["source"] == "Зеркало.1975.mkv"


def test_reviewed_alternate_imports_only_the_explicit_selection(managed):
    files = {"feature-one.mkv": FILM, "feature-two.mkv": FILM * 2}
    root = complete_and_validate(managed, files)
    item = current(managed)
    assert item["status"] == "needs_review"
    managed.service.review(item["id"], item["revision"], "feature-one.mkv", {"action": "skip"})
    managed.runner.tick()
    assert current(managed)["import_plan"]["source"] == "feature-one.mkv"
    detach_download(managed)
    managed.runner.tick()
    assert current(managed)["status"] == "ingest_queued"
    assert Path(current(managed)["film_path"]).read_bytes() == FILM
    assert (root / "feature-two.mkv").read_bytes() == FILM * 2


@pytest.mark.parametrize("defect", ["file_progress", "file_size", "missing_file", "empty_inventory"])
def test_incomplete_inventory_never_imports_even_when_torrent_reports_complete(managed, defect):
    root = fill_download(managed, {"Mirror.1975.mkv": FILM})
    if defect == "file_progress":
        managed.client.inventory[0]["progress"] = 0.99
    elif defect == "file_size":
        managed.client.inventory[0]["size"] += 1
    elif defect == "missing_file":
        managed.client.inventory.append({"name": "missing.nfo", "size": 10, "progress": 1})
    else:
        managed.client.inventory = []
    managed.runner.tick()
    assert current(managed)["status"] == "validating"
    managed.client.record["state"] = "stoppedUP"
    managed.runner.tick()
    assert current(managed)["status"] == "failed", current(managed)
    assert_no_import(managed)
    assert (root / "Mirror.1975.mkv").read_bytes() == FILM


def test_unfinished_torrent_waits_even_when_video_has_its_final_size(managed):
    root = fill_download(managed, {"Mirror.1975.mkv": FILM})
    managed.client.record.update(progress=0.99, state="downloading")
    managed.runner.tick()
    assert current(managed)["status"] == "downloading"
    assert_no_import(managed)
    assert (root / "Mirror.1975.mkv").read_bytes() == FILM


@pytest.mark.parametrize("extension", [
    ".rar", ".zip", ".7z", ".iso", ".vob", ".m2ts", ".ts", ".wmv", ".flv",
])
def test_archives_disc_structures_and_unsupported_containers_are_not_imported(managed, extension):
    relative = "Mirror.1975" + extension
    root = complete_and_validate(managed, {relative: FILM, "Mirror.1975.en.srt": SRT})
    item = current(managed)
    assert item["status"] == "failed", item
    assert "supported video" in item["error"]
    assert_no_import(managed)
    assert (root / relative).read_bytes() == FILM
    assert (root / "Mirror.1975.en.srt").read_bytes() == SRT


def test_archive_with_only_playable_sample_requires_review(managed):
    files = {"Mirror.1975.rar": FILM * 3, "Mirror.1975.sample.mkv": FILM[:100]}
    root = complete_and_validate(managed, files)
    assert current(managed)["status"] == "needs_review"
    assert_no_import(managed)
    assert all((root / relative).read_bytes() == data for relative, data in files.items())


def test_archive_beside_complete_feature_stays_untouched(managed):
    files = {"Mirror.1975.mkv": FILM, "Mirror.1975.rar": FILM * 3}
    root = validate_download(managed, files)
    assert current(managed)["import_plan"]["source"] == "Mirror.1975.mkv"
    detach_download(managed)
    managed.runner.tick()
    assert Path(current(managed)["film_path"]).read_bytes() == FILM
    assert (root / "Mirror.1975.rar").read_bytes() == FILM * 3
