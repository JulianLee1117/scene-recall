"""Transactional durable projects, original music and a single local job ledger."""

from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from pipeline.lab.models import ProjectDocument


SEARCH_FEATURE_PAUSE_REASON = "Optional search preparation paused by operator; use search-features resume to continue"
_SEARCH_FEATURE_KINDS = ("prepare-search-features", "fit-search-composition")


class RevisionConflict(ValueError):
    pass


class DuplicateJob(ValueError):
    pass


class ActiveProjectJobs(ValueError):
    pass


class LabStore:
    def __init__(self, state_dir: Path, assets_dir: Path | None = None):
        self.root = Path(state_dir) / "lab"
        self.path = self.root / "lab.sqlite3"
        self.assets_dir = Path(assets_dir).absolute() if assets_dir is not None else None

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    def initialize(self):
        from filelock import FileLock

        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "tracks").mkdir(exist_ok=True)
        # SQLite's initial journal-mode transition can fail immediately rather
        # than honor busy_timeout when both new lanes open a fresh database.
        # Serialize only schema setup; normal claims retain SQLite transactions.
        with FileLock(self.root / ".schema.lock", timeout=30, preserve_lock_file=True), self.connection() as con:
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError(f"Unsupported Lab database version {version}")
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, experiment_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, document TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions (
                    project_id TEXT NOT NULL, revision INTEGER NOT NULL, name TEXT NOT NULL,
                    document TEXT NOT NULL, created_at REAL NOT NULL,
                    PRIMARY KEY(project_id, revision));
                CREATE TABLE IF NOT EXISTS tracks (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, duration REAL NOT NULL,
                    path TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, project_id TEXT, kind TEXT NOT NULL,
                    base_revision INTEGER, status TEXT NOT NULL, snapshot TEXT NOT NULL,
                    progress TEXT, error TEXT, result TEXT, created_at REAL NOT NULL,
                    started_at REAL, finished_at REAL, cancel_requested INTEGER NOT NULL DEFAULT 0,
                    path_key TEXT, log TEXT NOT NULL DEFAULT '[]');
                CREATE INDEX IF NOT EXISTS job_fifo ON jobs(status, created_at);
                CREATE INDEX IF NOT EXISTS job_discovery_key ON jobs(kind, path_key, created_at);
                CREATE TABLE IF NOT EXISTS artifact_cleanup (
                    job_id TEXT PRIMARY KEY, assets_root TEXT, created_at REAL NOT NULL,
                    error TEXT);
                PRAGMA user_version=1;
            """)

    @staticmethod
    def _project(row):
        if row is None:
            raise KeyError("Project not found")
        result = dict(row)
        result["document"] = json.loads(result["document"])
        return result

    def create_project(self, name, experiment_id, document=None):
        from pipeline.lab.registry import EXPERIMENTS
        if experiment_id not in {item["id"] for item in EXPERIMENTS}:
            raise ValueError("Unknown experiment")
        identity, now = str(uuid.uuid4()), time.time()
        document = ProjectDocument.model_validate(document if document is not None else {}).model_dump_json()
        with self.connection() as con:
            con.execute("INSERT INTO projects VALUES (?,?,?,?,?,?,?)",
                        (identity, name, experiment_id, 1, document, now, now))
            con.execute("INSERT INTO revisions VALUES (?,?,?,?,?)", (identity, 1, name, document, now))
        return self.get_project(identity)

    def get_project(self, identity):
        with self.connection() as con:
            return self._project(con.execute("SELECT * FROM projects WHERE id=?", (identity,)).fetchone())

    def list_projects(self):
        with self.connection() as con:
            return [self._project(row) for row in con.execute("""
                SELECT projects.*, (
                    SELECT COUNT(*) FROM jobs
                    WHERE project_id=projects.id AND status IN ('queued','running')
                ) AS active_job_count
                FROM projects ORDER BY updated_at DESC
            """)]

    def delete_project(self, identity, base_revision):
        """Delete records atomically and durably schedule their owned files for removal."""
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            current = self._project(con.execute("SELECT * FROM projects WHERE id=?", (identity,)).fetchone())
            if current["revision"] != base_revision:
                raise RevisionConflict("Project changed; reload before deleting")
            if con.execute(
                "SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running')",
                (identity,),
            ).fetchone():
                raise ActiveProjectJobs("Cancel this project's active jobs and wait for them to stop before deleting")
            job_ids = [row[0] for row in con.execute("SELECT id FROM jobs WHERE project_id=?", (identity,))]
            con.executemany("INSERT INTO artifact_cleanup(job_id,assets_root,created_at) VALUES (?,?,?)",
                            [(job_id, str(self.assets_dir) if self.assets_dir else None, time.time()) for job_id in job_ids])
            con.execute("DELETE FROM jobs WHERE project_id=?", (identity,))
            con.execute("DELETE FROM revisions WHERE project_id=?", (identity,))
            con.execute("DELETE FROM projects WHERE id=?", (identity,))
        # File removal cannot be atomic with SQLite. A failed/locked file stays
        # in the durable cleanup queue and never turns a committed delete into
        # a misleading HTTP failure. The existing worker retries between jobs.
        from pipeline.lab.cleanup import drain_cleanup
        drain_cleanup(self, job_ids=job_ids)
        try:
            with self.connection() as con:
                pending = {row[0] for row in con.execute("SELECT job_id FROM artifact_cleanup")}
            cleanup_pending = bool(pending.intersection(job_ids))
        except sqlite3.Error:
            cleanup_pending = bool(job_ids)
        return {"deleted": identity, "cleanup_pending": cleanup_pending}

    def _update(self, con, identity, base_revision, document, name=None):
        current = self._project(con.execute("SELECT * FROM projects WHERE id=?", (identity,)).fetchone())
        if current["revision"] != base_revision:
            raise RevisionConflict("Project changed; reload before saving or generating again")
        old_document = current["document"]
        # Full saves also carry local Undo snapshots across song changes. Their
        # valid ranges are authoritative; explicit import actions reset ranges.
        document = ProjectDocument.model_validate(document).model_dump(mode="json")
        if (document["passage"] != old_document["passage"]
                or (document.get("track") or {}).get("id") != (old_document.get("track") or {}).get("id")):
            provenance = document.get("direction_plan") or {}
            if (provenance.get("passage") != document["passage"]
                    or provenance.get("track_id") != (document.get("track") or {}).get("id")):
                document["direction_plan"] = None
        revision, now = base_revision + 1, time.time()
        encoded = json.dumps(document, allow_nan=False)
        name = name or current["name"]
        con.execute("UPDATE projects SET name=?, revision=?, document=?, updated_at=? WHERE id=?",
                    (name, revision, encoded, now, identity))
        con.execute("INSERT INTO revisions VALUES (?,?,?,?,?)", (identity, revision, name, encoded, now))
        return {**current, "name": name, "revision": revision, "document": document, "updated_at": now}

    def update_project(self, identity, base_revision, document, name=None):
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            return self._update(con, identity, base_revision, document, name)

    def revisions(self, identity):
        self.get_project(identity)
        with self.connection() as con:
            return [dict(row) for row in con.execute(
                "SELECT revision,name,created_at FROM revisions WHERE project_id=? ORDER BY revision DESC", (identity,))]

    def restore(self, identity, base_revision, revision):
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM revisions WHERE project_id=? AND revision=?", (identity, revision)).fetchone()
            if row is None:
                raise KeyError("Revision not found")
            return self._update(con, identity, base_revision, json.loads(row["document"]), row["name"])

    def add_track(self, identity, name, duration, path):
        with self.connection() as con:
            con.execute("INSERT OR IGNORE INTO tracks VALUES (?,?,?,?,?)", (identity, name, duration, str(path), time.time()))
        return self.get_track(identity)

    def get_track(self, identity):
        with self.connection() as con:
            row = con.execute("SELECT * FROM tracks WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise KeyError("Track not found")
            return dict(row)

    @staticmethod
    def _job(row, *, private=False):
        if row is None:
            raise KeyError("Job not found")
        result = dict(row)
        from pipeline.lab.job_roles import role_for_kind
        result["worker_role"] = role_for_kind(result["kind"])
        result["result"] = json.loads(result["result"]) if result["result"] else None
        if private:
            result["snapshot"] = json.loads(result["snapshot"])
            result["document"] = result["snapshot"].get("document")
            result["log"] = json.loads(result["log"])
        else:
            # Lab progress contains bounded worker-authored messages, not the
            # request snapshot. Ingestion subprocess logs keep their own API.
            result["progress_steps"] = ([message for message in json.loads(result["log"]) if isinstance(message, str)][-80:]
                                        if result["kind"] != "ingest" else [])
            result["cancel_requested"] = bool(result["cancel_requested"])
            for field in ("snapshot", "path_key", "log"):
                result.pop(field, None)
        return result

    def enqueue(self, kind, project_id=None, base_revision=None, *, mode="preview", path=None, match=None, slot_ids=None, replan_timing=False, generate=None, suggest_only=False, next_scene=None, next_scene_preview=None):
        if (kind == "next-scene") != (next_scene is not None):
            raise ValueError("Next-scene jobs require their own options")
        if (kind == "next-scene-preview") != (next_scene_preview is not None):
            raise ValueError("Next-scene previews require a saved suggestion")
        if not isinstance(suggest_only, bool):
            raise ValueError("The alternatives option must be a boolean")
        if suggest_only and (kind != "draft" or not isinstance(slot_ids, list) or len(slot_ids) != 1):
            raise ValueError("Finding alternatives requires a draft job with exactly one selected music slot")
        if slot_ids is not None and kind not in {"plan", "draft", "generate"}:
            raise ValueError("Only music planning, drafting or generation jobs accept selected music slots")
        if generate is not None and kind != "generate":
            raise ValueError("Only generate jobs accept generation options")
        if replan_timing and kind not in {"rhythm", "analyze"}:
            raise ValueError("Only rhythm or analyze jobs can explicitly replan musical timing")
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            if kind == "ingest":
                canonical = Path(path).resolve()
                key = os.path.normcase(str(canonical))
                if con.execute("SELECT 1 FROM jobs WHERE path_key=? AND status IN ('queued','running')", (key,)).fetchone():
                    raise DuplicateJob("Already queued for ingestion")
                snapshot = {"path": str(canonical), "filename": canonical.name}
            else:
                project = self._project(con.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone())
                if project["revision"] != base_revision:
                    raise RevisionConflict("Project changed; save and start a new job")
                if con.execute("SELECT 1 FROM jobs WHERE project_id=? AND kind=? AND status IN ('queued','running')", (project_id, kind)).fetchone():
                    raise DuplicateJob("This project already has an active job of that kind")
                snapshot, key = {"document": project["document"], "experiment_id": project["experiment_id"], "mode": mode}, None
                if kind == "next-scene":
                    if project["experiment_id"] != "music-sketch":
                        raise ValueError("Next-scene suggestions belong to AI Music Video")
                    from pipeline.lab.models import NextSceneOptions
                    from pipeline.lab.next_scene import validate_request
                    options = NextSceneOptions.model_validate(next_scene).model_dump(mode="json")
                    snapshot["next_scene"] = options
                    snapshot["next_scene_scope"] = validate_request(project["document"], options)
                elif kind == "next-scene-preview":
                    from pipeline.lab.models import NextSceneAdjust
                    parent = self._job(con.execute("SELECT * FROM jobs WHERE id=?", (next_scene_preview["job_id"],)).fetchone(), private=True)
                    if parent["project_id"] != project_id or parent["kind"] != "next-scene" or parent["status"] != "completed":
                        raise ValueError("Preview must refer to a completed next-scene job in this project")
                    candidate = next((item for item in (parent["result"] or {}).get("candidates", [])
                                      if item["id"] == next_scene_preview["candidate_id"]), None)
                    if candidate is None:
                        raise ValueError("This next-scene suggestion is unavailable")
                    snapshot["next_scene_preview"] = {
                        "next_scene_job_id": parent["id"], "candidate": candidate,
                        "scope": parent["result"]["scope"], "document": parent["document"],
                        "adjustments": NextSceneAdjust.model_validate(next_scene_preview.get("adjustments") or {}).adjustment_payload(),
                    }
                if suggest_only:
                    if project["experiment_id"] != "music-sketch":
                        raise ValueError("Scene alternatives belong to AI Music Video")
                    snapshot["suggest_only"] = True
                if kind in {"rhythm", "analyze"}:
                    if replan_timing:
                        from pipeline.lab.timeline import require_replan_unlocked
                        require_replan_unlocked(project["document"])
                    snapshot["replan_timing"] = replan_timing
                if match is not None:
                    snapshot["match"] = match
                if kind == "generate":
                    if project["experiment_id"] != "music-sketch":
                        raise ValueError("Music edit generation belongs to AI Music Video")
                    from pipeline.lab.generation import validate_generate_request
                    mode_name, _targets = validate_generate_request(project["document"], generate, slot_ids)
                    snapshot["generate"] = {"mode": mode_name}
                    if slot_ids is not None:
                        snapshot["slot_ids"] = list(slot_ids)
                elif kind == "plan":
                    if project["experiment_id"] != "music-sketch":
                        raise ValueError("Shot direction planning belongs to AI Music Video")
                    from pipeline.lab.timeline import plan_targets
                    snapshot["slot_ids"] = [slot["id"] for slot in plan_targets(project["document"], slot_ids)]
                elif slot_ids is not None:
                    from pipeline.lab.timeline import draft_targets
                    draft_targets(project["document"], slot_ids)
                    snapshot["slot_ids"] = list(slot_ids)
            identity = str(uuid.uuid4())
            con.execute("INSERT INTO jobs (id,project_id,kind,base_revision,status,snapshot,created_at,path_key) VALUES (?,?,?,?,?,?,?,?)",
                        (identity, project_id, kind, base_revision, "queued", json.dumps(snapshot), time.time(), key))
        return self.get_job(identity)

    def get_job(self, identity, *, private=False):
        with self.connection() as con:
            return self._job(con.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone(), private=private)

    @staticmethod
    def _transition_render_snapshot(proposal):
        from pipeline.transitions.contracts import RENDERER_VERSION, RenderRequest
        proposal = {**proposal, "request": RenderRequest.model_validate(proposal["request"]).model_dump(mode="json")}
        if proposal["renderer_version"] != RENDERER_VERSION or len(proposal["sources"]) != 2:
            raise ValueError("Invalid transition render snapshot")
        encoded = json.dumps({"transition_render": proposal}, allow_nan=False, sort_keys=True)
        key = "transition-render:" + hashlib.sha256(encoded.encode()).hexdigest()
        return encoded, key

    def completed_transition_renders(self, proposal):
        """Bounded exact candidates; their files must be verified by the caller."""
        _encoded, key = self._transition_render_snapshot(proposal)
        with self.connection() as con:
            return [self._job(row, private=True) for row in con.execute(
                "SELECT * FROM jobs WHERE kind='transition-render' AND path_key=? AND status='completed' "
                "AND cancel_requested=0 ORDER BY created_at DESC,id DESC LIMIT 5", (key,))]

    def enqueue_transition_render(self, proposal, *, reusable=None):
        """Persist a validated recipe, or reuse a caller-verified immutable result."""
        encoded, key = self._transition_render_snapshot(proposal)
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            if reusable is not None:
                saved = con.execute("SELECT * FROM jobs WHERE id=? AND kind='transition-render' AND path_key=? "
                                    "AND status='completed' AND cancel_requested=0", (reusable["id"], key)).fetchone()
                if (saved is not None and saved["snapshot"] == encoded
                        and json.loads(saved["result"]) == reusable.get("result")):
                    return self._job(saved)
            previous = con.execute("SELECT * FROM jobs WHERE kind='transition-render' AND path_key=? AND status IN ('queued','running') AND cancel_requested=0", (key,)).fetchone()
            if previous is not None:
                return self._job(previous)
            identity = str(uuid.uuid4())
            con.execute("INSERT INTO jobs (id,kind,status,snapshot,created_at,path_key) VALUES (?,?,?,?,?,?)",
                        (identity, "transition-render", "queued", encoded, time.time(), key))
        return self.get_job(identity)

    def transition_renders(self, limit=20):
        with self.connection() as con:
            return [self._job(row, private=True) for row in con.execute(
                "SELECT * FROM jobs WHERE kind='transition-render' ORDER BY created_at DESC,id DESC LIMIT ?", (limit,))]

    @staticmethod
    def _algmods_snapshot(proposal):
        from pipeline.algmods.contracts import MODS_VERSION, RenderRequest
        proposal = {**proposal, "request": RenderRequest.model_validate(proposal["request"]).model_dump(mode="json")}
        if proposal["version"] != MODS_VERSION or "source" not in proposal:
            raise ValueError("Invalid Alg Mods render snapshot")
        encoded = json.dumps({"algmods_render": proposal}, allow_nan=False, sort_keys=True)
        key = "algmods-render:" + hashlib.sha256(encoded.encode()).hexdigest()
        return encoded, key

    def completed_algmods_renders(self, proposal):
        """Bounded exact candidates; their files must be verified by the caller."""
        _encoded, key = self._algmods_snapshot(proposal)
        with self.connection() as con:
            return [self._job(row, private=True) for row in con.execute(
                "SELECT * FROM jobs WHERE kind='algmods-render' AND path_key=? AND status='completed' "
                "AND cancel_requested=0 ORDER BY created_at DESC,id DESC LIMIT 5", (key,))]

    def enqueue_algmods_render(self, proposal, *, reusable=None):
        """Persist a validated treatment request, or reuse a caller-verified immutable result."""
        encoded, key = self._algmods_snapshot(proposal)
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            if reusable is not None:
                saved = con.execute("SELECT * FROM jobs WHERE id=? AND kind='algmods-render' AND path_key=? "
                                    "AND status='completed' AND cancel_requested=0", (reusable["id"], key)).fetchone()
                if (saved is not None and saved["snapshot"] == encoded
                        and json.loads(saved["result"]) == reusable.get("result")):
                    return self._job(saved)
            previous = con.execute("SELECT * FROM jobs WHERE kind='algmods-render' AND path_key=? AND status IN ('queued','running') AND cancel_requested=0", (key,)).fetchone()
            if previous is not None:
                return self._job(previous)
            identity = str(uuid.uuid4())
            con.execute("INSERT INTO jobs (id,kind,status,snapshot,created_at,path_key) VALUES (?,?,?,?,?,?)",
                        (identity, "algmods-render", "queued", encoded, time.time(), key))
        return self.get_job(identity)

    def algmods_renders(self, limit=20):
        with self.connection() as con:
            return [self._job(row, private=True) for row in con.execute(
                "SELECT * FROM jobs WHERE kind='algmods-render' ORDER BY created_at DESC,id DESC LIMIT ?", (limit,))]

    def enqueue_transition_bridge(self, proposal):
        """Attach a validated imported asset to its preallocated job identity."""
        from pipeline.transitions.bridges import BRIDGE_VERSION, BridgeImportRequest
        identity = proposal["job_id"]
        if str(uuid.UUID(identity)) != identity or proposal["bridge_version"] != BRIDGE_VERSION:
            raise ValueError("Invalid bridge render snapshot")
        request = BridgeImportRequest.model_validate(proposal["request"]).model_dump(mode="json")
        if (request != proposal["input"]["request"] or proposal["input"]["job_id"] != identity
                or len(proposal["source_snapshot"]["sources"]) != 2):
            raise ValueError("The bridge input does not belong to this request")
        proposal = {**proposal, "request": request}
        encoded = json.dumps({"transition_bridge": proposal}, allow_nan=False, sort_keys=True)
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            previous = con.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()
            if previous is not None:
                if previous["kind"] != "transition-bridge" or previous["snapshot"] != encoded:
                    raise ValueError("The bridge identity belongs to a different request")
                return self._job(previous)
            con.execute("INSERT INTO jobs (id,kind,status,snapshot,created_at,path_key) VALUES (?,?,?,?,?,?)",
                        (identity, "transition-bridge", "queued", encoded, time.time(),
                         "transition-bridge:" + request["parent_render_id"]))
        return self.get_job(identity)

    def transition_bridges(self, parent_render_id=None, limit=20):
        if not 1 <= limit <= 100:
            raise ValueError("Choose between 1 and 100 bridge renders")
        values = []
        clause = ""
        if parent_render_id is not None:
            if str(uuid.UUID(parent_render_id)) != parent_render_id:
                raise ValueError("Invalid parent render identity")
            clause = " AND path_key=?"
            values.append("transition-bridge:" + parent_render_id)
        with self.connection() as con:
            return [self._job(row, private=True) for row in con.execute(
                "SELECT * FROM jobs WHERE kind='transition-bridge'" + clause +
                " ORDER BY created_at DESC,id DESC LIMIT ?", (*values, limit))]

    def enqueue_transition_generation(self, proposal):
        """One explicit, quoted generation request owns one immutable job UUID."""
        from pipeline.transitions.generation import GENERATION_VERSION, GenerateRequest
        from pipeline.transitions.providers import request_identity
        identity = proposal["job_id"]
        request = GenerateRequest.model_validate(proposal["request"]).model_dump(mode="json")
        if (str(uuid.UUID(identity)) != identity or identity != request["request_id"]
                or proposal["generation_version"] != GENERATION_VERSION
                or proposal["parent_manifest_sha256"] != request["parent_manifest_sha256"]
                or len(proposal["source_snapshot"]["sources"]) != 2):
            raise ValueError("Invalid generation snapshot or quote identity")
        encoded = json.dumps({"transition_generation": {**proposal, "request": request}}, allow_nan=False, sort_keys=True)
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            previous = con.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()
            if previous is not None:
                saved = json.loads(previous["snapshot"])
                expected = json.loads(encoded)
                if previous["kind"] == "transition-generate":
                    for value in (saved, expected):
                        value["transition_generation"]["request"] = request_identity(value["transition_generation"]["request"])
                if previous["kind"] != "transition-generate" or saved != expected:
                    raise ValueError("The generation identity belongs to a different request")
                return self._job(previous)
            con.execute("INSERT INTO jobs (id,kind,status,snapshot,created_at,path_key) VALUES (?,?,?,?,?,?)",
                        (identity, "transition-generate", "queued", encoded, time.time(),
                         "transition-generate:" + request["parent_render_id"]))
        return self.get_job(identity)

    def transition_generations(self, parent_render_id=None, limit=20):
        if not 1 <= limit <= 100:
            raise ValueError("Choose between 1 and 100 generations")
        values, clause = [], ""
        if parent_render_id is not None:
            if str(uuid.UUID(parent_render_id)) != parent_render_id:
                raise ValueError("Invalid parent render identity")
            clause = " AND path_key=?"
            values.append("transition-generate:" + parent_render_id)
        with self.connection() as con:
            return [self._job(row, private=True) for row in con.execute(
                "SELECT * FROM jobs WHERE kind='transition-generate'" + clause +
                " ORDER BY created_at DESC,id DESC LIMIT ?", (*values, limit))]

    def enqueue_search_features(self, options):
        """One durable, resumable optional-feature job per film generation."""
        if (not isinstance(options, dict) or set(options) not in ({"film_id", "source_generation", "profile", "frame_count"},
                {"film_id", "source_generation", "profile", "frame_count", "composition_profile"})
                or not isinstance(options["film_id"], str) or not options["film_id"]
                or not isinstance(options["source_generation"], str) or len(options["source_generation"]) != 64
                or type(options["frame_count"]) is not int or options["frame_count"] < 1):
            raise ValueError("A validated film feature-preparation request is required")
        encoded = json.dumps({"search_features": options}, sort_keys=True, allow_nan=False)
        key = "prepare-search-features:" + hashlib.sha256(encoded.encode()).hexdigest()
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM jobs WHERE kind='prepare-search-features' AND path_key=? AND status IN ('queued','waiting_worker','running','interrupted','failed') AND cancel_requested=0 ORDER BY created_at DESC LIMIT 1", (key,)).fetchone()
            if row is not None:
                if row["status"] in {"interrupted", "failed"} or (row["status"] == "waiting_worker" and row["error"]
                        and row["error"] != SEARCH_FEATURE_PAUSE_REASON):
                    con.execute("UPDATE jobs SET status='waiting_worker',error=NULL,finished_at=NULL WHERE id=?", (row["id"],))
                identity = row["id"]
            else:
                identity = str(uuid.uuid4())
                con.execute("INSERT INTO jobs (id,kind,status,snapshot,created_at,path_key) VALUES (?,?,?,?,?,?)",
                            (identity, "prepare-search-features", "waiting_worker", encoded, time.time(), key))
        return self.get_job(identity)

    def continue_search_features(self, identity, result, *, error=None):
        """Yield the worker after one batch; blocked jobs need explicit resume."""
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT kind,status,cancel_requested FROM jobs WHERE id=?", (identity,)).fetchone()
            if row is None or row["kind"] != "prepare-search-features" or row["status"] != "running":
                raise ValueError("Only a running search-feature job can yield")
            status = "cancelled" if row["cancel_requested"] else "waiting_worker"
            con.execute("UPDATE jobs SET status=?,result=?,error=?,finished_at=? WHERE id=?",
                        (status, json.dumps(result, allow_nan=False), error,
                         time.time() if status == "cancelled" else None, identity))
        return self.get_job(identity)

    def enqueue_composition_fit(self):
        """One explicit local fitting experiment after existing feature work."""
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT id FROM jobs WHERE kind='fit-search-composition' AND status IN ('queued','waiting_worker','running') AND cancel_requested=0").fetchone()
            if row is not None:
                identity = row["id"]
            else:
                identity = str(uuid.uuid4())
                con.execute("INSERT INTO jobs (id,kind,status,snapshot,created_at,path_key) VALUES (?,?,?,?,?,?)",
                            (identity, "fit-search-composition", "waiting_worker", '{}', time.time(), "fit-search-composition:v1"))
        return self.get_job(identity)

    def search_feature_queue(self):
        """Inspect optional preparation without loading film indexes or models."""
        if not self.path.exists():
            return []
        with self.connection() as con:
            rows = con.execute("SELECT id,kind,status,snapshot,result,error,cancel_requested FROM jobs "
                               "WHERE kind IN (?,?) ORDER BY created_at,rowid", _SEARCH_FEATURE_KINDS).fetchall()
        return [{"job_id": row["id"], "kind": row["kind"], "status": row["status"],
                 "film_id": json.loads(row["snapshot"]).get("search_features", {}).get("film_id"),
                 "cursor": json.loads(row["result"] or "{}").get("cursor"),
                 "error": row["error"],
                 "paused": (row["status"] == "waiting_worker" and not row["cancel_requested"]
                            and row["error"] == SEARCH_FEATURE_PAUSE_REASON)} for row in rows]

    def pause_search_features(self):
        """Hold currently runnable optional jobs, after the active batch drains."""
        if not self.path.exists():
            return {"paused": 0, "job_ids": []}
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            if con.execute("SELECT 1 FROM jobs WHERE kind IN (?,?) AND status='running' LIMIT 1",
                           _SEARCH_FEATURE_KINDS).fetchone():
                raise ValueError("Optional search preparation is still running; stop the ingest worker "
                                 "with --role ingest --stop and wait for the active batch to finish")
            identities = [row[0] for row in con.execute(
                "SELECT id FROM jobs WHERE kind IN (?,?) AND status IN ('queued','waiting_worker') "
                "AND error IS NULL AND cancel_requested=0 ORDER BY created_at,rowid", _SEARCH_FEATURE_KINDS)]
            con.executemany("UPDATE jobs SET status='waiting_worker',error=? WHERE id=?",
                            [(SEARCH_FEATURE_PAUSE_REASON, identity) for identity in identities])
        return {"paused": len(identities), "job_ids": identities}

    def resume_search_features(self):
        """Release only jobs held by pause_search_features, retaining their cursors."""
        if not self.path.exists():
            return {"resumed": 0, "job_ids": []}
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            identities = [row[0] for row in con.execute(
                "SELECT id FROM jobs WHERE kind IN (?,?) AND status='waiting_worker' "
                "AND error=? AND cancel_requested=0 ORDER BY created_at,rowid",
                (*_SEARCH_FEATURE_KINDS, SEARCH_FEATURE_PAUSE_REASON))]
            con.executemany("UPDATE jobs SET error=NULL WHERE id=?", [(identity,) for identity in identities])
        return {"resumed": len(identities), "job_ids": identities}

    def enqueue_temporal_backfill(self, film_id, unit_ids, *, sampling_profile, batch_size=32):
        """Freeze bounded maintenance batches in the existing durable ledger.

        The caller supplies an inspected plan. The worker revalidates each
        source and skips evidence already refreshed before it reaches the job.
        Exact active batches are deduplicated; completed work is never replayed
        automatically after an interrupted worker.
        """
        if (not isinstance(film_id, str) or not 1 <= len(film_id) <= 200
                or not isinstance(sampling_profile, str) or not 1 <= len(sampling_profile) <= 100):
            raise ValueError("A film and sampling profile are required")
        if type(batch_size) is not int or not 1 <= batch_size <= 128:
            raise ValueError("Backfill batch_size must be between 1 and 128")
        if (not isinstance(unit_ids, (list, tuple)) or len(unit_ids) > 100_000
                or any(not isinstance(identity, str) or not 1 <= len(identity) <= 200 for identity in unit_ids)):
            raise ValueError("Backfill requires a bounded list of unit IDs")
        identities = sorted(set(unit_ids))
        jobs = []
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            for start in range(0, len(identities), batch_size):
                options = {"film_id": film_id, "unit_ids": identities[start:start + batch_size],
                           "sampling_profile": sampling_profile, "batch_size": batch_size}
                encoded = json.dumps({"temporal_backfill": options}, sort_keys=True, allow_nan=False)
                key = "backfill-temporal:" + hashlib.sha256(encoded.encode()).hexdigest()
                existing = con.execute("SELECT * FROM jobs WHERE kind='backfill-temporal' AND path_key=? AND status IN ('queued','waiting_worker','running') AND cancel_requested=0", (key,)).fetchone()
                if existing is not None:
                    jobs.append(self._job(existing))
                    continue
                identity = str(uuid.uuid4())
                con.execute("INSERT INTO jobs (id,kind,status,snapshot,created_at,path_key) VALUES (?,?,?,?,?,?)",
                            (identity, "backfill-temporal", "waiting_worker", encoded, time.time(), key))
                jobs.append(self._job(con.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()))
        return jobs

    def temporal_jobs(self):
        """Maintenance progress without creating an edit or changing its revision."""
        with self.connection() as con:
            return [self._job(row, private=True) for row in con.execute(
                "SELECT * FROM jobs WHERE kind='backfill-temporal' ORDER BY created_at,rowid")]

    def enqueue_match_search(self, request, profile_id, reference):
        """Freeze an explicit discovery request without creating an edit/project."""
        from pipeline.matching.contracts import SearchRequest
        request = SearchRequest.model_validate(request).model_dump(mode="json")
        snapshot = {"match_search": {"request": {**request, "profile_id": profile_id},
                                     "reference": reference}}
        encoded = json.dumps(snapshot, allow_nan=False, sort_keys=True)
        key = "match-search:" + hashlib.sha256(encoded.encode()).hexdigest()
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            previous = con.execute("SELECT * FROM jobs WHERE kind='match-search' AND path_key=? AND status IN ('queued','running') AND cancel_requested=0", (key,)).fetchone()
            if previous is not None:
                return self._job(previous)
            identity = str(uuid.uuid4())
            con.execute("INSERT INTO jobs (id,kind,status,snapshot,created_at,path_key) VALUES (?,?,?,?,?,?)",
                        (identity, "match-search", "queued", encoded, time.time(), key))
        return self.get_job(identity)

    def enqueue_match_search_preview(self, search_id, candidate_id):
        """Copy only an actual server candidate; a client cannot supply footage."""
        key = f"match-search-preview:{search_id}:{candidate_id}"
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            parent = self._job(con.execute("SELECT * FROM jobs WHERE id=?", (search_id,)).fetchone(), private=True)
            if parent["kind"] != "match-search" or parent["status"] != "completed" or parent["cancel_requested"]:
                raise ValueError("Wait for this match search to complete before preparing another preview")
            candidate = next((row for row in (parent["result"] or {}).get("candidates", []) if row["id"] == candidate_id), None)
            if candidate is None:
                raise KeyError("This match suggestion is unavailable")
            previous = con.execute("SELECT * FROM jobs WHERE kind='match-search-preview' AND path_key=? AND status IN ('queued','running') AND cancel_requested=0", (key,)).fetchone()
            if previous is not None:
                return self._job(previous)
            snapshot = {"match_search_preview": {"search_id": search_id, "candidate": candidate,
                                                "reference": parent["snapshot"]["match_search"]["reference"]}}
            identity = str(uuid.uuid4())
            con.execute("INSERT INTO jobs (id,kind,status,snapshot,created_at,path_key) VALUES (?,?,?,?,?,?)",
                        (identity, "match-search-preview", "queued", json.dumps(snapshot, allow_nan=False), time.time(), key))
        return self.get_job(identity)

    def match_search_previews(self, search_id, candidate_id):
        with self.connection() as con:
            return [self._job(row, private=True) for row in con.execute(
                "SELECT * FROM jobs WHERE kind='match-search-preview' AND path_key=? ORDER BY created_at DESC,id DESC",
                (f"match-search-preview:{search_id}:{candidate_id}",))]

    def project_jobs(self, identity):
        self.get_project(identity)
        with self.connection() as con:
            return [self._job(row) for row in con.execute(
                "SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC,id DESC", (identity,))]

    def claim(self, role="all", *, owner_token=None):
        from pipeline.lab.job_roles import kinds_for_role

        kinds = kinds_for_role(role)
        placeholders = ",".join("?" for _ in kinds)
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            if owner_token is not None:
                control = con.execute("SELECT token,stop_requested FROM worker_controls WHERE role=?", (role,)).fetchone()
                if control is None or control["token"] != owner_token or control["stop_requested"]:
                    return None
            # Older running workers understand only 'queued'. They leave these
            # maintenance jobs durable until an updated worker is started,
            # rather than dispatching an unknown kind as a creative edit.
            # UUIDs carry no queue order. Preserve insertion order when the
            # system clock gives multiple jobs the same creation timestamp.
            row = con.execute(f"""SELECT * FROM jobs WHERE kind IN ({placeholders})
                AND (status='queued' OR (kind='backfill-temporal' AND status='waiting_worker')
                     OR (kind IN ('prepare-search-features','fit-search-composition') AND status='waiting_worker' AND error IS NULL))
                ORDER BY CASE WHEN kind='fit-search-composition' THEN 2 WHEN kind IN ('backfill-temporal','prepare-search-features') THEN 1 ELSE 0 END,created_at,rowid LIMIT 1""", kinds).fetchone()
            if row is None:
                return None
            con.execute("UPDATE jobs SET status='running',started_at=? WHERE id=?", (time.time(), row["id"]))
            return self._job(con.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone(), private=True)

    def recover_interrupted(self, role="all"):
        """Called under this role's worker lock; another lane may still be active."""
        from pipeline.lab.job_roles import kinds_for_role

        kinds = kinds_for_role(role)
        placeholders = ",".join("?" for _ in kinds)
        with self.connection() as con:
            return con.execute(f"UPDATE jobs SET status='interrupted',finished_at=?,error=? WHERE status='running' AND kind IN ({placeholders})",
                               (time.time(), "Worker stopped before completion; inspect the job before explicitly retrying", *kinds)).rowcount

    def progress(self, identity, message):
        message = str(message)[-4000:]
        with self.connection() as con:
            row = con.execute("SELECT log FROM jobs WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise KeyError("Job not found")
            log = (json.loads(row["log"]) + [message])[-80:]
            con.execute("UPDATE jobs SET progress=?,log=? WHERE id=?", (message, json.dumps(log), identity))

    def publish_match_progress(self, identity, result):
        """Publish immutable suggestions as previews arrive; never edit a project."""
        with self.connection() as con:
            con.execute("UPDATE jobs SET result=? WHERE id=? AND kind IN ('match','match-search') AND status='running' AND cancel_requested=0",
                        (json.dumps(result, allow_nan=False), identity))

    def cancel(self, identity):
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT status FROM jobs WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise KeyError("Job not found")
            if row["status"] in {"queued", "waiting_worker"}:
                con.execute("UPDATE jobs SET status='cancelled',cancel_requested=1,finished_at=? WHERE id=?", (time.time(), identity))
            elif row["status"] == "running":
                con.execute("UPDATE jobs SET cancel_requested=1,progress=? WHERE id=?", ("Cancellation requested; waiting for the active operation to stop", identity))
            # A cancelled search must not be revived by a queued or running child.
            kind = con.execute("SELECT kind FROM jobs WHERE id=?", (identity,)).fetchone()[0]
            if kind == "match-search":
                con.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (identity,))
                for child in con.execute("SELECT id,status,snapshot FROM jobs WHERE kind='match-search-preview' AND status IN ('queued','running')").fetchall():
                    if json.loads(child["snapshot"])["match_search_preview"]["search_id"] == identity:
                        con.execute("UPDATE jobs SET cancel_requested=1,status=?,progress=?,finished_at=? WHERE id=?",
                                    ("cancelled" if child["status"] == "queued" else "running", "Parent match search cancelled",
                                     time.time() if child["status"] == "queued" else None, child["id"]))
        return self.get_job(identity)

    def is_cancelled(self, identity):
        with self.connection() as con:
            row = con.execute("SELECT cancel_requested FROM jobs WHERE id=?", (identity,)).fetchone()
            return bool(row and row[0])

    def finish(self, identity, *, result=None, error=None, document=None):
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            job = self._job(con.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone(), private=True)
            if document is not None and job["project_id"] is None:
                raise ValueError("Discovery jobs cannot create or change an edit")
            status = "cancelled" if job["cancel_requested"] else "failed" if error else "completed"
            result = result or {}
            if status == "completed" and document is not None:
                proposal = ProjectDocument.model_validate(document).model_dump(mode="json")
                # Additive defaults must not look like an AI edit to a snapshot
                # queued before the processing/duck-control fields existed.
                original = ProjectDocument.model_validate(job["document"]).model_dump(mode="json")
                for field, default in (("dialogue_clips", []), ("music_gain_db", 0),
                                       ("audio_fade_in_seconds", 0), ("audio_fade_out_seconds", 0)):
                    if proposal.get(field, default) != original.get(field, default):
                        raise ValueError("Generated revision changed the user's audio arrangement")
                positions = {clip["id"]: (index, clip) for index, clip in enumerate(proposal["clips"])}
                from pipeline.lab.timeline import clip_positions
                original_timing, proposal_timing = clip_positions(original), clip_positions(proposal)
                for index, clip in enumerate(original["clips"]):
                    if clip["locked"] and positions.get(clip["id"]) != (index, clip):
                        raise ValueError("Generated revision changed a locked clip")
                    if clip["locked"]:
                        before, after = original_timing.get(clip["id"]), proposal_timing.get(clip["id"])
                        if (before is None) != (after is None) or (before and any(abs(a - b) > 0.000001 for a, b in zip(before, after))):
                            raise ValueError("Generated revision moved a locked clip on the music timeline")
                try:
                    updated = self._update(con, job["project_id"], job["base_revision"], proposal)
                    result = {**result, "applied": True, "revision": updated["revision"]}
                except RevisionConflict:
                    result = {**result, "applied": False, "document": proposal, "reason": "Project changed while this job ran; proposal retained without overwriting edits"}
            con.execute("UPDATE jobs SET status=?,finished_at=?,error=?,result=? WHERE id=?",
                        (status, time.time(), error, json.dumps(result, allow_nan=False), identity))
        return self.get_job(identity)

    def ingest_snapshots(self):
        with self.connection() as con:
            jobs = [self._job(row, private=True) for row in con.execute("SELECT * FROM jobs WHERE kind='ingest' ORDER BY created_at,rowid")]
        pending = [job["id"] for job in jobs if job["status"] == "queued"]
        return [{"job_id": job["id"], **job["snapshot"], "status": {"completed": "done", "failed": "error", "interrupted": "error", "cancelled": "error"}.get(job["status"], job["status"]),
                 "queued_at": job["created_at"], "started_at": job["started_at"], "finished_at": job["finished_at"],
                 "error": job["error"], "log": job["log"], "progress": job["progress"],
                 "queue_position": pending.index(job["id"]) + 1 if job["id"] in pending else None} for job in jobs]
