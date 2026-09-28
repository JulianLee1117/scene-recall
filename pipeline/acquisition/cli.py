"""CLI facade over the running API; the service owns acquisition decisions."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

import click

MAX_TORRENT_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def _api_url(_ctx, _param, value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or "\\" in value or any(ord(char) < 33 for char in value)):
            raise ValueError
        _ = parsed.port
    except ValueError:
        raise click.BadParameter("Use an HTTP(S) API URL without credentials, query, or fragment")
    return value.rstrip("/")


def _request(api_url: str, path: str, *, body=None, content_type="application/json"):
    if body is not None and not isinstance(body, bytes):
        body = json.dumps(body).encode("utf-8")
    request = Request(api_url + "/acquisition" + path, data=body,
                      headers={"Accept": "application/json", **({"Content-Type": content_type} if body is not None else {})},
                      method="POST" if body is not None else "GET")
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        try:
            raw = exc.read(64 * 1024)
        finally:
            exc.close()
        detail = f"API returned HTTP {exc.code}"
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
                detail += ": " + payload["detail"]
            elif isinstance(payload, dict) and isinstance(payload.get("detail"), list):
                detail += ": " + "; ".join(str(error.get("msg", "Invalid input"))
                                          for error in payload["detail"] if isinstance(error, dict))
        except (UnicodeError, ValueError, RecursionError):
            pass
        raise click.ClickException(detail) from None
    except (URLError, TimeoutError, OSError):
        raise click.ClickException("Cannot reach the Scene Recall API; start it or check --api-url") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise click.ClickException("API response exceeded the supported size")
    try:
        return json.loads(raw)
    except (UnicodeError, ValueError, RecursionError):
        raise click.ClickException("API returned an invalid JSON response") from None


def _show(payload) -> None:
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@click.group()
@click.option("--api-url", default="http://127.0.0.1:8000", envvar="SCENE_RECALL_API_URL",
              callback=_api_url, show_default=True, help="Running Scene Recall backend.")
@click.pass_context
def acquisition(ctx: click.Context, api_url: str) -> None:
    """Search releases and manage downloads through the running API."""
    ctx.ensure_object(dict)
    ctx.obj["acquisition_api_url"] = api_url


@acquisition.command("status")
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show downloader, search-provider, and monitor availability."""
    _show(_request(ctx.obj["acquisition_api_url"], "/status"))


@acquisition.command("list")
@click.pass_context
def list_acquisitions(ctx: click.Context) -> None:
    """List durable acquisitions, their progress, and current revisions."""
    _show(_request(ctx.obj["acquisition_api_url"], ""))


@acquisition.command("search")
@click.argument("query")
@click.pass_context
def search(ctx: click.Context, query: str) -> None:
    """Search configured indexers for QUERY; results contain opaque release IDs."""
    _show(_request(ctx.obj["acquisition_api_url"], "/search?" + urlencode({"q": query})))


def _film_options(function):
    for option in (
        click.option("--edition", default="", help="Optional edition label."),
        click.option("--year", required=True, type=click.IntRange(1888, 2100)),
        click.option("--title", required=True, help="Canonical film title."),
    ):
        function = option(function)
    return function


@acquisition.command("add-magnet")
@click.argument("magnet")
@_film_options
@click.pass_context
def add_magnet(ctx: click.Context, magnet: str, title: str, year: int, edition: str) -> None:
    """Queue a quoted MAGNET link with an explicit film identity."""
    _show(_request(ctx.obj["acquisition_api_url"], "/magnet",
                   body={"magnet": magnet, "title": title, "year": year, "edition": edition}))


@acquisition.command("add-torrent")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@_film_options
@click.pass_context
def add_torrent(ctx: click.Context, file: Path, title: str, year: int, edition: str) -> None:
    """Upload a local .torrent FILE, at most 2 MiB."""
    if file.suffix.lower() != ".torrent":
        raise click.ClickException("Choose a .torrent file")
    with file.open("rb") as source:
        data = source.read(MAX_TORRENT_BYTES + 1)
    if not data or len(data) > MAX_TORRENT_BYTES:
        raise click.ClickException("Torrent file must be nonempty and at most 2 MiB")
    boundary = "scene-recall-" + uuid4().hex
    parts = []
    for name, value in {"title": title, "year": str(year), "edition": edition}.items():
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                      f"{value}\r\n").encode("utf-8"))
    # A fixed upload name avoids interpreting local filename bytes as headers.
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="release.torrent"\r\n'
                  'Content-Type: application/x-bittorrent\r\n\r\n').encode("ascii") + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("ascii"))
    _show(_request(ctx.obj["acquisition_api_url"], "/torrent", body=b"".join(parts),
                   content_type="multipart/form-data; boundary=" + boundary))


@acquisition.command("add-release")
@click.argument("release_id")
@_film_options
@click.pass_context
def add_release(ctx: click.Context, release_id: str, title: str, year: int, edition: str) -> None:
    """Queue a RELEASE_ID returned by search."""
    _show(_request(ctx.obj["acquisition_api_url"], "/release",
                   body={"release_id": release_id, "title": title, "year": year, "edition": edition}))


def _revision_options(function):
    function = click.option("--revision", type=click.IntRange(min=1), required=True,
                            help="Current revision from acquisition list.")(function)
    return click.argument("identity")(function)


@acquisition.command("cancel")
@_revision_options
@click.pass_context
def cancel(ctx: click.Context, identity: str, revision: int) -> None:
    """Stop work and clean owned download staging; keep imported library films."""
    _show(_request(ctx.obj["acquisition_api_url"], f"/{quote(identity, safe='')}/cancel",
                   body={"revision": revision}))


@acquisition.command("retry")
@_revision_options
@click.pass_context
def retry(ctx: click.Context, identity: str, revision: int) -> None:
    """Explicitly retry an acquisition needing attention."""
    _show(_request(ctx.obj["acquisition_api_url"], f"/{quote(identity, safe='')}/retry",
                   body={"revision": revision}))


@acquisition.command("review")
@_revision_options
@click.option("--video", required=True, help="Relative video path from this acquisition's file list.")
@click.option("--subtitle", help="Relative selected subtitle path from its file list.")
@click.option("--skip-subtitles", is_flag=True, help="Explicitly continue without an external subtitle.")
@click.pass_context
def review(ctx: click.Context, identity: str, revision: int, video: str,
           subtitle: str | None, skip_subtitles: bool) -> None:
    """Resolve a download's video and subtitle selection.

    Omit subtitle options to choose the video first and refresh its subtitle list.
    """
    if subtitle and skip_subtitles:
        raise click.UsageError("Choose only one of --subtitle or --skip-subtitles")
    decision = ({"action": "use", "relative_path": subtitle} if subtitle else
                {"action": "skip"} if skip_subtitles else None)
    _show(_request(ctx.obj["acquisition_api_url"], f"/{quote(identity, safe='')}/review",
                   body={"revision": revision, "video_path": video, "subtitle_decision": decision}))


if __name__ == "__main__":
    acquisition()
