import base64
import hashlib

import pytest

from pipeline.acquisition.settings import AcquisitionSettings, load_settings
from pipeline.acquisition.sources import (
    MAX_TORRENT_BYTES, SourceError, parse_magnet, parse_torrent, validate_relative_path,
)


def bencode(value):
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    return b"d" + b"".join(bencode(key) + bencode(item) for key, item in value.items()) + b"e"


def torrent_info(**overrides):
    info = {b"name": b"sample.mkv", b"length": 3, b"piece length": 16384, b"pieces": b"x" * 20}
    info.update({key.encode(): value for key, value in overrides.items()})
    return info


def test_hashes_exact_info_bytes_including_unsorted_keys_and_binary_pieces():
    info = torrent_info()
    raw_info = bencode(info)
    payload = bencode({b"announce": b"https://tracker.invalid/private-key", b"info": info})
    source = parse_torrent(payload)
    assert source.info_hash == hashlib.sha1(raw_info).hexdigest()
    assert source.files[0].path == "sample.mkv"
    assert source.total_size == 3
    assert source.torrent_bytes == payload
    assert "private-key" not in repr(source)


def test_base32_and_hex_magnets_share_identity():
    raw_hash = b"\xab" * 20
    base32 = base64.b32encode(raw_hash).decode().lower()
    source = parse_magnet(f"magnet:?xt=urn:btih:{base32}&dn=Sample&tr=https%3A%2F%2Ftracker.invalid%2Fsecret")
    assert source.info_hash == raw_hash.hex()
    assert source.name == "Sample"
    assert parse_magnet(f"magnet:?xt=urn:btih:{raw_hash.hex().upper()}").info_hash == source.info_hash
    assert "secret" not in repr(source)


def test_hybrid_magnet_uses_its_v1_hash():
    source = parse_magnet("magnet:?xt=urn:btmh:1220" + "a" * 64 + "&xt=urn:btih:" + "b" * 40)
    assert source.info_hash == "b" * 40


@pytest.mark.parametrize("value", [
    "https://example.com/release.torrent", "magnet:?xt=urn:btih:short",
    "magnet:?xt=urn:btmh:1220" + "a" * 64,
    "magnet:?xt=urn:btih:" + "a" * 40 + "&xt=urn:btih:" + "b" * 40,
    "magnet:?xt=urn:btih:" + "a" * 40 + "&dn=bad%0Aname",
    "magnet:?xt=urn:btih:" + "a" * 40 + "&dn=bad%GGname",
    "magnet://host?xt=urn:btih:" + "a" * 40,
])
def test_rejects_invalid_magnet_without_echoing_it(value):
    with pytest.raises(SourceError) as exc:
        parse_magnet(value)
    assert value not in str(exc.value)


@pytest.mark.parametrize("path", [
    "../film.mkv", "/film.mkv", "root/../film.mkv", "C:/film.mkv", "root\\film.mkv",
    "root//film.mkv", "root/CON.srt", "root/CONOUT$", "root/name.", "root/name ",
    "root/film.mkv:stream", "root/\x00movie", "root/\ud800", "root/LPT¹.txt",
    ".acquisition.json", "release/.SCENE-RECALL-IMPORTED",
])
def test_windows_unsafe_paths_are_rejected_on_every_platform(path):
    with pytest.raises(SourceError):
        validate_relative_path(path)


def test_multi_file_torrent_preserves_safe_subtitle_paths():
    info = torrent_info()
    del info[b"length"]
    info[b"name"] = b"Film"
    info[b"files"] = [{b"length": 3, b"path": [b"Film.mkv"]}, {b"length": 1, b"path": [b"Subs", b"en.srt"]}]
    source = parse_torrent(bencode({b"info": info}))
    assert [(item.path, item.size) for item in source.files] == [("Film/Film.mkv", 3), ("Film/Subs/en.srt", 1)]


@pytest.mark.parametrize("paths", [[b"a", b"A"], [b"sub/file", b"sub"]])
def test_duplicate_or_file_directory_collisions_are_rejected(paths):
    info = torrent_info()
    del info[b"length"]
    info[b"files"] = [{b"length": 1, b"path": path.split(b"/")} for path in paths]
    with pytest.raises(SourceError, match="conflicting"):
        parse_torrent(bencode({b"info": info}))


@pytest.mark.parametrize("payload", [b"", b"i01e", b"d4:infode4:infodee", b"d4:info", b"deextra", b"l" * 40 + b"e" * 40])
def test_malformed_and_excessively_nested_bencode(payload):
    with pytest.raises(SourceError):
        parse_torrent(payload)


def test_oversized_torrent_and_piece_count_mismatch():
    with pytest.raises(SourceError, match="2 MiB"):
        parse_torrent(b"x" * (MAX_TORRENT_BYTES + 1))
    with pytest.raises(SourceError, match="piece metadata"):
        parse_torrent(bencode({b"info": torrent_info(pieces=b"x" * 40)}))


def test_v1_and_hybrid_symlinks_are_rejected():
    info = torrent_info(attr=b"l")
    with pytest.raises(SourceError, match="symlinks"):
        parse_torrent(bencode({b"info": info}))
    info = torrent_info()
    info[b"file tree"] = {b"sample.mkv": {b"": {b"attr": b"l", b"length": 3}}}
    with pytest.raises(SourceError, match="symlinks"):
        parse_torrent(bencode({b"info": info}))


def test_hybrid_file_tree_also_checks_traversal():
    info = torrent_info()
    info[b"file tree"] = {b"..": {b"sample.mkv": {b"": {b"length": 3}}}}
    with pytest.raises(SourceError, match="unsafe"):
        parse_torrent(bencode({b"info": info}))


def test_v2_only_torrent_is_explicitly_unsupported():
    info = torrent_info()
    del info[b"pieces"]
    with pytest.raises(SourceError, match="v2-only"):
        parse_torrent(bencode({b"info": info}))


def test_settings_disabled_without_explicit_urls_and_secret_free_repr(monkeypatch):
    for key in ("QBITTORRENT_URL", "PROWLARR_URL", "PROWLARR_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    assert not load_settings().downloads_enabled
    assert not load_settings().search_enabled
    settings = AcquisitionSettings(qbittorrent_url="http://127.0.0.1:8080/", qbittorrent_password="unique-password", prowlarr_api_key="unique-key")
    assert settings.qbittorrent_url == "http://127.0.0.1:8080"
    assert "unique-password" not in repr(settings)
    assert "unique-key" not in repr(settings)


@pytest.mark.parametrize("url", ["file:///tmp/service", "https://name:secret@host", "http://host/?key=secret", "http://host/#secret", "http://host/../admin", "http://host\\@other", "http://host:bad"])
def test_unsafe_service_urls_rejected_without_secret_echo(url):
    with pytest.raises(ValueError) as exc:
        AcquisitionSettings(qbittorrent_url=url)
    assert "secret" not in str(exc.value)
