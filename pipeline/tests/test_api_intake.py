"""Focused tests for the lightweight film-intake API and ingest FIFO."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline.config import Config
from pipeline.tests.subtitle_helpers import full_english_srt


@contextmanager
def _api_client(config: Config, *, ingest_runner=None):
    from fastapi.testclient import TestClient

    patches = [
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=MagicMock()),
        patch("pipeline.api.main.ensure_search_indexes"),
    ]
    if ingest_runner is not None:
        patches.append(
            patch("pipeline.api.main._run_ingest_subprocess", ingest_runner)
        )
    with patches[0], patches[1], patches[2]:
        if len(patches) == 4:
            with patches[3]:
                import pipeline.api.main as api_mod

                with TestClient(api_mod.app) as client:
                    yield client
        else:
            import pipeline.api.main as api_mod

            with TestClient(api_mod.app) as client:
                yield client


def _usable_external_srt(prefix: str = "Ordinary dialogue") -> str:
    return "\n".join(
        f"{index}\n"
        f"00:00:{index * 2 - 1:02d},000 --> 00:00:{index * 2:02d},000\n"
        f"{prefix} line {index} has several spoken words.\n"
        for index in range(1, 9)
    ) + "\n"


def _promo_only_srt() -> str:
    promotions = (
        "Official YIFY movies site: YTS.MX",
        "Downloaded from www.OpenSubtitles.org",
        "Support us and become a VIP member",
        "Remove all ads at https://example.invalid",
    )
    return "\n".join(
        f"{index}\n"
        f"00:00:{index * 2 - 1:02d},000 --> 00:00:{index * 2:02d},000\n"
        f"{text}\n"
        for index, text in enumerate(promotions, start=1)
    ) + "\n"


def test_incoming_groups_release_folder_and_selects_largest_video(
    config: Config,
) -> None:
    incoming = config.paths.incoming_dir
    release = incoming / "The.Lighthouse.2019.1080p.BluRay"
    (release / "Featurettes").mkdir(parents=True)
    primary = release / "The.Lighthouse.2019.mkv"
    primary.write_bytes(b"main feature")
    (release / "Featurettes" / "Making Of.mp4").write_bytes(b"extra")
    (release / "trailer.mov").write_bytes(b"x")
    direct = incoming / "Marty.Supreme.2025.1080p.WEB-DL.mp4"
    direct.write_bytes(b"direct")

    with _api_client(config) as client:
        response = client.get("/incoming")

    assert response.status_code == 200
    by_title = {item["suggested_title"]: item for item in response.json()}
    lighthouse = by_title["The Lighthouse"]
    assert lighthouse == {
        "relative_path": (
            "The.Lighthouse.2019.1080p.BluRay/The.Lighthouse.2019.mkv"
        ),
        "filename": primary.name,
        "size_gb": 0.0,
        "suggested_title": "The Lighthouse",
        "suggested_year": 2019,
        "suggested_edition": None,
        "suggested_filename": "The Lighthouse (2019).mkv",
        "extra_video_count": 2,
        "subtitle_review_candidates": [],
    }
    assert by_title["Marty Supreme"]["suggested_filename"] == (
        "Marty Supreme (2025).mp4"
    )


def test_import_requires_confirmation_and_rejects_traversal(config: Config) -> None:
    incoming = config.paths.incoming_dir
    incoming.mkdir(parents=True)
    source = incoming / "Film.2020.mkv"
    source.write_bytes(b"film")
    outside = incoming.parent / "outside.mkv"
    outside.write_bytes(b"outside")
    base = {
        "title": "Film",
        "year": 2020,
        "edition": None,
        "ingest": False,
    }

    with _api_client(config) as client:
        unconfirmed = client.post(
            "/films/import",
            json={**base, "relative_path": source.name, "confirm_finished": False},
        )
        traversal = client.post(
            "/films/import",
            json={**base, "relative_path": "../outside.mkv", "confirm_finished": True},
        )

    assert unconfirmed.status_code == 400
    assert traversal.status_code == 400
    assert source.exists()
    assert outside.exists()


def test_manual_intake_cannot_discover_or_import_managed_downloads(config: Config) -> None:
    incoming = config.paths.incoming_dir
    managed = incoming / ".scene-recall-managed" / ("a" * 32) / "data"
    managed.mkdir(parents=True)
    managed_source = managed / "Mirror.1975.mkv"
    managed_source.write_bytes(b"Torrent client still owns these bytes")
    manual_source = incoming / "Film.2020.mkv"
    manual_source.write_bytes(b"Manual intake remains available")

    with _api_client(config) as client:
        response = client.get("/incoming")
        assert response.status_code == 200
        assert [item["relative_path"] for item in response.json()] == [manual_source.name]
        for prefix in (".scene-recall-managed", ".SCENE-RECALL-MANAGED"):
            blocked = client.post("/films/import", json={
                "relative_path": f"{prefix}/{'a' * 32}/data/{managed_source.name}",
                "title": "Mirror", "year": 1975, "confirm_finished": True, "ingest": False,
            })
            assert blocked.status_code == 409
            assert "managed download queue" in blocked.json()["detail"]

    assert managed_source.read_bytes() == b"Torrent client still owns these bytes"
    assert manual_source.read_bytes() == b"Manual intake remains available"
    assert not (config.paths.films_dir / "Mirror (1975).mkv").exists()


def test_import_sanitizes_name_moves_file_and_suppresses_release_extras(
    config: Config,
) -> None:
    release = config.paths.incoming_dir / "Alien.Resurrection.1997"
    release.mkdir(parents=True)
    source = release / "feature.mkv"
    source.write_bytes(b"feature")
    extra = release / "behind-the-scenes.mp4"
    extra.write_bytes(b"extra")

    with _api_client(config) as client:
        response = client.post(
            "/films/import",
            json={
                "relative_path": "Alien.Resurrection.1997/feature.mkv",
                "title": "Alien: Resurrection?",
                "year": 1997,
                "edition": "Director's Cut",
                "ingest": False,
                "confirm_finished": True,
            },
        )
        after = client.get("/incoming")
        extra_import = client.post(
            "/films/import",
            json={
                "relative_path": "Alien.Resurrection.1997/behind-the-scenes.mp4",
                "title": "Behind the Scenes",
                "year": 1997,
                "ingest": False,
                "confirm_finished": True,
            },
        )

    assert response.status_code == 200
    assert response.json()["filename"] == (
        "Alien - Resurrection (1997) [Director's Cut].mkv"
    )
    destination = config.paths.films_dir / response.json()["filename"]
    assert destination.read_bytes() == b"feature"
    assert not source.exists()
    assert extra.exists()
    assert after.status_code == 200
    assert after.json() == []
    assert extra_import.status_code == 409


def test_import_preserves_best_english_srt_as_canonical_sidecar(
    config: Config,
    monkeypatch,
) -> None:
    release = config.paths.incoming_dir / "Singin.in.the.Rain.1952.1080p"
    source = release / "Singin.in.the.Rain.1952.mp4"
    subtitles = release / "Subs"
    subtitles.mkdir(parents=True)
    source.write_bytes(b"feature")
    english = subtitles / "Singin.in.the.Rain.1952.Eng.srt"
    english_content = full_english_srt()
    english.write_text(english_content, encoding="utf-8")
    commentary = subtitles / "Singin.in.the.Rain.1952.English.Commentary.srt"
    commentary.write_text(
        _usable_external_srt("Director commentary"), encoding="utf-8"
    )

    monkeypatch.setattr("pipeline.api.main._probe_intake_duration", lambda path: 6000)
    with _api_client(config) as client:
        response = client.post(
            "/films/import",
            json={
                "relative_path": (
                    "Singin.in.the.Rain.1952.1080p/"
                    "Singin.in.the.Rain.1952.mp4"
                ),
                "title": "Singin in the Rain",
                "year": 1952,
                "ingest": False,
                "confirm_finished": True,
            },
        )

    assert response.status_code == 200
    assert response.json()["subtitle_filename"] == (
        "Singin in the Rain (1952).en.srt"
    )
    imported = config.paths.films_dir / response.json()["subtitle_filename"]
    assert imported.read_text(encoding="utf-8") == english_content
    assert english.exists(), "the incoming subtitle remains as raw release evidence"


@pytest.mark.parametrize("decision", [None, {"action": "auto"}, {"action": "use_as_english", "relative_path": "Film.2020/Film.2020.srt"}])
def test_import_validates_unmarked_english_after_lightweight_inventory(config, monkeypatch, decision):
    release = config.paths.incoming_dir / "Film.2020"
    release.mkdir(parents=True)
    source = release / "Film.2020.mkv"
    source.write_bytes(b"film")
    subtitle = release / "Film.2020.srt"
    raw = full_english_srt().encode()
    subtitle.write_bytes(raw)
    probe = MagicMock(return_value=6000)
    monkeypatch.setattr("pipeline.api.main._probe_intake_duration", probe)
    with _api_client(config) as client:
        listing = client.get("/incoming")
        assert listing.json()[0]["subtitle_review_candidates"]
        probe.assert_not_called()
        response = client.post("/films/import", json={
            "relative_path": "Film.2020/Film.2020.mkv", "title": "Film", "year": 2020,
            "confirm_finished": True, "ingest": False, "subtitle_decision": decision,
        })
    assert response.status_code == 200, response.text
    probe.assert_called_once_with(source)
    assert (config.paths.films_dir / "Film (2020).en.srt").read_bytes() == raw
    assert subtitle.read_bytes() == raw


@pytest.mark.parametrize("kind", ["sparse", "foreign", "malformed", "out_of_bounds", "unknown_duration", "ambiguous"])
def test_automatic_import_uses_fallback_when_external_track_does_not_pass(config, monkeypatch, kind):
    release = config.paths.incoming_dir / "Film.2020"
    release.mkdir(parents=True)
    source = release / "Film.2020.mkv"
    source.write_bytes(b"film")
    subtitle = release / "Film.2020.en.srt"
    raw = full_english_srt()
    if kind == "sparse":
        raw = _usable_external_srt()
    elif kind == "foreign":
        raw = raw.replace("You know what they said about the house. We should come back with your friend at number", "No puedo venir a casa porque tengo que trabajar con mi amigo numero")
    elif kind == "malformed":
        raw += "121\nthis is not a timestamp\ntext\n\n"
    elif kind == "out_of_bounds":
        raw += "121\n03:00:00,000 --> 03:00:02,000\nYou know what they said.\n\n"
    elif kind == "ambiguous":
        (release / "Film.2020.eng.srt").write_text(raw.replace("house", "garden"), encoding="utf-8")
    subtitle.write_text(raw, encoding="utf-8")
    expected = subtitle.read_bytes()
    monkeypatch.setattr("pipeline.api.main._probe_intake_duration", lambda path: None if kind == "unknown_duration" else 6000)
    with _api_client(config) as client:
        response = client.post("/films/import", json={
            "relative_path": "Film.2020/Film.2020.mkv", "title": "Film", "year": 2020,
            "confirm_finished": True, "ingest": False, "subtitle_decision": {"action": "auto"},
        })
    assert response.status_code == 200, response.text
    assert response.json()["subtitle_filename"] is None
    assert subtitle.read_bytes() == expected
    assert not (config.paths.films_dir / "Film (2020).en.srt").exists()


def test_explicit_choice_cannot_bypass_subtitle_time_bounds(config, monkeypatch):
    incoming = config.paths.incoming_dir
    incoming.mkdir(parents=True)
    source = incoming / "Film.2020.mkv"
    source.write_bytes(b"film")
    subtitle = incoming / "Film.2020.srt"
    subtitle.write_text(full_english_srt(), encoding="utf-8")
    monkeypatch.setattr("pipeline.api.main._probe_intake_duration", lambda path: 100)
    with _api_client(config) as client:
        response = client.post("/films/import", json={
            "relative_path": source.name, "title": "Film", "year": 2020,
            "confirm_finished": True, "ingest": False,
            "subtitle_decision": {"action": "use_as_english", "relative_path": subtitle.name},
        })
    assert response.status_code == 409
    assert source.exists() and subtitle.exists()
    assert not (config.paths.films_dir / "Film (2020).mkv").exists()


def test_unmarked_sidecar_requires_explicit_review_before_import(
    config: Config,
) -> None:
    release = config.paths.incoming_dir / "The.Master.2012.1080p"
    release.mkdir(parents=True)
    source = release / "The.Master.2012.mp4"
    source.write_bytes(b"feature")
    subtitle = release / "The.Master.2012.YIFY.srt"
    subtitle_content = _usable_external_srt("English dialogue")
    subtitle.write_text(subtitle_content, encoding="utf-8")
    relative_source = "The.Master.2012.1080p/The.Master.2012.mp4"
    relative_subtitle = "The.Master.2012.1080p/The.Master.2012.YIFY.srt"

    with _api_client(config) as client:
        listing = client.get("/incoming")
        response = client.post(
            "/films/import",
            json={
                "relative_path": relative_source,
                "title": "The Master",
                "year": 2012,
                "ingest": False,
                "confirm_finished": True,
            },
        )

    assert listing.status_code == 200
    assert listing.json()[0]["subtitle_review_candidates"] == [
        {
            "relative_path": relative_subtitle,
            "filename": subtitle.name,
            "excerpt": (
                "English dialogue line 1 has several spoken words. · "
                "English dialogue line 2 has several spoken words. · "
                "English dialogue line 3 has several spoken words."
            ),
            "validation": "English dialogue could not be established throughout the file. Film duration will be checked when adding the film.",
        }
    ]
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Choose Automatic to check subtitles, select a known English track, or skip subtitles"
    )
    assert source.read_bytes() == b"feature"
    assert subtitle.read_text(encoding="utf-8") == subtitle_content
    assert not (release / ".scene-recall-imported").exists()
    assert not (config.paths.films_dir / "The Master (2012).mp4").exists()
    assert not (config.paths.films_dir / "The Master (2012).en.srt").exists()


def test_generic_english_label_in_matching_release_is_review_only(
    config: Config,
) -> None:
    release = config.paths.incoming_dir / "Hiroshima.Mon.Amour.1959.1080p"
    subtitles = release / "Subs"
    featurettes = release / "Featurettes"
    subtitles.mkdir(parents=True)
    featurettes.mkdir()
    source = release / "Hiroshima.Mon.Amour.1959.mkv"
    source.write_bytes(b"feature")
    english = subtitles / "ENG.srt"
    english.write_text(_usable_external_srt("English dialogue"), encoding="utf-8")
    (subtitles / "DUT.srt").write_text(
        _usable_external_srt("Dutch dialogue"), encoding="utf-8"
    )
    (subtitles / "Different.Movie.English.srt").write_text(
        _usable_external_srt("Other movie"), encoding="utf-8"
    )
    (featurettes / "ENG.srt").write_text(
        _usable_external_srt("Bonus dialogue"), encoding="utf-8"
    )

    with _api_client(config) as client:
        listing = client.get("/incoming")

    assert listing.status_code == 200
    candidates = listing.json()[0]["subtitle_review_candidates"]
    assert [candidate["relative_path"] for candidate in candidates] == [
        "Hiroshima.Mon.Amour.1959.1080p/Subs/ENG.srt"
    ]
    assert candidates[0]["filename"] == english.name


def test_import_uses_explicitly_confirmed_unmarked_sidecar(
    config: Config,
) -> None:
    release = config.paths.incoming_dir / "The.Master.2012.1080p"
    release.mkdir(parents=True)
    source = release / "The.Master.2012.mp4"
    source.write_bytes(b"feature")
    subtitle = release / "The.Master.2012.YIFY.srt"
    subtitle.write_text(_usable_external_srt("English dialogue"), encoding="utf-8")
    relative_subtitle = "The.Master.2012.1080p/The.Master.2012.YIFY.srt"

    with _api_client(config) as client:
        response = client.post(
            "/films/import",
            json={
                "relative_path": "The.Master.2012.1080p/The.Master.2012.mp4",
                "title": "The Master",
                "year": 2012,
                "ingest": False,
                "confirm_finished": True,
                "subtitle_decision": {
                    "action": "use_as_english",
                    "relative_path": relative_subtitle,
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["subtitle_filename"] == "The Master (2012).en.srt"
    imported_film = config.paths.films_dir / "The Master (2012).mp4"
    imported_subtitle = config.paths.films_dir / "The Master (2012).en.srt"
    assert imported_film.read_bytes() == b"feature"
    assert not source.exists()
    assert imported_subtitle.read_bytes() == subtitle.read_bytes()
    assert subtitle.exists(), "the selected release subtitle remains raw evidence"


def test_import_can_explicitly_skip_reviewable_sidecar(config: Config) -> None:
    release = config.paths.incoming_dir / "Film.2020"
    release.mkdir(parents=True)
    source = release / "Film.2020.mkv"
    source.write_bytes(b"feature")
    subtitle = release / "Film.2020.srt"
    subtitle_content = _usable_external_srt()
    subtitle.write_text(subtitle_content, encoding="utf-8")

    with _api_client(config) as client:
        response = client.post(
            "/films/import",
            json={
                "relative_path": "Film.2020/Film.2020.mkv",
                "title": "Film",
                "year": 2020,
                "ingest": False,
                "confirm_finished": True,
                "subtitle_decision": {"action": "skip"},
            },
        )

    assert response.status_code == 200
    assert response.json()["subtitle_filename"] is None
    assert (config.paths.films_dir / "Film (2020).mkv").read_bytes() == b"feature"
    assert not (config.paths.films_dir / "Film (2020).en.srt").exists()
    assert subtitle.read_text(encoding="utf-8") == subtitle_content


def test_import_recomputes_and_exactly_validates_subtitle_selection(
    config: Config,
) -> None:
    incoming = config.paths.incoming_dir
    release = incoming / "Film.2020"
    release.mkdir(parents=True)
    source = release / "Film.2020.mkv"
    source.write_bytes(b"feature")
    subtitle = release / "Film.2020.srt"
    subtitle.write_text(_usable_external_srt(), encoding="utf-8")
    other_release = incoming / "Other.2021"
    other_release.mkdir()
    other_subtitle = other_release / "Other.2021.srt"
    other_subtitle.write_text(_usable_external_srt(), encoding="utf-8")

    request = {
        "relative_path": "Film.2020/Film.2020.mkv",
        "title": "Film",
        "year": 2020,
        "ingest": False,
        "confirm_finished": True,
    }
    with _api_client(config) as client:
        wrong_release = client.post(
            "/films/import",
            json={
                **request,
                "subtitle_decision": {
                    "action": "use_as_english",
                    "relative_path": "Other.2021/Other.2021.srt",
                },
            },
        )
        subtitle.write_text(_promo_only_srt(), encoding="utf-8")
        stale_selection = client.post(
            "/films/import",
            json={
                **request,
                "subtitle_decision": {
                    "action": "use_as_english",
                    "relative_path": "Film.2020/Film.2020.srt",
                },
            },
        )

    assert wrong_release.status_code == 409
    assert stale_selection.status_code == 409
    assert source.read_bytes() == b"feature"
    assert other_subtitle.exists()
    assert not (release / ".scene-recall-imported").exists()
    assert not (config.paths.films_dir / "Film (2020).mkv").exists()
    assert not (config.paths.films_dir / "Film (2020).en.srt").exists()


def test_sidecar_selection_declines_forced_or_ambiguous_english_tracks(
    config: Config,
) -> None:
    from pipeline.intake import resolve_external_sidecars

    incoming = config.paths.incoming_dir
    release = incoming / "Film.2020"
    release.mkdir(parents=True)
    source = release / "Film.2020.mkv"
    source.write_bytes(b"feature")
    forced = release / "Film.2020.en.forced.srt"
    forced.write_text(_usable_external_srt("Forced subtitle"), encoding="utf-8")

    assert resolve_external_sidecars(incoming, source, release)[0] is None

    forced.unlink()
    (release / "Film.2020.en.srt").write_text(
        _usable_external_srt("First subtitle"), encoding="utf-8"
    )
    (release / "Film.2020.eng.srt").write_text(
        _usable_external_srt("Second subtitle"), encoding="utf-8"
    )

    assert resolve_external_sidecars(incoming, source, release)[0] is None


@pytest.mark.parametrize(
    "subtitle_name",
    [
        "Film.2020.srt",
        "Film.2020.French.srt",
        "Different.Movie.English.srt",
        "Film.2020.Featurette.English.srt",
        "Film.2020.English.Spanish.srt",
    ],
)
def test_sidecar_selection_requires_source_association_and_english_evidence(
    config: Config,
    subtitle_name: str,
) -> None:
    from pipeline.intake import resolve_external_sidecars

    incoming = config.paths.incoming_dir
    release = incoming / "Film.2020"
    release.mkdir(parents=True)
    source = release / "Film.2020.mkv"
    source.write_bytes(b"feature")
    subtitle = release / subtitle_name
    subtitle.write_text(_usable_external_srt(), encoding="utf-8")

    assert resolve_external_sidecars(incoming, source, release)[0] is None


def test_import_rejects_trivial_promo_only_srt_but_preserves_release_copy(
    config: Config,
) -> None:
    release = config.paths.incoming_dir / "Parasite.2019.1080p"
    release.mkdir(parents=True)
    source = release / "Parasite.2019.mkv"
    source.write_bytes(b"feature")
    promo = release / "Parasite.2019.en.srt"
    promo.write_text(_promo_only_srt(), encoding="utf-8")

    with _api_client(config) as client:
        response = client.post(
            "/films/import",
            json={
                "relative_path": "Parasite.2019.1080p/Parasite.2019.mkv",
                "title": "Parasite",
                "year": 2019,
                "ingest": False,
                "confirm_finished": True,
            },
        )

    assert response.status_code == 200
    assert response.json()["subtitle_filename"] is None
    assert promo.exists(), "rejected raw evidence remains in the release"
    assert not (config.paths.films_dir / "Parasite (2019).en.srt").exists()


def test_copy_file_no_replace_preserves_a_racing_destination(
    tmp_path: Path,
) -> None:
    from pipeline.api.main import _copy_file_no_replace

    source = tmp_path / "source.srt"
    destination = tmp_path / "destination.srt"
    source.write_text("new", encoding="utf-8")
    destination.write_text("existing", encoding="utf-8")

    try:
        _copy_file_no_replace(source, destination)
    except FileExistsError:
        pass
    else:
        raise AssertionError("exclusive sidecar copy unexpectedly replaced a peer")

    assert destination.read_text(encoding="utf-8") == "existing"


def test_import_refuses_filename_collision_without_moving_source(
    config: Config,
) -> None:
    incoming = config.paths.incoming_dir
    films = config.paths.films_dir
    incoming.mkdir(parents=True)
    films.mkdir(parents=True)
    source = incoming / "new.mkv"
    source.write_bytes(b"new")
    destination = films / "Existing (2001).mkv"
    destination.write_bytes(b"old")

    with _api_client(config) as client:
        response = client.post(
            "/films/import",
            json={
                "relative_path": source.name,
                "title": "Existing",
                "year": 2001,
                "ingest": False,
                "confirm_finished": True,
            },
        )

    assert response.status_code == 409
    assert source.read_bytes() == b"new"
    assert destination.read_bytes() == b"old"


def test_canonical_filename_matches_frontend_invalid_character_preview() -> None:
    from pipeline.api.main import _canonical_film_filename

    assert _canonical_film_filename(
        "Alien: Resurrection?",
        1997,
        "Director|Cut",
        ".MKV",
    ) == "Alien - Resurrection (1997) [Director Cut].mkv"


def test_release_parser_preserves_numeric_titles_before_release_year() -> None:
    from pipeline.api.main import _release_suggestion

    assert _release_suggestion("2001.A.Space.Odyssey.1968.1080p.mkv")[:2] == (
        "2001 A Space Odyssey",
        1968,
    )
    assert _release_suggestion("1917.2019.2160p.mkv")[:2] == ("1917", 2019)
    assert _release_suggestion("Blade.Runner.2049.2017.BluRay.mkv")[:2] == (
        "Blade Runner 2049",
        2017,
    )


def test_incoming_symlink_cannot_escape_configured_root(config: Config) -> None:
    incoming = config.paths.incoming_dir
    incoming.mkdir(parents=True)
    outside = incoming.parent / "outside.mkv"
    outside.write_bytes(b"outside")
    link = incoming / "linked.mkv"
    try:
        link.symlink_to(outside)
    except OSError:
        import pytest

        pytest.skip("creating symlinks is not permitted on this Windows host")

    with _api_client(config) as client:
        listing = client.get("/incoming")
        imported = client.post(
            "/films/import",
            json={
                "relative_path": link.name,
                "title": "Outside",
                "year": 2020,
                "ingest": False,
                "confirm_finished": True,
            },
        )

    assert listing.status_code == 200
    assert listing.json() == []
    assert imported.status_code == 400
    assert outside.read_bytes() == b"outside"


def test_ingest_only_accepts_direct_library_files(config: Config) -> None:
    films = config.paths.films_dir
    direct = films / "Direct (2020).mkv"
    nested = films / "nested" / "Nested (2020).mkv"
    nested.parent.mkdir(parents=True)
    direct.write_bytes(b"direct")
    nested.write_bytes(b"nested")

    with _api_client(config, ingest_runner=lambda _path, _log: None) as client:
        accepted = client.post("/ingest", json={"path": str(direct)})
        rejected = client.post("/ingest", json={"path": str(nested)})

    assert accepted.status_code == 200
    assert accepted.json()["status"] in {"queued", "running", "done"}
    assert rejected.status_code == 400


def test_ingest_queue_is_durable_fifo_and_survives_api_close(tmp_path: Path) -> None:
    from pipeline.api.main import _IngestQueue
    from pipeline.lab.store import LabStore

    store = LabStore(tmp_path)
    store.initialize()
    queue = _IngestQueue(store)
    first = queue.enqueue(tmp_path / "First.mkv")
    second = queue.enqueue(tmp_path / "Second.mkv")
    queue.close()
    reopened = _IngestQueue(LabStore(tmp_path))
    assert [job["job_id"] for job in reopened.snapshots()] == [first["job_id"], second["job_id"]]
    claimed = store.claim()
    assert claimed["id"] == first["job_id"]
    assert reopened.snapshots()[1]["queue_position"] == 1
    store.finish(claimed["id"])
    assert store.claim()["id"] == second["job_id"]


def test_ingest_queue_deduplicates_active_canonical_path(tmp_path: Path) -> None:
    from pipeline.api.main import _DuplicateIngestError, _IngestQueue
    from pipeline.lab.store import LabStore

    store = LabStore(tmp_path)
    store.initialize()
    queue = _IngestQueue(store)
    film = tmp_path / "Film.mkv"
    job = queue.enqueue(film)
    with pytest.raises(_DuplicateIngestError):
        queue.enqueue(film.parent / "." / film.name)
    store.claim()
    with pytest.raises(_DuplicateIngestError):
        queue.enqueue(film)
    store.finish(job["job_id"])
    assert queue.enqueue(film)["status"] == "queued"


def test_ingest_queue_failure_does_not_block_next_film(config: Config) -> None:
    from pipeline.api.main import _IngestQueue
    from pipeline.lab.store import LabStore
    from pipeline.lab.worker import execute_job

    store = LabStore(config.paths.state_dir)
    store.initialize()
    queue = _IngestQueue(store)
    queue.enqueue(config.paths.films_dir / "First.mkv")
    queue.enqueue(config.paths.films_dir / "Second.mkv")
    order = []

    def runner(path, progress):
        order.append(path.name)
        progress("working")
        if path.name == "First.mkv":
            raise RuntimeError("mock ingest failed")

    execute_job(store.claim(), config, None, store, ingest_runner=runner)
    execute_job(store.claim(), config, None, store, ingest_runner=runner)
    assert order == ["First.mkv", "Second.mkv"]
    snapshots = queue.snapshots()
    assert [job["status"] for job in snapshots] == ["error", "done"]
    assert snapshots[0]["error"] == "mock ingest failed"
    assert snapshots[1]["log"][-1] == "working"


def test_import_preserves_explicit_sdh_when_regular_track_is_automatic(
    config: Config, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pipeline.intake import resolve_external_sidecars

    incoming = config.paths.incoming_dir
    release = incoming / "Film.1985.1080p"
    release.mkdir(parents=True)
    source = release / "Film.1985.mkv"
    source.write_bytes(b"feature")
    regular = release / "Film.1985.en.srt"
    regular.write_text(full_english_srt(), encoding="utf-8")
    sdh = release / "Film.1985.en.sdh.srt"
    sdh.write_text(full_english_srt().replace("You know", "[SPEAKING] You know"), encoding="utf-8")
    selected_bytes = sdh.read_bytes()
    automatic, _ = resolve_external_sidecars(incoming, source, release, media_duration=6000)
    assert automatic == regular
    monkeypatch.setattr("pipeline.api.main._probe_intake_duration", lambda _path: 6000.0)

    with _api_client(config) as client:
        listing = client.get("/incoming")
        assert sdh.name in {
            candidate["filename"]
            for candidate in listing.json()[0]["subtitle_review_candidates"]
        }
        response = client.post("/films/import", json={
            "relative_path": source.relative_to(incoming).as_posix(),
            "title": "Film", "year": 1985, "ingest": False, "confirm_finished": True,
            "subtitle_decision": {
                "action": "use_as_english",
                "relative_path": sdh.relative_to(incoming).as_posix(),
            },
        })

    assert response.status_code == 200, response.text
    canonical = config.paths.films_dir / "Film (1985).en.srt"
    assert canonical.read_bytes() == selected_bytes != regular.read_bytes()
    assert sdh.read_bytes() == selected_bytes


def test_import_rejects_source_replaced_during_subtitle_duration_probe(
    config: Config, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    incoming = config.paths.incoming_dir
    release = incoming / "Film.1985.1080p"
    release.mkdir(parents=True)
    source = release / "Film.1985.mkv"
    source.write_bytes(b"feature A")
    original_stat = source.stat()
    subtitle = release / "Film.1985.en.srt"
    subtitle.write_text(full_english_srt(), encoding="utf-8")
    subtitle_bytes = subtitle.read_bytes()

    def replace_while_probing(path: Path) -> float:
        replacement = release / "replacement.download"
        replacement.write_bytes(b"feature B")
        os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        replacement.replace(path)
        return 6000.0

    monkeypatch.setattr("pipeline.api.main._probe_intake_duration", replace_while_probing)
    with _api_client(config) as client:
        response = client.post("/films/import", json={
            "relative_path": source.relative_to(incoming).as_posix(),
            "title": "Film", "year": 1985, "ingest": True, "confirm_finished": True,
            "subtitle_decision": {"action": "auto"},
        })
        assert client.get("/ingest/jobs").json() == []

    assert response.status_code == 409, response.text
    assert "changed during validation" in response.json()["detail"]
    assert source.read_bytes() == b"feature B"
    assert subtitle.read_bytes() == subtitle_bytes
    assert {path.name for path in release.iterdir()} == {source.name, subtitle.name}
    assert not (config.paths.films_dir / "Film (1985).mkv").exists()
    assert not (config.paths.films_dir / "Film (1985).en.srt").exists()
