"""Bounded torrent metadata parsing. No paths are opened and no URLs fetched."""

import base64
from dataclasses import dataclass, field
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit


MAX_TORRENT_BYTES = 2 * 1024 * 1024
MAX_MAGNET_LENGTH = 16 * 1024
MAX_TORRENT_FILES = 10_000
_RESERVED = re.compile(r"^(CON|CONIN\$|CONOUT\$|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.I)


class SourceError(ValueError):
    """A safe message which never includes a tracker URL or source payload."""


@dataclass(frozen=True)
class TorrentFile:
    path: str
    size: int


@dataclass(frozen=True)
class TorrentSource:
    info_hash: str
    name: str
    files: tuple[TorrentFile, ...] = ()
    total_size: int = 0
    magnet: str | None = field(default=None, repr=False)
    torrent_bytes: bytes | None = field(default=None, repr=False)


def normalize_info_hash(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", value):
        raise SourceError("A single v1 torrent info hash is required.")
    return value.lower()


def validate_relative_path(value: str) -> str:
    """Use Windows-safe relative names on every OS; never normalize traversal away."""
    if not isinstance(value, str) or not value or len(value) > 4096 or "\\" in value:
        raise SourceError("Torrent contains an unsafe file path.")
    for part in value.split("/"):
        try:
            encoded_length = len(part.encode("utf-8"))
        except UnicodeError:
            raise SourceError("Torrent contains an invalid file name.") from None
        if (
            not part or part in {".", ".."} or part[-1:] in {".", " "}
            or any(ord(c) < 32 or c in '<>:"|?*' for c in part)
            or _RESERVED.match(part) or encoded_length > 255
            or part.casefold() in {".acquisition.json", ".scene-recall-imported"}
        ):
            raise SourceError("Torrent contains an unsafe file path.")
    return value


def parse_magnet(value: str) -> TorrentSource:
    if not isinstance(value, str) or not value or len(value) > MAX_MAGNET_LENGTH:
        raise SourceError("Magnet link is empty or exceeds the 16 KiB limit.")
    value = value.strip()
    if any(ord(c) < 32 for c in value) or re.search(r"%(?![0-9A-Fa-f]{2})", value):
        raise SourceError("Magnet link is malformed.")
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "magnet" or parsed.netloc or parsed.path or parsed.fragment:
            raise ValueError
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=100)
    except ValueError:
        raise SourceError("Provide a magnet link, not a browser or download URL.") from None
    hashes: set[str] = set()
    name = ""
    for key, item in pairs:
        if any(ord(c) < 32 for c in key + item):
            raise SourceError("Magnet link is malformed.")
        if key == "xt" and item.lower().startswith("urn:btih:"):
            encoded = item[9:]
            if re.fullmatch(r"[0-9a-fA-F]{40}", encoded):
                hashes.add(encoded.lower())
            elif re.fullmatch(r"[A-Za-z2-7]{32}", encoded):
                hashes.add(base64.b32decode(encoded.upper()).hex())
            else:
                raise SourceError("Magnet link has an invalid v1 info hash.")
        elif key == "dn" and not name:
            name = item[:500]
    if not hashes:
        raise SourceError("Use a v1 or hybrid torrent; v2-only magnets are not supported.")
    if len(hashes) != 1:
        raise SourceError("Magnet link contains conflicting torrent identities.")
    info_hash = hashes.pop()
    canonical = [(key, f"urn:btih:{info_hash}" if key == "xt" and item.lower().startswith("urn:btih:") else item) for key, item in pairs]
    return TorrentSource(info_hash=info_hash, name=name or info_hash, magnet="magnet:?" + urlencode(canonical))


class _Bencode:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0
        self.nodes = 0
        self.info_span: tuple[int, int] | None = None

    def parse(self, depth: int = 0):
        self.nodes += 1
        if depth > 32 or self.nodes > 200_000 or self.pos >= len(self.data):
            raise SourceError("Torrent metadata is malformed or too complex.")
        token = self.data[self.pos:self.pos + 1]
        if token == b"i":
            end = self.data.find(b"e", self.pos + 1)
            raw = self.data[self.pos + 1:end]
            if end < 0 or len(raw) > 20 or not re.fullmatch(rb"0|-?[1-9][0-9]*", raw):
                raise SourceError("Torrent metadata contains an invalid integer.")
            self.pos = end + 1
            return int(raw)
        if token in {b"d", b"l"}:
            self.pos += 1
            result = {} if token == b"d" else []
            while self.data[self.pos:self.pos + 1] != b"e":
                if token == b"l":
                    result.append(self.parse(depth + 1))
                else:
                    key = self.parse(depth + 1)
                    if not isinstance(key, bytes) or key in result:
                        raise SourceError("Torrent metadata contains an invalid or duplicate key.")
                    start = self.pos
                    result[key] = self.parse(depth + 1)
                    if depth == 0 and key == b"info":
                        self.info_span = (start, self.pos)
            self.pos += 1
            return result
        colon = self.data.find(b":", self.pos, self.pos + 12)
        raw = self.data[self.pos:colon]
        if colon < 0 or not re.fullmatch(rb"0|[1-9][0-9]*", raw):
            raise SourceError("Torrent metadata contains an invalid string.")
        length = int(raw)
        start = colon + 1
        self.pos = start + length
        if self.pos > len(self.data):
            raise SourceError("Torrent metadata is truncated.")
        return self.data[start:self.pos]


def _component(value) -> str:
    if not isinstance(value, bytes):
        raise SourceError("Torrent contains an invalid file name.")
    try:
        decoded = value.decode("utf-8")
    except UnicodeError:
        raise SourceError("Torrent file names must use valid UTF-8.") from None
    if "/" in decoded:
        raise SourceError("Torrent contains an unsafe file path.")
    return validate_relative_path(decoded)


def _regular_file(entry: dict) -> None:
    attr = entry.get(b"attr", b"")
    if not isinstance(attr, bytes) or b"l" in attr or b"symlink path" in entry:
        raise SourceError("Torrent symlinks are not supported.")


def _validate_v2_tree(tree, prefix: tuple[str, ...] = ()) -> None:
    if not isinstance(tree, dict):
        raise SourceError("Torrent file tree is malformed.")
    for key, value in tree.items():
        if key == b"":
            if not prefix or not isinstance(value, dict):
                raise SourceError("Torrent file tree is malformed.")
            _regular_file(value)
        else:
            _validate_v2_tree(value, prefix + (_component(key),))


def parse_torrent(data: bytes) -> TorrentSource:
    if not isinstance(data, bytes) or not data or len(data) > MAX_TORRENT_BYTES:
        raise SourceError("Torrent file is empty or exceeds the 2 MiB limit.")
    decoder = _Bencode(data)
    root = decoder.parse()
    if decoder.pos != len(data) or not isinstance(root, dict) or decoder.info_span is None:
        raise SourceError("Torrent file must contain one metadata dictionary.")
    info = root.get(b"info")
    if not isinstance(info, dict):
        raise SourceError("Torrent info dictionary is missing.")
    if b"pieces" not in info:
        raise SourceError("Use a v1 or hybrid torrent; v2-only torrents are not supported.")
    pieces = info[b"pieces"]
    piece_length = info.get(b"piece length")
    if not isinstance(pieces, bytes) or len(pieces) % 20 or not isinstance(piece_length, int) or piece_length <= 0:
        raise SourceError("Torrent piece metadata is invalid.")
    name = _component(info.get(b"name"))
    if b"name.utf-8" in info:
        name = _component(info[b"name.utf-8"])
    _regular_file(info)
    files: list[TorrentFile] = []
    if b"files" in info:
        entries = info[b"files"]
        if b"length" in info or not isinstance(entries, list) or not 1 <= len(entries) <= MAX_TORRENT_FILES:
            raise SourceError("Torrent file list is invalid or exceeds 10,000 files.")
        for entry in entries:
            if not isinstance(entry, dict):
                raise SourceError("Torrent file list is malformed.")
            _regular_file(entry)
            parts = entry.get(b"path")
            if not isinstance(parts, list) or not parts:
                raise SourceError("Torrent contains an invalid file path.")
            path = "/".join([name, *(_component(part) for part in parts)])
            if b"path.utf-8" in entry:
                utf8_parts = entry[b"path.utf-8"]
                if not isinstance(utf8_parts, list) or not utf8_parts:
                    raise SourceError("Torrent contains an invalid file path.")
                path = "/".join([name, *(_component(part) for part in utf8_parts)])
            size = entry.get(b"length")
            if not isinstance(size, int) or size < 0:
                raise SourceError("Torrent contains an invalid file size.")
            files.append(TorrentFile(validate_relative_path(path), size))
    else:
        size = info.get(b"length")
        if not isinstance(size, int) or size < 0:
            raise SourceError("Torrent contains an invalid file size.")
        files.append(TorrentFile(name, size))
    paths: set[str] = set()
    for file in files:
        path = file.path.casefold()
        if path in paths or any("/".join(path.split("/")[:i]) in paths for i in range(1, len(path.split("/")))):
            raise SourceError("Torrent contains conflicting file paths.")
        paths.add(path)
    for path in paths:
        if any("/".join(path.split("/")[:i]) in paths for i in range(1, len(path.split("/")))):
            raise SourceError("Torrent contains conflicting file paths.")
    if b"file tree" in info:
        _validate_v2_tree(info[b"file tree"])
    total_size = sum(file.size for file in files)
    if total_size <= 0 or len(pieces) // 20 != (total_size + piece_length - 1) // piece_length:
        raise SourceError("Torrent size does not match its piece metadata.")
    start, end = decoder.info_span
    return TorrentSource(hashlib.sha1(data[start:end]).hexdigest(), name, tuple(files), total_size, torrent_bytes=data)
