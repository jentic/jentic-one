"""Unit tests for the spec-mirror filesystem helpers (no DB, no app wiring).

The DB-reading half of ``SpecMirrorService`` is covered by the integration
tests (``tests/integration/registry/services/test_spec_mirror.py``); these
tests exercise the pure path/write/prune helpers against a temp directory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jentic_one.registry.services.spec_mirror_service import (
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
    ],
)
def test_encode_segment_produces_safe_single_segments(raw: str, expected: str) -> None:
    encoded = _encode_segment(raw)
    assert encoded == expected
    assert "/" not in encoded
    assert encoded not in (".", "..")


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
    assert meta_a == {"revision_id": "rev-a", "state": "published"}


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


def test_write_version_dir_skips_rewrite_when_meta_unchanged(tmp_path: Path) -> None:
    snap = _snapshot("rev-a", "published")
    _write_version_dir(tmp_path, _SEGMENTS, [snap])
    spec_path = tmp_path / "acme.com" / "widget" / "v1" / "published" / "rev-a.json"
    first_mtime = spec_path.stat().st_mtime_ns

    _write_version_dir(tmp_path, _SEGMENTS, [snap])

    assert spec_path.stat().st_mtime_ns == first_mtime


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
    segments = ("a/b", "name with space", "v1")
    encoded = (
        _encode_segment(segments[0]),
        _encode_segment(segments[1]),
        _encode_segment(segments[2]),
    )
    _write_version_dir(tmp_path, encoded, [_snapshot("rev-a", "draft")])

    pruned = _prune_unknown_dirs(tmp_path, {segments})

    assert pruned == 0
    assert tmp_path.joinpath(*encoded).is_dir()
