"""Unit tests for the spec-mirror filesystem helpers (no DB, no app wiring).

The DB-reading half of ``SpecMirrorService`` is covered by the integration
tests (``tests/integration/registry/services/test_spec_mirror.py``); these
tests exercise the pure path/write/prune helpers against a temp directory.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from jentic_one.registry.services.spec_mirror_service import (
    _decode_segment,
    _encode_segment,
    _prune_unknown_dirs,
    _remove_version_dir,
    _RevisionSnapshot,
    _write_version_dir,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("acme.com", "acme.com"),
        ("a/b", "a%2Fb"),
        ("a b", "a%20b"),
        ("..", "%2E%2E"),
        (".", "%2E"),
        ("v1", "v1"),
        # Uppercase is encoded so case-only-distinct identifiers can't collide
        # into one directory on case-insensitive filesystems (macOS, Windows).
        ("Stripe", "%53tripe"),
        ("COM", "%43%4F%4D"),
        # Windows reserved device names (incl. with an extension) get their
        # first character encoded so the directory stays creatable there.
        ("con", "%63on"),
        ("nul.json", "%6Eul.json"),
        ("com1", "%63om1"),
        ("console", "console"),
    ],
)
def test_encode_segment_produces_safe_single_segments(raw: str, expected: str) -> None:
    encoded = _encode_segment(raw)
    assert encoded == expected
    assert "/" not in encoded
    assert encoded not in (".", "..")


@pytest.mark.parametrize("raw", ["acme.com", "a/b", "Stripe", "con", "nul.json", "%2E", ".."])
def test_encode_segment_round_trips(raw: str) -> None:
    assert _decode_segment(_encode_segment(raw)) == raw


def _snapshot(
    revision_id: str, state: str, content: dict[str, object] | None = None
) -> _RevisionSnapshot:
    return _RevisionSnapshot(
        revision_id=revision_id,
        state=state,
        meta={"revision_id": revision_id, "state": state},
        content=content or {"openapi": "3.0.0", "info": {"title": revision_id}},
    )


_SEGMENTS = ("acme.com", "widget", "v1")


def test_write_version_dir_places_revisions_by_state(tmp_path: Path) -> None:
    _write_version_dir(
        tmp_path,
        _SEGMENTS,
        [_snapshot("rev-a", "published"), _snapshot("rev-b", "archived")],
    )

    version_dir = tmp_path / "acme.com" / "widget" / "v1"
    spec_a = version_dir / "published" / "rev-a.json"
    spec_b = version_dir / "archived" / "rev-b.json"
    assert json.loads(spec_a.read_text())["info"]["title"] == "rev-a"
    assert json.loads(spec_b.read_text())["info"]["title"] == "rev-b"
    meta_a = json.loads((version_dir / "rev-a.meta.json").read_text())
    assert meta_a["revision_id"] == "rev-a"
    assert meta_a["state"] == "published"


def test_write_version_dir_meta_carries_verifiable_mirror_digest(tmp_path: Path) -> None:
    """``mirror_digest`` hashes the mirrored file's bytes, so a consumer can
    verify the spec it reads (``sha`` refers to the original source bytes)."""
    _write_version_dir(tmp_path, _SEGMENTS, [_snapshot("rev-a", "published")])

    version_dir = tmp_path / "acme.com" / "widget" / "v1"
    spec_bytes = (version_dir / "published" / "rev-a.json").read_bytes()
    meta = json.loads((version_dir / "rev-a.meta.json").read_text())
    assert meta["mirror_digest"] == f"sha256:{hashlib.sha256(spec_bytes).hexdigest()}"


def test_write_version_dir_moves_file_on_state_change(tmp_path: Path) -> None:
    _write_version_dir(tmp_path, _SEGMENTS, [_snapshot("rev-a", "draft")])
    _write_version_dir(tmp_path, _SEGMENTS, [_snapshot("rev-a", "published")])

    version_dir = tmp_path / "acme.com" / "widget" / "v1"
    assert (version_dir / "published" / "rev-a.json").is_file()
    # The draft copy is pruned and its emptied state dir removed with it.
    assert not (version_dir / "draft").exists()


def test_write_version_dir_prunes_deleted_revisions(tmp_path: Path) -> None:
    _write_version_dir(
        tmp_path,
        _SEGMENTS,
        [_snapshot("rev-a", "published"), _snapshot("rev-b", "archived")],
    )
    _write_version_dir(tmp_path, _SEGMENTS, [_snapshot("rev-a", "published")])

    version_dir = tmp_path / "acme.com" / "widget" / "v1"
    assert (version_dir / "published" / "rev-a.json").is_file()
    assert not (version_dir / "archived").exists()
    assert not (version_dir / "rev-b.meta.json").exists()


def test_write_version_dir_prunes_out_of_enum_state_dirs(tmp_path: Path) -> None:
    """A file left in a directory outside the known lifecycle states (e.g. a
    revision that once carried an out-of-enum state) is still pruned."""
    _write_version_dir(tmp_path, _SEGMENTS, [_snapshot("rev-a", "published")])
    version_dir = tmp_path / "acme.com" / "widget" / "v1"
    weird = version_dir / "superseded"
    weird.mkdir()
    (weird / "rev-old.json").write_text("{}")

    _write_version_dir(tmp_path, _SEGMENTS, [_snapshot("rev-a", "published")])

    assert not weird.exists()
    assert (version_dir / "published" / "rev-a.json").is_file()


def test_write_version_dir_prunes_orphaned_tmp_files(tmp_path: Path) -> None:
    """Temp files left by a crashed write are removed on the next sync, both
    inside state dirs and at the version level (meta temps)."""
    _write_version_dir(tmp_path, _SEGMENTS, [_snapshot("rev-a", "published")])
    version_dir = tmp_path / "acme.com" / "widget" / "v1"
    (version_dir / "published" / ".rev-a.json.abc123.tmp").write_text("{")
    (version_dir / ".rev-a.meta.json.abc123.tmp").write_text("{")

    _write_version_dir(tmp_path, _SEGMENTS, [_snapshot("rev-a", "published")])

    assert not (version_dir / "published" / ".rev-a.json.abc123.tmp").exists()
    assert not (version_dir / ".rev-a.meta.json.abc123.tmp").exists()


def test_write_version_dir_skips_rewrite_when_unchanged(tmp_path: Path) -> None:
    snap = _snapshot("rev-a", "published")
    _write_version_dir(tmp_path, _SEGMENTS, [snap])
    spec_path = tmp_path / "acme.com" / "widget" / "v1" / "published" / "rev-a.json"
    first_mtime = spec_path.stat().st_mtime_ns

    _write_version_dir(tmp_path, _SEGMENTS, [snap])

    assert spec_path.stat().st_mtime_ns == first_mtime


def test_write_version_dir_heals_corrupted_spec_file(tmp_path: Path) -> None:
    """A truncated/tampered spec file is rewritten even when its sidecar still
    matches — presence of the file alone must not suppress the write."""
    snap = _snapshot("rev-a", "published")
    _write_version_dir(tmp_path, _SEGMENTS, [snap])
    spec_path = tmp_path / "acme.com" / "widget" / "v1" / "published" / "rev-a.json"
    spec_path.write_text('{"truncated": tru')

    _write_version_dir(tmp_path, _SEGMENTS, [snap])

    assert json.loads(spec_path.read_text())["info"]["title"] == "rev-a"


def test_write_version_dir_none_removes_the_api(tmp_path: Path) -> None:
    _write_version_dir(tmp_path, _SEGMENTS, [_snapshot("rev-a", "published")])
    _write_version_dir(tmp_path, _SEGMENTS, None)

    # The version dir and its now-empty parents are gone; the root remains.
    assert not (tmp_path / "acme.com").exists()
    assert tmp_path.is_dir()


def test_remove_version_dir_keeps_sibling_versions(tmp_path: Path) -> None:
    _write_version_dir(tmp_path, _SEGMENTS, [_snapshot("rev-a", "published")])
    _write_version_dir(tmp_path, ("acme.com", "widget", "v2"), [_snapshot("rev-b", "published")])

    _remove_version_dir(tmp_path, _SEGMENTS)

    assert not (tmp_path / "acme.com" / "widget" / "v1").exists()
    assert (tmp_path / "acme.com" / "widget" / "v2" / "published" / "rev-b.json").is_file()


def test_prune_unknown_dirs_removes_only_unregistered_apis(tmp_path: Path) -> None:
    _write_version_dir(tmp_path, _SEGMENTS, [_snapshot("rev-a", "published")])
    _write_version_dir(tmp_path, ("gone.com", "old", "v9"), [_snapshot("rev-x", "archived")])

    pruned = _prune_unknown_dirs(tmp_path, {("acme.com", "widget", "v1")})

    assert pruned == 1
    assert (tmp_path / "acme.com" / "widget" / "v1").is_dir()
    assert not (tmp_path / "gone.com").exists()


def test_prune_unknown_dirs_decodes_encoded_segments(tmp_path: Path) -> None:
    segments = ("a/b", "Name With Space", "v1")
    encoded = (
        _encode_segment(segments[0]),
        _encode_segment(segments[1]),
        _encode_segment(segments[2]),
    )
    _write_version_dir(tmp_path, encoded, [_snapshot("rev-a", "draft")])

    pruned = _prune_unknown_dirs(tmp_path, {segments})

    assert pruned == 0
    assert tmp_path.joinpath(*encoded).is_dir()
