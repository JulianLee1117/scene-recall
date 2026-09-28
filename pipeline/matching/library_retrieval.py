"""Bounded library recall from existing indexed stills, never final cut evidence.

The nearest retained source still supplies one vector in the published legacy
visual space. Returned times are *seek proposals*, not decoded PTS, and a caller
must verify people, geometry and movement on native frames before publishing a
match. This adapter neither embeds video nor builds another library index.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from lancedb.expr import col, lit

from pipeline.index.writer import published_film_ids, require_visual_encoder_profile, table_names
from pipeline.lab.media import JobCancelled
from pipeline.search.candidates import frame_neighbors


CONTRACT = "match-library-indexed-still-proposals-v1"
FRAME_LIMIT = 600
SHOT_LIMIT = 200
SCREEN_LIMIT = 48
_TABLES = ("frames", "units", "films")
_UNIT_COLUMNS = ["unit_id", "film_id", "t_start", "t_end", "caption", "is_representative"]
_FRAME_COLUMNS = ["frame_id", "unit_id", "film_id", "timestamp", "timestamp_source", "path", "visual_encoder"]


def _cancel(cancelled):
    if cancelled():
        raise JobCancelled("Match search cancelled")


def _versions(db):
    return {name: int(db.open_table(name).version) for name in _TABLES}


def library_identity(config, db):
    """Freeze published table generations, vector space and available film scope.

    Only metadata columns are scanned. Frame vectors are read later for one
    source shot and through the bounded vector query. Reopening the tables at
    the end catches publication concurrent with this snapshot.
    """
    if not set(_TABLES).issubset(table_names(db)):
        raise ValueError("The indexed film library is not ready for Match search")
    before = _versions(db)
    try:
        require_visual_encoder_profile(db, config)
    except RuntimeError as exc:
        # Match's public validation boundary renders ValueError as an actionable
        # request error. Keep incompatible encoders unavailable, with the reason.
        raise ValueError(str(exc)) from exc
    tables = {name: {"version": before[name], "rows": int(db.open_table(name).count_rows())}
              for name in _TABLES}
    published = published_film_ids(db)
    rows = db.open_table("films").search().select(["film_id", "title", "path"]).limit(None).to_list()
    if len({row["film_id"] for row in rows}) != len(rows):
        raise ValueError("The indexed film library contains duplicate film identities")
    films = sorted(({"film_id": str(row["film_id"]), "title": str(row["title"])}
                    for row in rows if row["film_id"] in published and Path(row["path"]).is_file()),
                   key=lambda row: row["film_id"])
    if before != _versions(db):
        raise ValueError("The film index changed during Match search. Start a new search.")
    return {"contract": CONTRACT, "visual_encoder": config.models.visual_encoder,
            "tables": tables, "films": films, "film_count": len(films),
            "frame_count": tables["frames"]["rows"], "unit_count": tables["units"]["rows"]}


def _validate_identity(config, db, pinned):
    current = library_identity(config, db)
    if not isinstance(pinned, dict) or current != pinned:
        raise ValueError("The film index changed after this search was queued. Start a new search.")
    return current


def _any_of(field, values):
    # Balanced expression trees avoid recursion limits for a bounded 600-row
    # hydration even when each frame belongs to a different shot.
    expressions = [col(field) == lit(value) for value in sorted(set(values))]
    while len(expressions) > 1:
        expressions = [expressions[index] | expressions[index + 1]
                       if index + 1 < len(expressions) else expressions[index]
                       for index in range(0, len(expressions), 2)]
    return expressions[0]


def _source(db, supplied, anchor_time):
    identity = supplied.get("unit_id")
    if not isinstance(identity, str) or not identity:
        raise ValueError("Choose an indexed reference shot")
    rows = (db.open_table("units").search().where(col("unit_id") == lit(identity))
            .select(_UNIT_COLUMNS).limit(2).to_list())
    if len(rows) != 1:
        raise ValueError("The reference shot is no longer in the published index")
    current = rows[0]
    if any(supplied.get(key) != current[key] for key in ("film_id", "t_start", "t_end")):
        raise ValueError("The reference shot changed after selection")
    if not isinstance(anchor_time, (int, float)) or isinstance(anchor_time, bool) or not math.isfinite(anchor_time):
        raise ValueError("Choose a finite reference moment inside its indexed shot")
    if not current["t_start"] <= anchor_time < current["t_end"]:
        raise ValueError("Choose a reference moment inside its indexed shot")
    return current


def retrieve(config, db, source, anchor_time, options, pinned, cancelled=lambda: False):
    """Return at most 200 unique shot proposals from at most 600 frame hits.

    Film scope is applied before vector retrieval. A candidate must still have
    the required incoming footage at its indexed seed, not merely somewhere in
    its shot. No scene-level annotation is used as proof of person count.
    """
    _cancel(cancelled)
    identity = _validate_identity(config, db, pinned)
    source = _source(db, source, anchor_time)
    if options.get("reference", {}).get("unit_id", source["unit_id"]) != source["unit_id"]:
        raise ValueError("The reference selection does not match its indexed shot")
    available = {film["film_id"] for film in identity["films"]}
    if source["film_id"] not in available:
        raise ValueError("The reference film is unavailable")
    requested = set(options.get("film_ids") or [])
    if not requested.issubset(available):
        raise ValueError("The selected films are outside the available indexed library")
    allowed = requested or available
    include_source = options.get("include_source_film", True)
    if not include_source:
        allowed = allowed - {source["film_id"]}
    minimum = options.get("min_incoming_seconds", 1.0)
    if isinstance(minimum, bool) or not isinstance(minimum, (int, float)) or not math.isfinite(minimum) or minimum <= 0:
        raise ValueError("Required incoming footage must be finite and positive")

    reference_rows = (db.open_table("frames").search()
                      .where(col("unit_id") == lit(source["unit_id"]))
                      .select([*_FRAME_COLUMNS, "visual_vec"]).limit(None).to_list())
    reference_rows = [row for row in reference_rows
                      if row["film_id"] == source["film_id"]
                      and math.isfinite(row["timestamp"])
                      and source["t_start"] <= row["timestamp"] < source["t_end"]
                      and Path(row["path"]).is_file()]
    if not reference_rows:
        raise ValueError("No retained reference frame is available for library discovery")
    reference = min(reference_rows, key=lambda row: (abs(row["timestamp"] - anchor_time), row["frame_id"]))
    vector = np.asarray(reference["visual_vec"], dtype=np.float32)
    if vector.ndim != 1 or not len(vector) or not np.isfinite(vector).all() or float(np.linalg.norm(vector)) <= 1e-12:
        raise ValueError("The indexed reference vector is invalid; repair the frame index")
    _cancel(cancelled)
    if not allowed:
        _validate_identity(config, db, pinned)
        return []
    # Frame is_representative selects a shot's display thumbnail; its other
    # retained keyframes remain essential recall evidence.
    frame_filter = _any_of("film_id", allowed) & (col("unit_id") != lit(source["unit_id"]))
    # Same-film frames in an overlapping unsplit/derived unit cannot consume the
    # bounded query. Full unit bounds are checked again after hydration.
    if include_source and source["film_id"] in allowed:
        frame_filter = frame_filter & ((col("film_id") != lit(source["film_id"]))
                                      | (col("timestamp") < lit(source["t_start"]))
                                      | (col("timestamp") >= lit(source["t_end"])))
    frames = frame_neighbors(db, vector, columns=[*_FRAME_COLUMNS, "_distance"],
                             limit=FRAME_LIMIT, where=frame_filter)
    _cancel(cancelled)
    frames.sort(key=lambda row: (float(row.get("_distance", math.inf)), str(row["frame_id"])))
    unit_ids = {row["unit_id"] for row in frames}
    units = (db.open_table("units").search().where(_any_of("unit_id", unit_ids))
             .select(_UNIT_COLUMNS).limit(None).to_list()) if unit_ids else []
    if len({row["unit_id"] for row in units}) != len(units):
        raise ValueError("The indexed library contains duplicate shot identities")
    units = {row["unit_id"]: row for row in units}
    film_rows = (db.open_table("films").search().where(_any_of("film_id", allowed))
                 .select(["film_id", "title", "path"]).limit(None).to_list())
    films = {row["film_id"]: row for row in film_rows}
    reference_evidence = {"frame_id": reference["frame_id"], "time": float(reference["timestamp"]),
                          "path": reference["path"], "timestamp_source": reference["timestamp_source"]}
    result, seen = [], set()
    for rank, frame in enumerate(frames, 1):
        _cancel(cancelled)
        row, film = units.get(frame["unit_id"]), films.get(frame["film_id"])
        if (row is None or film is None or row["unit_id"] in seen or row["unit_id"] == source["unit_id"]
                or row["film_id"] != frame["film_id"] or frame["film_id"] not in allowed
                or not row["is_representative"] or not math.isfinite(float(frame.get("_distance", math.inf)))):
            continue
        timestamp = frame["timestamp"]
        if (not math.isfinite(timestamp) or not row["t_start"] <= timestamp < row["t_end"]
                or row["t_end"] - timestamp < minimum - 1e-6 or not Path(frame["path"]).is_file()
                or not Path(film["path"]).is_file()):
            continue
        if row["film_id"] == source["film_id"] and (not include_source or
                max(row["t_start"], source["t_start"]) < min(row["t_end"], source["t_end"])):
            continue
        if frame["visual_encoder"] != identity["visual_encoder"]:
            raise ValueError("The candidate frame uses an incompatible visual encoder")
        seen.add(row["unit_id"])
        result.append({key: row[key] for key in ("unit_id", "film_id", "t_start", "t_end", "caption")})
        result[-1].update(film_title=film["title"], film_path=film["path"], frame_id=frame["frame_id"],
                          path=frame["path"], time=float(timestamp), global_rank=rank,
                          reference_frame=dict(reference_evidence))
        if len(result) == SHOT_LIMIT:
            break
    _validate_identity(config, db, pinned)
    _cancel(cancelled)
    return result
