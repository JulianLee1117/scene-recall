"""Generated clips resolve like film shots without joining the library (ADR-0109)."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from pipeline.index.writer import open_db, table_names
from pipeline.lab import generated
from pipeline.lab.media import resolve_film, validate_sources


def _clip(path: Path, color="red", seconds=1.5):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color={color}:size=128x72:rate=24:duration={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_register_copies_by_content_and_resolves_as_a_source(tmp_path, config):
    db = open_db(config)
    source = _clip(tmp_path / "push.mp4")
    row = generated.register(config, db, source, "Push into the TV", {"provider": "runway", "model": "seedance2_fast"})
    assert row["film_id"].startswith("gen-") and len(row["film_id"]) == 24
    assert Path(row["path"]).parent == config.paths.state_dir / "lab" / "generated"
    assert Path(row["path"]).read_bytes() == source.read_bytes()
    assert abs(row["duration"] - 1.5) < 0.05 and row["fps"] == 24
    assert resolve_film(db, row["film_id"]) == row
    assert generated.register(config, db, source, "Again", {}) == row
    stored = db.open_table(generated.TABLE).to_arrow().to_pylist()
    assert len(stored) == 1 and json.loads(stored[0]["provenance"])["model"] == "seedance2_fast"
    # The library never sees generated rows.
    assert "films" not in table_names(db)
    with pytest.raises(ValueError, match="Generated source gen-missing"):
        resolve_film(db, "gen-missing")


def test_documents_can_place_generated_clips(tmp_path, config):
    db = open_db(config)
    row = generated.register(config, db, _clip(tmp_path / "push.mp4"), "Push", {})
    clip = {"id": "c1", "film_id": row["film_id"], "title": "Push", "source_start": 0.0, "source_end": 1.0}
    document = validate_sources({"clips": [clip]}, db, None, require_media=True)
    assert document["clips"][0]["film_id"] == row["film_id"]
    with pytest.raises(ValueError, match="extends beyond"):
        validate_sources({"clips": [{**clip, "source_end": 3.0}]}, db, None)


def test_api_streams_a_generated_source(tmp_path, config):
    from pipeline.api import main

    db = open_db(config)
    row = generated.register(config, db, _clip(tmp_path / "push.mp4"), "Push", {})
    with (
        patch.object(main, "load_config", return_value=config),
        patch.object(main, "open_db", return_value=db),
        patch.object(main, "ensure_search_indexes"),
        TestClient(main.app) as client,
    ):
        assert client.get(f"/video/{row['film_id']}/playback").json() == {"url": f"/video/{row['film_id']}"}
        assert client.get(f"/video/{row['film_id']}").content == Path(row["path"]).read_bytes()
        assert client.get("/video/gen-0000000000000000000a").status_code == 404
