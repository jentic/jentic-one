"""Read the commits of a pushed git pack, for the force-push check.

A push's ``git-receive-pack`` body carries a pack (``gitformat-pack``) after
its ref commands. The broker cannot tell a fast-forward from a forced update
by the commands alone (both are ``old → new``), and github.com cannot compare
a commit that only exists in the uploaded pack. So the broker reads the pack
itself: :func:`index_pack` resolves the **commit** objects in it (inflating
every object, applying ``OFS_DELTA``/``REF_DELTA`` deltas whose base is an
in-pack commit) and records each commit's parents; :func:`walk_to_boundaries`
then walks from a ref's new commit through in-pack parents to the commits
outside the pack, which already exist upstream and can be compared there.

Every object is inflated (to find where the next one starts), and every
non-delta object hashed, but only commit contents are kept, so memory is
bounded by the commits in the push, not its blobs. A delta whose base is not
in the pack (a thin pack's base on the server) cannot be resolved; it is
counted in :attr:`PackIndex.unresolved`. git only deltifies trees and blobs
against such bases, so a commit hidden that way is a crafted pack: the walk
then sees its id as outside the pack, the upstream comparison cannot find it,
and the push is refused.

The pack's trailing checksum is verified and nothing may follow it, so the
pack read here is byte for byte the pack the upstream unpacks.

Pure module: synchronous (callers run it in a worker thread), no FastAPI,
httpx or DB imports.
"""

from __future__ import annotations

import hashlib
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import BinaryIO

_READ_CHUNK = 64 * 1024
_INFLATE_STEP = 1024 * 1024

_COMMIT, _TREE, _BLOB, _TAG, _OFS_DELTA, _REF_DELTA = 1, 2, 3, 4, 6, 7
_TYPE_NAMES = {_COMMIT: b"commit", _TREE: b"tree", _BLOB: b"blob", _TAG: b"tag"}
# The type of a delta whose base is not in the pack.
_UNKNOWN = 0


class PackError(ValueError):
    """A pack the broker cannot read; its message is safe to show the client."""


@dataclass(slots=True)
class PackIndex:
    """The commits of a pack: object name → parent names, plus what could not be resolved."""

    commits: dict[str, tuple[str, ...]] = field(default_factory=dict)
    object_count: int = 0
    unresolved: int = 0


class _Reader:
    """A byte reader over the pack that hashes everything it consumes."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._buffer = bytearray()
        self._eof = False
        self.offset = 0
        self.checksum = hashlib.sha1(usedforsecurity=False)

    def _fill(self, size: int) -> None:
        while len(self._buffer) < size and not self._eof:
            chunk = self._stream.read(_READ_CHUNK)
            if not chunk:
                self._eof = True
            else:
                self._buffer += chunk

    def _consume(self, size: int, *, hashed: bool = True) -> bytes:
        data = bytes(self._buffer[:size])
        del self._buffer[:size]
        if hashed:
            self.checksum.update(data)
        self.offset += size
        return data

    def read(self, size: int, *, hashed: bool = True) -> bytes:
        self._fill(size)
        if len(self._buffer) < size:
            raise PackError("the pack ends early")
        return self._consume(size, hashed=hashed)

    def byte(self) -> int:
        return self.read(1)[0]

    def at_end(self) -> bool:
        self._fill(1)
        return not self._buffer

    def inflate(
        self, expected: int, *, keep: bool, sink: Callable[[bytes], None] | None = None
    ) -> bytes:
        """Inflate one zlib stream of ``expected`` bytes; its data if ``keep``, else ``b""``."""
        inflater = zlib.decompressobj()
        produced = 0
        kept: list[bytes] = []

        def take(data: bytes) -> None:
            nonlocal produced
            produced += len(data)
            if produced > expected:
                raise PackError("a pack object inflates past its declared size")
            if sink is not None:
                sink(data)
            if keep:
                kept.append(data)

        while not inflater.eof:
            self._fill(1)
            if not self._buffer:
                raise PackError("the pack ends inside an object")
            chunk = bytes(self._buffer[:_READ_CHUNK])
            pending = chunk
            try:
                while True:
                    take(inflater.decompress(pending, _INFLATE_STEP))
                    if inflater.eof:
                        break
                    pending = inflater.unconsumed_tail
                    if not pending:
                        break
            except zlib.error:
                raise PackError("a pack object is not valid zlib data") from None
            used = len(chunk) - len(inflater.unused_data) if inflater.eof else len(chunk)
            self._consume(used)
        if produced != expected:
            raise PackError("a pack object inflates to less than its declared size")
        return b"".join(kept)


def _varint(data: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if pos >= len(data):
            raise PackError("a delta is truncated")
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, pos
        if shift > 63:
            raise PackError("a delta size is too large")


def apply_delta(base: bytes, delta: bytes) -> bytes:
    """Apply a git delta (``gitformat-pack`` "Deltified representation") to ``base``."""
    source_size, pos = _varint(delta, 0)
    target_size, pos = _varint(delta, pos)
    if source_size != len(base):
        raise PackError("a delta does not apply to its base")
    out = bytearray()
    while pos < len(delta):
        op = delta[pos]
        pos += 1
        if op & 0x80:
            offset = size = 0
            for bit in range(4):
                if op & (1 << bit):
                    if pos >= len(delta):
                        raise PackError("a delta is truncated")
                    offset |= delta[pos] << (8 * bit)
                    pos += 1
            for bit in range(3):
                if op & (1 << (4 + bit)):
                    if pos >= len(delta):
                        raise PackError("a delta is truncated")
                    size |= delta[pos] << (8 * bit)
                    pos += 1
            size = size or 0x10000
            if offset + size > len(base):
                raise PackError("a delta copies past the end of its base")
            out += base[offset : offset + size]
        elif op:
            if pos + op > len(delta):
                raise PackError("a delta is truncated")
            out += delta[pos : pos + op]
            pos += op
        else:
            raise PackError("a delta has a reserved instruction")
        if len(out) > target_size:
            raise PackError("a delta produces more than its declared size")
    if len(out) != target_size:
        raise PackError("a delta produces less than its declared size")
    return bytes(out)


def object_name(kind: bytes, content: bytes) -> str:
    """The SHA-1 object name of a ``kind`` object with ``content``."""
    digest = hashlib.sha1(kind + b" " + str(len(content)).encode() + b"\0", usedforsecurity=False)
    digest.update(content)
    return digest.hexdigest()


def commit_parents(content: bytes) -> tuple[str, ...]:
    """The parents of a commit object: the ``parent`` lines right after its ``tree`` line.

    git reads parents only there; a ``parent`` header anywhere else is not a
    parent. A commit without a ``tree`` line is refused.
    """
    lines = content.split(b"\n")
    if not lines or not lines[0].startswith(b"tree ") or len(lines[0]) != 45:
        raise PackError("a commit object has no tree")
    parents: list[str] = []
    for line in lines[1:]:
        if not line.startswith(b"parent "):
            break
        name = line[len(b"parent ") :].decode("ascii", "replace")
        if len(name) != 40 or any(c not in "0123456789abcdef" for c in name):
            raise PackError("a commit object has a malformed parent")
        parents.append(name)
    return tuple(parents)


def index_pack(stream: BinaryIO, *, max_commit_bytes: int) -> PackIndex:
    """Resolve the commits of the pack ``stream`` holds (from its ``PACK`` header to EOF).

    ``max_commit_bytes`` caps the commit contents kept in memory. Raises
    :class:`PackError` for a malformed pack, a bad checksum, bytes after the
    checksum, or too many commit bytes.
    """
    reader = _Reader(stream)
    if reader.read(4) != b"PACK":
        raise PackError("the push body has no pack")
    version = int.from_bytes(reader.read(4), "big")
    if version not in (2, 3):
        raise PackError(f"unsupported pack version {version}")
    count = int.from_bytes(reader.read(4), "big")

    index = PackIndex(object_count=count)
    offset_type: dict[int, int] = {}
    offset_commit: dict[int, str] = {}
    name_type: dict[str, int] = {}
    contents: dict[str, bytes] = {}
    kept_bytes = 0

    def keep_commit(offset: int, content: bytes) -> None:
        nonlocal kept_bytes
        kept_bytes += len(content)
        if kept_bytes > max_commit_bytes:
            raise PackError("the push carries more commit data than Jentic inspects")
        name = object_name(b"commit", content)
        contents[name] = content
        name_type[name] = _COMMIT
        offset_type[offset] = _COMMIT
        offset_commit[offset] = name
        index.commits[name] = commit_parents(content)

    for _ in range(count):
        start = reader.offset
        byte = reader.byte()
        kind = (byte >> 4) & 0x7
        size = byte & 0x0F
        shift = 4
        while byte & 0x80:
            byte = reader.byte()
            size |= (byte & 0x7F) << shift
            shift += 7
            if shift > 63:
                raise PackError("a pack object size is too large")
        if kind in _TYPE_NAMES:
            hasher = hashlib.sha1(
                _TYPE_NAMES[kind] + b" " + str(size).encode() + b"\0", usedforsecurity=False
            )
            data = reader.inflate(size, keep=kind == _COMMIT, sink=hasher.update)
            if kind == _COMMIT:
                keep_commit(start, data)
            else:
                name_type[hasher.hexdigest()] = kind
                offset_type[start] = kind
            continue
        if kind == _OFS_DELTA:
            byte = reader.byte()
            distance = byte & 0x7F
            while byte & 0x80:
                byte = reader.byte()
                distance = ((distance + 1) << 7) | (byte & 0x7F)
            base_offset = start - distance
            if distance <= 0 or base_offset not in offset_type:
                raise PackError("an offset delta names no earlier object")
            base_type = offset_type[base_offset]
            base_content = contents[offset_commit[base_offset]] if base_type == _COMMIT else None
        elif kind == _REF_DELTA:
            base_name = reader.read(20).hex()
            base_type = name_type.get(base_name, _UNKNOWN)
            base_content = contents.get(base_name) if base_type == _COMMIT else None
        else:
            raise PackError(f"a pack object has the reserved type {kind}")
        delta = reader.inflate(size, keep=base_content is not None)
        if base_content is not None:
            keep_commit(start, apply_delta(base_content, delta))
            continue
        offset_type[start] = base_type
        if base_type == _UNKNOWN:
            index.unresolved += 1

    expected = reader.checksum.digest()
    if reader.read(20, hashed=False) != expected:
        raise PackError("the pack checksum does not match")
    if not reader.at_end():
        raise PackError("the push body continues after its pack")
    return index


@dataclass(frozen=True, slots=True)
class Walk:
    """Where a ref's new commit leads in the pack.

    ``reached_old`` is set when the walk met the ref's old commit inside the
    pack (a fast-forward already proven). ``boundaries`` are the commits
    outside the pack the walk reached, the end of the first-parent line first.
    """

    reached_old: bool
    boundaries: tuple[str, ...]


def walk_to_boundaries(index: PackIndex, new: str, old: str) -> Walk:
    """Walk from ``new`` through in-pack parents to the commits outside the pack.

    Depth first, first parent first, so the first boundary is where the
    first-parent line leaves the pack: for an ordinary push that is the ref's
    old commit or a descendant of it, and the check compares it first.
    """
    seen: set[str] = set()
    boundaries: list[str] = []
    stack: list[str] = [new]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        if name == old:
            return Walk(reached_old=True, boundaries=())
        parents = index.commits.get(name)
        if parents is None:
            boundaries.append(name)
            continue
        stack.extend(reversed(parents))
    return Walk(reached_old=False, boundaries=tuple(boundaries))


__all__ = [
    "PackError",
    "PackIndex",
    "Walk",
    "apply_delta",
    "commit_parents",
    "index_pack",
    "object_name",
    "walk_to_boundaries",
]
