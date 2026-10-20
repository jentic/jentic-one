"""The force-push check's pack reader: commits, deltas, thin packs, and the ancestry walk."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import zlib
from pathlib import Path

import pytest

from jentic_one.broker.core.git_pack import (
    PackError,
    apply_delta,
    commit_parents,
    index_pack,
    object_name,
    walk_to_boundaries,
)

_COMMIT, _TREE, _BLOB, _OFS_DELTA, _REF_DELTA = 1, 2, 3, 6, 7
_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # the empty tree
_OUTSIDE = "e" * 40
_LIMIT = 1 << 20


def _commit(*parents: str, message: str = "m") -> bytes:
    lines = [f"tree {_TREE_SHA}"]
    lines += [f"parent {p}" for p in parents]
    lines += ["author A <a@x> 0 +0000", "committer A <a@x> 0 +0000", "", message, ""]
    return "\n".join(lines).encode()


def _header(kind: int, size: int) -> bytes:
    first = (kind << 4) | (size & 0x0F)
    size >>= 4
    out = bytearray()
    while size:
        out.append(first | 0x80)
        first = size & 0x7F
        size >>= 7
    out.append(first)
    return bytes(out)


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _ofs(distance: int) -> bytes:
    out = [distance & 0x7F]
    distance >>= 7
    while distance:
        distance -= 1
        out.insert(0, 0x80 | (distance & 0x7F))
        distance >>= 7
    return bytes(out)


def _delta(base: bytes, target: bytes) -> bytes:
    """A delta copying ``base``'s common prefix with ``target`` and inserting the rest."""
    common = 0
    while common < min(len(base), len(target)) and base[common] == target[common]:
        common += 1
    out = bytearray(_varint(len(base)) + _varint(len(target)))
    if common:
        out += bytes([0x80 | 0x10 | 0x20]) + common.to_bytes(2, "little")
    rest = target[common:]
    for start in range(0, len(rest), 127):
        piece = rest[start : start + 127]
        out += bytes([len(piece)]) + piece
    return bytes(out)


class _Pack:
    def __init__(self) -> None:
        self.objects: list[bytes] = []
        self.offsets: list[int] = []
        self._size = 12

    def _add(self, raw: bytes) -> int:
        self.offsets.append(self._size)
        self.objects.append(raw)
        self._size += len(raw)
        return len(self.objects) - 1

    def whole(self, kind: int, content: bytes) -> int:
        return self._add(_header(kind, len(content)) + zlib.compress(content))

    def ofs_delta(self, base_index: int, delta: bytes) -> int:
        distance = self._size - self.offsets[base_index]
        return self._add(_header(_OFS_DELTA, len(delta)) + _ofs(distance) + zlib.compress(delta))

    def ref_delta(self, base_name: str, delta: bytes) -> int:
        raw = _header(_REF_DELTA, len(delta)) + bytes.fromhex(base_name) + zlib.compress(delta)
        return self._add(raw)

    def bytes(self, *, count: int | None = None) -> bytes:
        n = len(self.objects) if count is None else count
        body = b"PACK" + (2).to_bytes(4, "big") + n.to_bytes(4, "big") + b"".join(self.objects)
        return body + hashlib.sha1(body).digest()


def _name(content: bytes) -> str:
    return object_name(b"commit", content)


# ── commits and parents ──────────────────────────────────────────────────────


def test_commit_parents_are_read_only_after_the_tree_line() -> None:
    content = _commit("a" * 40, "b" * 40) + b"parent " + b"c" * 40 + b"\n"
    assert commit_parents(content) == ("a" * 40, "b" * 40)
    with pytest.raises(PackError, match="no tree"):
        commit_parents(b"parent " + b"a" * 40 + b"\n")
    with pytest.raises(PackError, match="malformed parent"):
        commit_parents(f"tree {_TREE_SHA}\nparent xyz\n".encode())


def test_a_chain_of_commits_is_indexed_and_walked() -> None:
    first = _commit(_OUTSIDE, message="1")
    second = _commit(_name(first), message="2")
    third = _commit(_name(second), message="3")
    pack = _Pack()
    for content in (first, second, third):
        pack.whole(_COMMIT, content)
    pack.whole(_BLOB, b"blob data")
    pack.whole(_TREE, b"")

    index = index_pack(io.BytesIO(pack.bytes()), max_commit_bytes=_LIMIT)

    assert index.object_count == 5
    assert index.unresolved == 0
    assert index.commits[_name(third)] == (_name(second),)
    walk = walk_to_boundaries(index, _name(third), _name(first))
    assert walk.reached_old
    outside = walk_to_boundaries(index, _name(third), "f" * 40)
    assert not outside.reached_old
    assert outside.boundaries == (_OUTSIDE,)


def test_the_first_parent_boundary_comes_first() -> None:
    side = "1" * 40
    main = "2" * 40
    merge = _commit(main, side)
    pack = _Pack()
    pack.whole(_COMMIT, merge)

    walk = walk_to_boundaries(
        index_pack(io.BytesIO(pack.bytes()), max_commit_bytes=_LIMIT), _name(merge), "9" * 40
    )

    assert walk.boundaries == (main, side)


def test_a_new_commit_outside_the_pack_is_its_own_boundary() -> None:
    pack = _Pack()
    index = index_pack(io.BytesIO(pack.bytes()), max_commit_bytes=_LIMIT)

    assert index.object_count == 0
    assert walk_to_boundaries(index, "1" * 40, "2" * 40).boundaries == ("1" * 40,)
    assert walk_to_boundaries(index, "2" * 40, "2" * 40).reached_old


# ── deltas ───────────────────────────────────────────────────────────────────


def test_apply_delta_copies_and_inserts() -> None:
    base = b"hello world, this is the base"
    target = b"hello world, and a new ending"
    assert apply_delta(base, _delta(base, target)) == target
    with pytest.raises(PackError, match="does not apply"):
        apply_delta(b"short", _delta(base, target))
    with pytest.raises(PackError, match="reserved instruction"):
        apply_delta(b"", _varint(0) + _varint(1) + b"\x00")


def test_offset_and_ref_deltas_against_in_pack_commits_resolve() -> None:
    base = _commit(_OUTSIDE, message="base commit message")
    ofs_target = _commit(_name(base), message="base commit message, deltified by offset")
    ref_target = _commit(_name(ofs_target), message="base commit message, deltified by name")
    pack = _Pack()
    base_index = pack.whole(_COMMIT, base)
    pack.ofs_delta(base_index, _delta(base, ofs_target))
    pack.ref_delta(_name(ofs_target), _delta(ofs_target, ref_target))

    index = index_pack(io.BytesIO(pack.bytes()), max_commit_bytes=_LIMIT)

    assert index.unresolved == 0
    assert index.commits[_name(ofs_target)] == (_name(base),)
    assert index.commits[_name(ref_target)] == (_name(ofs_target),)
    walk = walk_to_boundaries(index, _name(ref_target), "f" * 40)
    assert walk.boundaries == (_OUTSIDE,)


def test_a_thin_pack_delta_against_an_outside_base_is_unresolved() -> None:
    upstream_commit = _commit(_OUTSIDE, message="on the server")
    hidden = _commit(_name(upstream_commit), message="on the server, then changed")
    pack = _Pack()
    pack.ref_delta(_name(upstream_commit), _delta(upstream_commit, hidden))
    pack.ref_delta("d" * 40, b"\x05\x05\x05hello")

    index = index_pack(io.BytesIO(pack.bytes()), max_commit_bytes=_LIMIT)

    assert index.unresolved == 2
    assert _name(hidden) not in index.commits
    walk = walk_to_boundaries(index, _name(hidden), "f" * 40)
    assert walk.boundaries == (_name(hidden),)


def test_an_offset_delta_must_name_an_earlier_object() -> None:
    pack = _Pack()
    pack._add(_header(_OFS_DELTA, 3) + _ofs(1) + zlib.compress(b"\x00\x00\x00"))
    with pytest.raises(PackError, match="no earlier object"):
        index_pack(io.BytesIO(pack.bytes()), max_commit_bytes=_LIMIT)


# ── malformed packs ──────────────────────────────────────────────────────────


def test_malformed_packs_are_refused() -> None:
    pack = _Pack()
    pack.whole(_COMMIT, _commit(_OUTSIDE))
    good = pack.bytes()

    cases = {
        "no pack": b"NOPE" + good[4:],
        "checksum": good[:-1] + bytes([good[-1] ^ 1]),
        "continues after": good + b"extra",
        "ends": good[:-25],
        # A count past the objects reads the checksum as an object.
        "zlib data|ends": pack.bytes(count=2),
    }
    for fragment, data in cases.items():
        with pytest.raises(PackError, match=fragment):
            index_pack(io.BytesIO(data), max_commit_bytes=_LIMIT)


def test_an_object_that_inflates_to_the_wrong_size_is_refused() -> None:
    pack = _Pack()
    pack._add(_header(_BLOB, 3) + zlib.compress(b"four"))
    with pytest.raises(PackError, match="past its declared size"):
        index_pack(io.BytesIO(pack.bytes()), max_commit_bytes=_LIMIT)


def test_commit_bytes_are_capped() -> None:
    pack = _Pack()
    pack.whole(_COMMIT, _commit(_OUTSIDE, message="x" * 500))
    with pytest.raises(PackError, match="more commit data"):
        index_pack(io.BytesIO(pack.bytes()), max_commit_bytes=100)


# ── real git packs ───────────────────────────────────────────────────────────


def _git(cwd: Path, *args: str, stdin: bytes | None = None) -> bytes:
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(cwd),
        "GIT_AUTHOR_NAME": "A",
        "GIT_AUTHOR_EMAIL": "a@x",
        "GIT_COMMITTER_NAME": "A",
        "GIT_COMMITTER_EMAIL": "a@x",
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, input=stdin, capture_output=True, check=True
    ).stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_packs_written_by_git_are_read(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    text = "".join(f"line {i}\n" for i in range(400))
    (tmp_path / "f.txt").write_text(text)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    base = _git(tmp_path, "rev-parse", "HEAD").decode().strip()
    for i in range(3):
        (tmp_path / "f.txt").write_text(text.replace(f"line {i}\n", f"changed {i}\n"))
        _git(tmp_path, "commit", "-q", "-am", f"change {i}")
    _git(tmp_path, "checkout", "-q", "-b", "side", base)
    (tmp_path / "g.txt").write_text("side\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "side")
    _git(tmp_path, "checkout", "-q", "main")
    _git(tmp_path, "merge", "-q", "--no-edit", "side")
    tip = _git(tmp_path, "rev-parse", "HEAD").decode().strip()

    expected = {}
    for line in _git(tmp_path, "rev-list", "--parents", tip, f"^{base}").decode().splitlines():
        name, *parents = line.split()
        expected[name] = tuple(parents)

    for flags in ([], ["--thin"]):
        pack = _git(
            tmp_path,
            "pack-objects",
            "--stdout",
            "--revs",
            *flags,
            stdin=f"{tip}\n^{base}\n".encode(),
        )
        index = index_pack(io.BytesIO(pack), max_commit_bytes=_LIMIT)
        assert index.commits == expected
        walk = walk_to_boundaries(index, tip, base)
        assert walk.reached_old
    assert index.unresolved >= 1  # the thin pack's blob delta against the base's blob
