"""Spec mirror service — opt-in mirror of spec documents to a local directory.

Implements the ``spec_mirror`` config section: when enabled, registry
mutations (import, promote, archive, delete) also rewrite the affected API's
directory under ``spec_mirror.path`` so the filesystem mirrors the registry
DB. Consumers mount that directory read-only for direct file access to specs.

Layout, one directory per API version, revision files grouped by lifecycle
state (state transitions move the file between state dirs)::

    <path>/<vendor>/<name>/<version>/
        published/<revision_id>.json    # promoted revisions
        imported/<revision_id>.json     # catalog/auto-imported live revisions
        draft/<revision_id>.json        # drafts awaiting promote
        archived/<revision_id>.json     # superseded revisions
        <revision_id>.meta.json         # sidecar: state, origin, sha, digest, …

``published/`` + ``imported/`` together hold the at-most-one servable revision
per API version (the DB enforces one active revision), so consumers glob those
two dirs for the live spec. Path segments are percent-encoded; the mirror
writes canonical JSON re-serialized from the stored document — the sidecar
``sha`` refers to the original source bytes, not the mirrored file.

Consistency posture (see ``rules/backend/service-protocols.md``, cross-system
intent-then-apply): the registry DB is the source of truth and commits first;
mirroring happens post-commit, is **best-effort** (a mirror failure never fails
the registry operation), and is **idempotent** — ``sync_api`` rewrites an API's
directory from DB state, so any drift (crash between commit and write, feature
enabled after imports happened) heals on the next sync or the startup
reconcile (``spec_mirror_lifespan``). Within a sync, desired files are written
before stale ones are removed, so a revision changing state may briefly appear
in two state dirs but never in none.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, unquote

import structlog

from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.repos.revision_repo import ApiRevisionRepository
from jentic_one.shared.context import Context
from jentic_one.shared.models import ApiRevisionState

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = structlog.get_logger()

#: Directory name per revision lifecycle state, under each API version dir.
_STATE_DIRS = tuple(state.value for state in ApiRevisionState)

_META_SUFFIX = ".meta.json"


def _encode_segment(segment: str) -> str:
    """Percent-encode an identifier into a single safe path segment.

    ``vendor``/``name``/``version`` are user-controlled strings; encoding
    everything but unreserved characters keeps separators and traversal
    sequences out of the path. Dot-only segments survive ``quote`` unchanged,
    so encode them explicitly — ``.`` and ``..`` must never become a segment.
    """
    encoded = quote(segment, safe="")
    if set(encoded) == {"."}:
        return encoded.replace(".", "%2E")
    return encoded


def _decode_segment(segment: str) -> str:
    """Inverse of :func:`_encode_segment` (``unquote`` covers ``%2E`` too)."""
    return unquote(segment)


@dataclass(frozen=True)
class _RevisionSnapshot:
    """Plain-data copy of one revision + spec doc, detached from the session.

    The sync reads everything inside one short session, then does file I/O
    with no session held (pool rule: never hold a connection across unrelated
    I/O). Only revisions that actually carry a spec file are snapshotted.
    """

    revision_id: str
    state: str
    meta: dict[str, Any]
    content: dict[str, Any]


class SpecMirrorService:
    """Maintains the on-disk mirror of registry spec documents.

    Every public method self-gates on ``spec_mirror.enabled`` and is
    best-effort: failures are logged and swallowed, never raised to the
    caller — the registry operation that triggered the sync has already
    committed and must not be failed by its mirror side effect.
    """

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    @property
    def _base_dir(self) -> Path:
        return Path(self._ctx.config.spec_mirror.path)

    @property
    def _enabled(self) -> bool:
        return self._ctx.config.spec_mirror.enabled

    async def sync_api(self, vendor: str, name: str, version: str) -> None:
        """Make the API's version directory match the DB (best-effort).

        Writes every revision's spec into the dir matching its state, then
        prunes files for revisions that no longer exist or changed state. An
        API absent from the DB syncs to a removed directory, so this also
        serves as the delete path when callers don't know whether the API row
        survived.
        """
        if not self._enabled:
            return
        try:
            snapshots = await self._load_snapshots(vendor, name, version)
            await asyncio.to_thread(
                _write_version_dir,
                self._base_dir,
                (_encode_segment(vendor), _encode_segment(name), _encode_segment(version)),
                snapshots,
            )
            logger.debug(
                "spec_mirror_synced",
                api_vendor=vendor,
                api_name=name,
                api_version=version,
                revisions=len(snapshots) if snapshots is not None else 0,
            )
        except Exception:
            logger.warning(
                "spec_mirror_sync_failed",
                api_vendor=vendor,
                api_name=name,
                api_version=version,
                exc_info=True,
            )

    async def remove_api(self, vendor: str, name: str, version: str) -> None:
        """Remove the API's version directory from the mirror (best-effort)."""
        if not self._enabled:
            return
        try:
            await asyncio.to_thread(
                _remove_version_dir,
                self._base_dir,
                (_encode_segment(vendor), _encode_segment(name), _encode_segment(version)),
            )
            logger.debug(
                "spec_mirror_removed",
                api_vendor=vendor,
                api_name=name,
                api_version=version,
            )
        except Exception:
            logger.warning(
                "spec_mirror_remove_failed",
                api_vendor=vendor,
                api_name=name,
                api_version=version,
                exc_info=True,
            )

    async def reconcile_all(self) -> None:
        """Rebuild the whole mirror from the DB (best-effort).

        Syncs every registered API (backfilling specs imported while the
        feature was off or the write was missed) and prunes directories whose
        API no longer exists. Runs sequentially — reconcile is a startup path
        where a bounded runtime matters less than pool headroom.
        """
        if not self._enabled:
            return
        try:
            async with self._ctx.registry_db.session() as session:
                identifiers = await ApiRepository.list_identifiers(session)
            for vendor, name, version in identifiers:
                await self.sync_api(vendor, name, version)
            pruned = await asyncio.to_thread(_prune_unknown_dirs, self._base_dir, set(identifiers))
            logger.info(
                "spec_mirror_reconciled",
                apis=len(identifiers),
                pruned_dirs=pruned,
            )
        except Exception:
            logger.warning("spec_mirror_reconcile_failed", exc_info=True)

    async def _load_snapshots(
        self, vendor: str, name: str, version: str
    ) -> list[_RevisionSnapshot] | None:
        """Snapshot the API's revisions + spec docs; ``None`` when the API is gone."""
        async with self._ctx.registry_db.session() as session:
            api = await ApiRepository.get_by_identifier(session, vendor, name, version)
            if api is None:
                return None
            revisions = await ApiRevisionRepository.list_for_api_with_spec_files(session, api.id)
            snapshots: list[_RevisionSnapshot] = []
            for revision in revisions:
                # Ingest stores one spec file per revision; a revision without
                # one (mid-ingest read) simply isn't mirrored until re-synced.
                spec_file = min(revision.spec_files, key=lambda f: f.filename, default=None)
                if spec_file is None:
                    continue
                snapshots.append(
                    _RevisionSnapshot(
                        revision_id=str(revision.id),
                        state=revision.state,
                        meta={
                            "revision_id": str(revision.id),
                            "api": {"vendor": vendor, "name": name, "version": version},
                            "state": revision.state,
                            "origin": revision.origin,
                            "filename": spec_file.filename,
                            "sha": spec_file.sha,
                            "spec_digest": revision.spec_digest,
                            "created_at": _iso(revision.created_at),
                            "promoted_at": _iso(revision.promoted_at),
                            "archived_at": _iso(revision.archived_at),
                        },
                        content=spec_file.content,
                    )
                )
            return snapshots


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _atomic_write(path: Path, data: str) -> None:
    """Write via a same-directory temp file + rename, so readers never see a
    partial document (rename is atomic on POSIX within one filesystem)."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)


def _write_version_dir(
    base_dir: Path,
    segments: tuple[str, str, str],
    snapshots: list[_RevisionSnapshot] | None,
) -> None:
    """Rewrite one API version directory to match *snapshots* (sync, threaded).

    Two phases: write every desired file first, then prune stale ones — so a
    state-changed revision is briefly in two state dirs, never in none. The
    meta sidecar doubles as the change marker: when it already matches and the
    spec file sits in the right state dir, the (potentially large) spec write
    is skipped, keeping startup reconciles cheap.
    """
    version_dir = base_dir.joinpath(*segments)
    if snapshots is None:
        _remove_version_dir(base_dir, segments)
        return

    desired_specs: set[Path] = set()
    desired_meta: set[Path] = set()
    for snap in snapshots:
        spec_path = version_dir / snap.state / f"{snap.revision_id}.json"
        meta_path = version_dir / f"{snap.revision_id}{_META_SUFFIX}"
        desired_specs.add(spec_path)
        desired_meta.add(meta_path)

        meta_data = json.dumps(snap.meta, indent=2, sort_keys=True) + "\n"
        if spec_path.is_file() and _file_equals(meta_path, meta_data):
            continue
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(spec_path, json.dumps(snap.content, indent=2, ensure_ascii=False) + "\n")
        _atomic_write(meta_path, meta_data)

    if not version_dir.is_dir():
        return
    for state_name in _STATE_DIRS:
        state_dir = version_dir / state_name
        if not state_dir.is_dir():
            continue
        for entry in state_dir.iterdir():
            if entry.is_file() and entry not in desired_specs:
                entry.unlink(missing_ok=True)
        _rmdir_if_empty(state_dir)
    for entry in version_dir.iterdir():
        if entry.is_file() and entry.name.endswith(_META_SUFFIX) and entry not in desired_meta:
            entry.unlink(missing_ok=True)
    if not snapshots:
        _remove_version_dir(base_dir, segments)


def _file_equals(path: Path, data: str) -> bool:
    try:
        return path.read_text(encoding="utf-8") == data
    except OSError:
        return False


def _remove_version_dir(base_dir: Path, segments: tuple[str, str, str]) -> None:
    version_dir = base_dir.joinpath(*segments)
    shutil.rmtree(version_dir, ignore_errors=True)
    # Drop now-empty <vendor>/<name> parents so removed APIs leave no husk.
    _rmdir_if_empty(version_dir.parent)
    _rmdir_if_empty(version_dir.parent.parent)


def _rmdir_if_empty(path: Path) -> None:
    # OSError: not empty (or already gone) — both fine.
    with suppress(OSError):
        path.rmdir()


def _prune_unknown_dirs(base_dir: Path, known: set[tuple[str, str, str]]) -> int:
    """Remove ``<vendor>/<name>/<version>`` dirs with no matching API row.

    Only three-level directories are touched; stray files at intermediate
    levels are left alone (they're not ours to judge).
    """
    if not base_dir.is_dir():
        return 0
    pruned = 0
    for vendor_dir in [p for p in base_dir.iterdir() if p.is_dir()]:
        for name_dir in [p for p in vendor_dir.iterdir() if p.is_dir()]:
            for version_dir in [p for p in name_dir.iterdir() if p.is_dir()]:
                identifier = (
                    _decode_segment(vendor_dir.name),
                    _decode_segment(name_dir.name),
                    _decode_segment(version_dir.name),
                )
                if identifier not in known:
                    shutil.rmtree(version_dir, ignore_errors=True)
                    pruned += 1
            _rmdir_if_empty(name_dir)
        _rmdir_if_empty(vendor_dir)
    return pruned


def _ensure_writable_dir(path: Path) -> None:
    """Create the mirror root if missing and prove it is writable (fail fast)."""
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".jentic-write-probe"
    probe.write_text("", encoding="utf-8")
    probe.unlink()


@asynccontextmanager
async def spec_mirror_lifespan(app: FastAPI, ctx: Context) -> AsyncGenerator[None, None]:
    """Startup hook for the mirror: validate the directory, then reconcile.

    Wired by the composition root only when ``spec_mirror.enabled`` and this
    process serves the registry surface. Directory validation raises (a
    misconfigured mirror should fail boot loudly, matching the config
    validator's posture); the reconcile itself is best-effort like every other
    mirror write.
    """
    await asyncio.to_thread(_ensure_writable_dir, Path(ctx.config.spec_mirror.path))
    if ctx.config.spec_mirror.reconcile_on_startup:
        await SpecMirrorService(ctx).reconcile_all()
    yield
