"""Optional helpers for making and reviewing AI Music Video edits through the running API.

Thin and replaceable: each command is a few API calls an agent or a person can
also make by hand. Change or drop them freely.

    uv run python scripts/edit_tools.py make spec.json OUT_DIR    # project, beats, generation, render, recipe card
    uv run python scripts/edit_tools.py render PROJECT_ID OUT.mp4 [--export]
    uv run python scripts/edit_tools.py card PROJECT_ID           # the edit's recipe and pacing shape as text
    uv run python scripts/edit_tools.py sheet PROJECT_ID VIDEO OUT.jpg   # one frame per shot
    uv run python scripts/edit_tools.py dailies spec.json OUT_DIR # a review page of rendered edits

A make spec: {"name", "label", "track" (part of an imported track's name), "passage": [start, end],
"instruction", "settings" (planner_settings), optional "film_ids", "aspect", "ranges",
"reuse_from" (a project whose beats and listening match this passage), "project_id" (regenerate it)}.
A dailies spec: {"title", "intro", "items": [{"pid", "file", "title", "question"}]}.
"""
from __future__ import annotations

import argparse
import html
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))       # the repository's pipeline package
API = httpx.Client(base_url="http://127.0.0.1:8000", timeout=120)
PACE_LABEL = {"patient": "patient", "balanced": "balanced", "kinetic": "energetic", "rapid": "rapid",
              "hold": "hold", "flash": "flash"}


def project(pid: str) -> dict:
    response = API.get(f"/lab/projects/{pid}")
    response.raise_for_status()
    return response.json()


def track(name_part: str) -> dict:
    from pipeline.config import load_config
    path = load_config().paths.state_dir / "lab" / "lab.sqlite3"
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = [row for row in db.execute("select id, name, duration from tracks") if name_part.lower() in row[1].lower()]
    if len(rows) != 1:
        raise SystemExit(f"{len(rows)} imported tracks match {name_part!r}; import it in the app or be more specific")
    return {"id": rows[0][0], "name": rows[0][1], "duration": rows[0][2]}


def create(spec: dict) -> str:
    document = {"track": track(spec["track"]), "passage": {"start": spec["passage"][0], "end": spec["passage"][1]},
                "planner_settings": spec["settings"], "film_ids": spec.get("film_ids") or [],
                "aspect_ratio": spec.get("aspect", "16:9"),
                "editor_direction": {"instruction": spec.get("instruction", ""), "ranges": spec.get("ranges") or []}}
    if spec.get("reuse_from"):
        source = project(spec["reuse_from"])["document"]
        if source["passage"] == document["passage"] and source["track"]["id"] == document["track"]["id"]:
            document["rhythm"], document["analysis"] = source.get("rhythm"), source.get("analysis")
    response = API.post("/lab/projects", json={"name": spec["name"], "experiment_id": "music-sketch", "document": document})
    response.raise_for_status()
    return response.json()["id"]


def job(pid: str, kind: str, **extra) -> dict:
    """Start a job on the project's current revision and wait for it, printing its steps."""
    response = API.post(f"/lab/projects/{pid}/jobs", json={"kind": kind, "base_revision": project(pid)["revision"], **extra})
    if response.status_code >= 400:
        raise SystemExit(f"{kind}: {response.status_code} {response.text}")
    job_id, seen = response.json()["id"], 0
    while True:
        state = API.get(f"/lab/jobs/{job_id}").json()
        steps = state.get("progress_steps") or []
        for step in steps[seen:]:
            if not str(step).startswith(("Rendering clip", "Finding footage")):
                print("   ", step, flush=True)
        seen = len(steps)
        if state["status"] not in ("queued", "running", "cancelling"):
            if state["status"] != "completed":
                raise SystemExit(f"{kind} {state['status']}: {state.get('error')}")
            return state
        time.sleep(3)


def render(pid: str, out: Path, mode: str = "preview") -> Path:
    state = job(pid, "render", mode=mode)
    out.parent.mkdir(parents=True, exist_ok=True)
    with API.stream("GET", f"/lab/jobs/{state['id']}/output") as response:
        response.raise_for_status()
        with out.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)
    return out


def tc(seconds: float) -> str:
    minutes, rest = divmod(max(0.0, seconds), 60)
    return f"{int(minutes)}:{rest:04.1f}"


def card(pid: str) -> str:
    d = project(pid)["document"]
    plan, origin = d.get("direction_plan") or {}, d["passage"]["start"]
    slots = d["music_timeline"]["slots"]
    durations = sorted(s["end"] - s["start"] for s in slots)
    films = {(s.get("alternatives") or [{}])[0].get("film_title") for s in slots}
    lines = [f"song: {d['track']['name']} {tc(origin)}-{tc(d['passage']['end'])}",
             f"direction: {(d.get('editor_direction') or {}).get('instruction', '')}",
             f"settings: {json.dumps(d['planner_settings'])} | {d['aspect_ratio']} | films: {len(d['film_ids']) or 'all'}",
             f"concept: {plan.get('concept', '')}",
             f"shots: {len(slots)} from {len(films)} films; {durations[0]:.2f}s to {durations[-1]:.2f}s, "
             f"median {durations[len(durations) // 2]:.2f}s"]
    lines += [f"  {a['start'] - origin:5.1f}-{a['end'] - origin:5.1f}s {a['pace']:<8} {a['intent'][:90]}"
              for a in plan.get("acts") or []]
    return "\n".join(lines)


def sheet(pid: str, video: Path, out: Path, columns: int = 4) -> Path:
    from PIL import Image, ImageDraw
    d = project(pid)["document"]
    origin, tiles = d["passage"]["start"], []
    with tempfile.TemporaryDirectory() as tmp:
        for index, slot in enumerate(d["music_timeline"]["slots"]):
            frame = Path(tmp) / f"{index}.jpg"
            subprocess.run(["ffmpeg", "-v", "quiet", "-ss", f"{(slot['start'] + slot['end']) / 2 - origin:.3f}", "-i",
                            str(video), "-frames:v", "1", "-vf", "scale=320:-2", str(frame)], check=True)
            image = Image.open(frame).convert("RGB")
            draw = ImageDraw.Draw(image)
            draw.rectangle([0, 0, 320, 30], fill=(0, 0, 0))
            draw.text((4, 2), f"{index + 1}. {slot['start'] - origin:5.2f}s +{slot['end'] - slot['start']:.2f}s", fill=(255, 255, 0))
            draw.text((4, 15), (slot.get("alternatives") or [{}])[0].get("film_title", "")[:34], fill=(255, 255, 255))
            tiles.append(image)
    width, height = tiles[0].size
    page = Image.new("RGB", (columns * width, -(-len(tiles) // columns) * height), (20, 20, 20))
    for index, image in enumerate(tiles):
        page.paste(image, ((index % columns) * width, (index // columns) * height))
    page.save(out, quality=85)
    return out


def _strip(d: dict) -> str:
    origin, length = d["passage"]["start"], d["passage"]["end"] - d["passage"]["start"]
    x = lambda t: round((t - origin) / length * 1000, 2)
    parts = [f'<rect class="b-{a["pace"]}" x="{x(a["start"])}" y="0" width="{max(0.5, x(a["end"]) - x(a["start"]))}" height="22">'
             f'<title>{PACE_LABEL.get(a["pace"], a["pace"])} {tc(a["start"] - origin)}-{tc(a["end"] - origin)}</title></rect>'
             for a in (d.get("direction_plan") or {}).get("acts") or []]
    parts += [f'<line class="cut" x1="{x(s["start"])}" x2="{x(s["start"])}" y1="0" y2="22"/>'
              for s in d["music_timeline"]["slots"][1:]]
    parts += [f'<text class="tick" x="{x(origin + s)}" y="36">{s}s</text>'
              for s in range(0, int(length) + 1, 5 if length <= 40 else 10)]
    return f'<svg class="strip" viewBox="-14 0 1028 40" preserveAspectRatio="none" role="img" aria-label="Pacing shape">{"".join(parts)}</svg>'


def _section(index: int, total: int, item: dict) -> str:
    d = project(item["pid"])["document"]
    plan, origin, slots = d.get("direction_plan") or {}, d["passage"]["start"], d["music_timeline"]["slots"]
    durations = [s["end"] - s["start"] for s in slots]
    titles = {c["id"]: c.get("title", "") for c in d.get("clips", [])}       # hand-built edits have no alternatives
    films = [(s.get("alternatives") or [{}])[0].get("film_title", "") or titles.get(s.get("clip_id"), "") for s in slots]
    settings = d["planner_settings"]
    chips = [f"pace: {PACE_LABEL.get(settings.get('pacing'), settings.get('pacing'))}",
             f"footage: {settings.get('footage', 'balanced')}", f"match cuts: {settings.get('match_cuts', 'some')}",
             d["aspect_ratio"], "1 film" if len(d["film_ids"]) == 1 else f"{len(d['film_ids'])} films" if d["film_ids"] else "whole library"]
    paces = sorted({a["pace"] for a in plan.get("acts") or []}, key=list(PACE_LABEL).index)
    legend = "".join(f'<span class="key"><i class="b-{p}"></i>{PACE_LABEL[p]}</span>' for p in paces)
    rows = "".join(f"<tr><td>{i + 1}</td><td>{tc(s['start'] - origin)}</td><td>{t:.2f}s</td><td>{html.escape(f)}</td></tr>"
                   for i, (s, t, f) in enumerate(zip(slots, durations, films)))
    instruction = (d.get("editor_direction") or {}).get("instruction") or "(no brief: the editor chose the style)"
    song = plan.get("song") or {}
    read = ""
    if song:
        known, style = song.get("profile") or {}, song.get("treatment") or {}
        heard = " · ".join(part for part in (known.get("genre"), known.get("mood")) if part)
        read = (f'<div class="read"><p class="label">How the editor read the song</p>'
                f'<p>{html.escape(heard)}</p><p>{html.escape(known.get("lyric_reading") or "")}</p>'
                f'<p class="label">The style it chose: {html.escape(style.get("style", ""))}</p>'
                f'<p>{html.escape(style.get("idea", ""))}</p><p class="muted">Look: {html.escape(style.get("look", ""))}</p></div>')
    return f"""<section class="edit" id="e{index}">
  <header class="edit-head"><p class="eyebrow">Edit {index} of {total}</p><h2>{html.escape(item['title'])}</h2>
    <p class="song">{html.escape(Path(d['track']['name']).stem)} <span class="mono">{tc(origin)}–{tc(d['passage']['end'])}</span></p></header>
  <video controls playsinline preload="metadata" src="{item['file']}"></video>
  <div class="facts">
    <p class="brief">“{html.escape(instruction)}”</p>{read}
    <ul class="chips">{''.join(f'<li>{html.escape(c)}</li>' for c in chips)}</ul>
    <div class="shape"><p class="label">Pacing shape <span class="mono">{len(slots)} shots · {len(set(films))} films · {min(durations):.2f}–{max(durations):.2f}s</span></p>
      {_strip(d)}<div class="legend">{legend}<span class="key"><i class="cutkey"></i>cut</span></div></div>
    <p class="look"><strong>Look for:</strong> {html.escape(item['question'])}</p>
    <details><summary>The editor's concept and cut list</summary><p class="concept">{html.escape(plan.get('concept', ''))}</p>
      <div class="table-wrap"><table><thead><tr><th>#</th><th>At</th><th>Length</th><th>Film</th></tr></thead><tbody>{rows}</tbody></table></div></details>
  </div>
</section>"""


DAILIES_CSS = """
:root { --bg: #f3f2ef; --surface: #ffffff; --ink: #1d1f24; --muted: #5d6270; --line: #d9d8d3; --accent: #b5561c; --screen: #0d0e10;
  --patient: #9db3c9; --balanced: #b9c4a3; --kinetic: #e0c07a; --rapid: #e39a6a; --hold: #6d8fb3; --flash: #b5561c;
  --body: "Schibsted Grotesk", "Segoe UI", system-ui, sans-serif; --mono: "IBM Plex Mono", ui-monospace, Consolas, monospace; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg: #15171b; --surface: #1d2025; --ink: #ecebe7; --muted: #a2a6b0;
  --line: #30343b; --accent: #e08a4f; --screen: #000; --patient: #5f7a95; --balanced: #7d8a68; --kinetic: #b39350; --rapid: #c27646;
  --hold: #4f7bab; --flash: #e08a4f; color-scheme: dark; } }
:root[data-theme="dark"] { --bg: #15171b; --surface: #1d2025; --ink: #ecebe7; --muted: #a2a6b0; --line: #30343b; --accent: #e08a4f;
  --screen: #000; --patient: #5f7a95; --balanced: #7d8a68; --kinetic: #b39350; --rapid: #c27646; --hold: #4f7bab; --flash: #e08a4f; color-scheme: dark; }
body { background: var(--bg); color: var(--ink); font: 400 1rem/1.55 var(--body); }
main { max-width: 46rem; margin: 0 auto; padding-inline: 16px; padding-block: 2rem 3rem; display: grid; gap: 2.5rem; }
h1, h2 { text-wrap: balance; margin: 0; letter-spacing: -0.01em; } h1 { font-size: clamp(1.8rem, 6vw, 2.4rem); } h2 { font-size: 1.45rem; }
p { margin: 0; } .eyebrow, .label { font: 500 .72rem/1.2 var(--mono); text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }
.mono { font-family: var(--mono); font-size: .85em; font-variant-numeric: tabular-nums; } .top, .edit-head { display: grid; gap: .5rem; }
.intro, .song, .concept { color: var(--muted); } .edit { display: grid; gap: 1rem; padding-top: 2rem; border-top: 1px solid var(--line); }
video { width: 100%; max-width: 100%; aspect-ratio: 16 / 9; background: var(--screen); border-radius: 6px; display: block; }
.facts { display: grid; gap: 1rem; min-width: 0; } .brief { font-size: 1.1rem; border-left: 3px solid var(--accent); padding-left: .8rem; }
.chips { display: flex; flex-wrap: wrap; gap: .4rem; list-style: none; margin: 0; padding: 0; }
.chips li { font: 400 .8rem/1 var(--mono); padding: .4rem .55rem; border: 1px solid var(--line); border-radius: 4px; background: var(--surface); }
.shape { display: grid; gap: .5rem; } .label .mono { text-transform: none; letter-spacing: 0; margin-left: .4rem; }
.strip { width: 100%; height: 3.2rem; display: block; overflow: visible; }
.strip .cut { stroke: var(--ink); stroke-width: 1.4; vector-effect: non-scaling-stroke; opacity: .75; }
.strip .tick { font: 400 11px var(--mono); fill: var(--muted); text-anchor: middle; }
.b-patient { fill: var(--patient); } .b-balanced { fill: var(--balanced); } .b-kinetic { fill: var(--kinetic); }
.b-rapid { fill: var(--rapid); } .b-hold { fill: var(--hold); } .b-flash { fill: var(--flash); }
.legend { display: flex; flex-wrap: wrap; gap: .4rem 1rem; font: 400 .78rem/1 var(--mono); color: var(--muted); }
.key { display: inline-flex; align-items: center; gap: .35rem; } .key i { width: .9rem; height: .6rem; display: inline-block; border-radius: 2px; }
.key i.b-patient { background: var(--patient); } .key i.b-balanced { background: var(--balanced); } .key i.b-kinetic { background: var(--kinetic); }
.key i.b-rapid { background: var(--rapid); } .key i.b-hold { background: var(--hold); } .key i.b-flash { background: var(--flash); }
.key i.cutkey { width: 2px; height: .8rem; background: var(--ink); border-radius: 0; }
details { border: 1px solid var(--line); border-radius: 6px; background: var(--surface); padding: .7rem .9rem; }
summary { cursor: pointer; font-weight: 600; } summary:focus-visible, video:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.table-wrap { overflow-x: auto; margin-top: .7rem; }
table { border-collapse: collapse; width: 100%; font: 400 .82rem/1.4 var(--mono); font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: .3rem .6rem .3rem 0; border-bottom: 1px solid var(--line); white-space: nowrap; } th { color: var(--muted); font-weight: 500; }
footer { color: var(--muted); border-top: 1px solid var(--line); padding-top: 1.5rem; }
.read { display: grid; gap: .45rem; border: 1px solid var(--line); border-radius: 6px; background: var(--surface); padding: .8rem .9rem; }
.read .label:not(:first-child) { margin-top: .4rem; } .muted { color: var(--muted); }
"""


def dailies(spec: dict) -> str:
    """A review page: each edit's video, brief, settings, pacing shape and cut list (videos sit beside the page)."""
    items = spec["items"]
    body = "".join(_section(index, len(items), item) for index, item in enumerate(items, start=1))
    return f"""<title>Scene Recall Dailies</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Schibsted+Grotesk:wght@400;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>{DAILIES_CSS}</style>
<main><header class="top"><p class="eyebrow">Scene Recall · AI editor</p><h1>{html.escape(spec.get('title', 'Dailies'))}</h1>
<p class="intro">{html.escape(spec.get('intro', ''))}</p></header>{body}
<footer><p>Reply in the chat with what works and what doesn't. Short reactions are enough.</p></footer></main>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("make")
    make.add_argument("spec", type=Path)
    make.add_argument("out_dir", type=Path)
    one = commands.add_parser("render")
    one.add_argument("project_id")
    one.add_argument("out", type=Path)
    one.add_argument("--export", action="store_true")
    commands.add_parser("card").add_argument("project_id")
    frames = commands.add_parser("sheet")
    frames.add_argument("project_id")
    frames.add_argument("video", type=Path)
    frames.add_argument("out", type=Path)
    page = commands.add_parser("dailies")
    page.add_argument("spec", type=Path)
    page.add_argument("out_dir", type=Path)
    args = parser.parse_args()
    if args.command == "make":
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
        pid = spec.get("project_id") or create(spec)
        print("project", pid, flush=True)
        if not spec.get("project_id") and not spec.get("reuse_from"):
            job(pid, "rhythm")
        job(pid, "generate", generate={"mode": "regenerate"})
        print("render", render(pid, args.out_dir / f"{spec['label']}.mp4"))
        print(card(pid))
    elif args.command == "render":
        print(render(args.project_id, args.out, "export" if args.export else "preview"))
    elif args.command == "card":
        print(card(args.project_id))
    elif args.command == "sheet":
        print(sheet(args.project_id, args.video, args.out))
    elif args.command == "dailies":
        args.out_dir.mkdir(parents=True, exist_ok=True)
        (args.out_dir / "index.html").write_text(dailies(json.loads(args.spec.read_text(encoding="utf-8"))), encoding="utf-8")
        print(args.out_dir / "index.html")


if __name__ == "__main__":
    main()
