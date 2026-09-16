"""Spec mirror service — opt-in mirror of spec documents to a local directory.

Implements the ``spec_mirror`` config section: when enabled, registry
mutations (import, promote, archive, delete, overlay rollback) also rewrite
the affected API's directory under ``spec_mirror.path`` so the filesystem
mirrors the registry DB. Consumers mount that directory read-only for direct
file access to specs.

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
``sha`` refers to the original source bytes, while ``mirror_digest`` is the
sha256 of the mirrored file's bytes so consumers can verify what they read.

Consistency posture (see ``rules/backend/service-protocols.md``, cross-system
intent-then-apply): the registry DB is the source of truth and commits first;
mirroring happens post-commit, is **best-effort** (a mirror failure never fails
the registry operation), and is **idempotent** — ``sync_api`` rewrites an API's
directory from DB state, verifying mirrored bytes (not just file presence), so
any drift or corruption heals on the next sync or the startup reconcile
(``spec_mirror_lifespan``). Within a sync, desired files are written before
stale ones are removed, so a revision changing state may briefly appear in two
state dirs but never in none.

Concurrency: syncs for the same API serialize on a per-loop, per-identifier
asyncio lock, so the last completed sync always reflects the latest DB read,
and temp files use unique names so a rename can never publish interleaved
writes. The locks are **per process** — a mirror directory must have exactly
one registry-surface writer process (see the ``spec_mirror`` config docs);
replicas sharing a rw mount are unsupported.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote
from weakref import WeakKeyDictionary

import structlog

from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.repos.revision_repo import ApiRevisionRepository
from jentic_one.shared.context import Context
from jentic_one.shared.metrics import get_meter

if TYPE_CHECKING:
    from fastapi import FastAPI
    from opentelemetry.metrics import Counter

logger = structlog.get_logger()

_META_SUFFIX = ".meta.json"

#: Characters copied through unencoded by :func:`_encode_segment`. Deliberately
#: excludes uppercase ASCII: case-insensitive filesystems (macOS, Windows)
#: would otherwise collapse identifiers differing only by case into one
#: directory, letting each API's sync prune the other's files.
_SAFE_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-_.~")

#: Windows reserved device names (matched against the part before the first
#: dot, which is how Windows reserves them — ``con.json`` is also reserved).
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)

_METER_NAME = "jentic_one.spec_mirror"

#: Failure counter, created once on first increment (lazy so importing this
#: module has no side effects; see the flow3_metrics pattern). Increments go
#: through the sanctioned ``shared/metrics.py`` facade and are non-throwing.
_failure_counter: Counter | None = None


def _record_failure(operation: str) -> None:
    """Count one best-effort mirror failure (``operation`` = sync|reconcile).

    Failures are otherwise only visible as error logs while consumers silently
    read stale specs — the counter gives operators an alertable signal.
    """
    global _failure_counter
    if _failure_counter is None:
        _failure_counter = get_meter(_METER_NAME).create_counter(
            "spec_mirror.failures",
            description="Best-effort spec-mirror operations that failed (mirror is drifting)",
        )
    _failure_counter.add(1, {"operation": operation})


def _encode_segment(segment: str) -> str:
    """Percent-encode an identifier into a single safe path segment.

    ``vendor``/``name``/``version`` are user-controlled strings. Everything
    outside :data:`_SAFE_CHARS` is percent-encoded (UTF-8 bytes, uppercase
    hex), which keeps separators, traversal sequences, and case-only
    collisions out of the path. Two literal edge cases on top:

    - dot-only segments survive the copy-through unchanged, so encode them
      explicitly — ``.`` and ``..`` must never become a segment;
    - Windows reserved device names (``con``, ``nul``, ``com1``…) get their
      first character encoded so the directory stays creatable there.
    """
    encoded = "".join(
        ch if ch in _SAFE_CHARS else "".join(f"%{byte:02X}" for byte in ch.encode("utf-8"))
        for ch in segment
    )
    if set(encoded) == {"."}:
        return encoded.replace(".", "%2E")
    if encoded.split(".", 1)[0] in _WINDOWS_RESERVED:
        return f"%{ord(encoded[0]):02X}{encoded[1:]}"
    return encoded


def _decode_segment(segment: str) -> str:
    """Inverse of :func:`_encode_segment` (``unquote`` covers every escape)."""
    return unquote(segment)


#: Per-event-loop, per-API serialization locks for sync operations. Keyed by
#: loop (weakly, so test loops don't leak or cross-bind — an asyncio.Lock is
#: bound to the loop it first awaits on) then by identifier tuple. Per-process
#: only: cross-replica coordination is explicitly out of scope (single-writer
#: deployment constraint, documented on ``SpecMirrorConfig``).
_api_locks: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[tuple[str, str, str], asyncio.Lock]]
_api_locks = WeakKeyDictionary()


def _api_lock(identifier: tuple[str, str, str]) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    locks = _api_locks.setdefault(loop, {})
    return locks.setdefault(identifier, asyncio.Lock())


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
    best-effort: failures are logged (error level) and counted
    (``spec_mirror.failures``), never raised to the caller — the registry
    operation that triggered the sync has already committed and must not be
    failed by its mirror side effect.
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
        API absent from the DB syncs to a removed directory, so this is also
        the delete path — being DB-driven, it stays correct when a delete
        races a re-import of the same identity.

        Serialized per API identity (per process): the DB read happens under
        the lock, so the last completed sync reflects the latest DB state and
        two racing syncs can't interleave a stale snapshot over a fresh one.
        """
        if not self._enabled:
            return
        try:
            async with _api_lock((vendor, name, version)):
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
            # Error (not warning): the mirror is now known-stale and — for a
            # deleted API — nothing re-triggers until the startup reconcile,
            # so consumers may keep reading a spec the operator removed.
            logger.error(
                "spec_mirror_sync_failed",
                api_vendor=vendor,
                api_name=name,
                api_version=version,
                exc_info=True,
            )
            _record_failure("sync")

    async def reconcile_all(self) -> None:
        """Rebuild the whole mirror from the DB (best-effort).

        Syncs every registered API (backfilling specs imported while the
        feature was off or the write was missed) and prunes directories whose
        API no longer exists. The identifier set is re-read *after* the syncs
        so an API imported mid-reconcile is never pruned with a stale
        snapshot. Runs sequentially — reconcile is a startup path where a
        bounded runtime matters less than pool headroom.
        """
        if not self._enabled:
            return
        try:
            async with self._ctx.registry_db.session() as session:
                identifiers = await ApiRepository.list_identifiers(session)
            for vendor, name, version in identifiers:
                await self.sync_api(vendor, name, version)
            # Fresh read for the prune set: anything imported during the loop
            # above was mirrored by its own post-commit hook and must survive.
            async with self._ctx.registry_db.session() as session:
                known = set(await ApiRepository.list_identifiers(session))
            pruned = await asyncio.to_thread(_prune_unknown_dirs, self._base_dir, known)
            logger.info(
                "spec_mirror_reconciled",
                apis=len(identifiers),
                pruned_dirs=pruned,
            )
        except Exception:
            logger.error("spec_mirror_reconcile_failed", exc_info=True)
            _record_failure("reconcile")

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
                if len(revision.spec_files) > 1:
                    # The schema permits multiple files per revision even
                    # though ingest writes one; mirroring only the primary
                    # silently drops the rest, so make that visible.
                    logger.warning(
                        "spec_mirror_multiple_spec_files",
                        revision_id=str(revision.id),
                        spec_files=len(revision.spec_files),
                        mirrored=spec_file.filename,
                    )
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
    """Write via a uniquely-named same-directory temp file + rename.

    Readers never see a partial document (rename is atomic on POSIX within one
    filesystem), and — because the temp name is unique per write, not derived
    from the target — two racing writers can never interleave into one temp
    file and rename corrupt bytes into place.
    """
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
        os.replace(tmp_name, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp_name)
        raise


def _write_version_dir(
    base_dir: Path,
    segments: tuple[str, str, str],
    snapshots: list[_RevisionSnapshot] | None,
) -> None:
    """Rewrite one API version directory to match *snapshots* (sync, threaded).

    Two phases: write every desired file first, then prune stale ones — so a
    state-changed revision is briefly in two state dirs, never in none. A
    revision is only skipped when **both** its sidecar and its spec file match
    the desired bytes, so a truncated or tampered spec file heals on the next
    sync instead of hiding behind an intact sidecar.
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

        spec_data = json.dumps(snap.content, indent=2, ensure_ascii=False) + "\n"
        # mirror_digest hashes the *mirrored* bytes (canonical JSON), unlike
        # ``sha`` which refers to the original source bytes — it is what a
        # consumer can actually verify against the file it reads.
        digest = hashlib.sha256(spec_data.encode("utf-8")).hexdigest()
        meta_data = (
            json.dumps({**snap.meta, "mirror_digest": f"sha256:{digest}"}, indent=2, sort_keys=True)
            + "\n"
        )
        if _file_equals(meta_path, meta_data) and _file_equals(spec_path, spec_data):
            continue
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(spec_path, spec_data)
        _atomic_write(meta_path, meta_data)

    if not version_dir.is_dir():
        return
    # Prune every subdirectory, not just the known lifecycle states, so a
    # revision that once carried an out-of-enum state leaves no stale file.
    for state_dir in [p for p in version_dir.iterdir() if p.is_dir()]:
        for entry in state_dir.iterdir():
            if entry.is_file() and entry not in desired_specs:
                entry.unlink(missing_ok=True)
        _rmdir_if_empty(state_dir)
    for entry in version_dir.iterdir():
        if not entry.is_file():
            continue
        if entry.name.endswith(_META_SUFFIX) and entry not in desired_meta:
            entry.unlink(missing_ok=True)
        elif entry.name.startswith(".") and entry.name.endswith(".tmp"):
            # Orphaned temp file from a crashed write (state-dir orphans are
            # already caught by the not-desired prune above).
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
    process serves the registry surface (combined and standalone shapes
    alike). Directory validation raises (a misconfigured mirror should fail
    boot loudly, matching the config validator's posture). The reconcile runs
    as a lifespan-supervised background task so a large registry never delays
    readiness — it is best-effort and idempotent, so cancelling it at shutdown
    is safe (the next boot's reconcile picks up where it left off).
    """
    await asyncio.to_thread(_ensure_writable_dir, Path(ctx.config.spec_mirror.path))
    reconcile_task: asyncio.Task[None] | None = None
    if ctx.config.spec_mirror.reconcile_on_startup:
        reconcile_task = asyncio.create_task(
            SpecMirrorService(ctx).reconcile_all(), name="spec-mirror-reconcile"
        )
    try:
        yield
    finally:
        if reconcile_task is not None:
            reconcile_task.cancel()
            with suppress(asyncio.CancelledError):
                await reconcile_task
