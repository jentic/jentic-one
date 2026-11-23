"""``Idempotency-Key`` store over the shared-state ``AtomicStore`` role.

An application service (not ``broker/core/``) that gives the spec's
``Idempotency-Key`` semantics on top of the shared-state backend's atomic
``set_if_absent`` claim. The same code path works on the memory backend
(single-instance) and the Redis backend (cross-instance) — only the injected
``AtomicStore`` differs.

Two TTLs guard against a poison-pill (a pending claim must be short):

- ``pending_ttl_s`` — a **short** claim written by :meth:`begin`. If the broker
  is hard-killed after the claim but before :meth:`complete`, the key frees
  itself within seconds (not the 24h replay window) and the next retry re-claims
  it ``FRESH``.
- ``done_ttl_s`` — the **long** replay window the record is promoted to once
  :meth:`complete` stores the real response.

Per-caller keyspace (``idem:{caller}:{key}``) stops one caller replaying
another's response; ``caller`` is the resolved ``actor_id``, never the raw
token. A record is small and bounded (status, fingerprint, capped headers);
the response body is a separate ``idem-body:{caller}:{key}:{tag}:{claim}`` entry
(``tag`` is the start of the request fingerprint and ``claim`` is its generation),
so on the memory backend bodies spend their own budget and a full body budget only
drops bodies from replays, never refuses claims.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import secrets
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

import structlog

from jentic_one.broker.core.errors import IdempotencyCompletionError, IdempotencyUnavailableError
from jentic_one.broker.core.headers import SENSITIVE_RESPONSE_HEADERS, extract_header_items
from jentic_one.broker.core.proxy_headers import ResponseHeaderMap
from jentic_one.shared.state.backend import (
    AtomicStore,
    KeyValueStore,
    StateBackendCapacityError,
)

_logger = structlog.get_logger(__name__)

# Response headers that describe one exchange only and so are not stored for
# replay (lower-cased).
_PER_EXCHANGE_HEADERS: frozenset[str] = frozenset({"server-timing"})


@runtime_checkable
class IdempotencyStateStore(KeyValueStore, AtomicStore, Protocol):
    """The store roles idempotency needs: ``get``/``set`` (KV) + ``set_if_absent`` (atomic).

    A narrow union (ISP) over the shared-state backend — the claim is an atomic
    ``set_if_absent``; the pending read and the DONE promotion are plain KV
    ``get``/``set``. Both the memory and Redis backends satisfy it.
    """


_KEY_PREFIX = "idem:"
# Stored bodies live in their own namespace so they have their own budget.
_BODY_KEY_PREFIX = "idem-body:"
# Fingerprint or claim-token characters (hex) a body key carries to bind it to its record.
_BODY_FINGERPRINT_TAG_LEN = 16
_CLAIM_TOKEN_BYTES = 8
# Most serialized header bytes one record carries.
MAX_RECORD_HEADER_BYTES = 512
_MAX_STATUS_CODE = 999
_SEPARATORS = (",", ":")
# Headers a replay needs most (the media type, where a 201 put the resource,
# its validator, and when to retry), kept first when the cap binds; the rest
# follow in response order.
_PRIORITY_HEADERS = ("content-type", "location", "etag", "content-location", "retry-after")
_HEADER_RANK = {name: rank for rank, name in enumerate(_PRIORITY_HEADERS)}
# How long a released body slot lingers once its body is dropped.
_RELEASED_BODY_TTL_S = 1.0
# Record phases. PENDING carries only the fingerprint and owner token (no response yet);
# DONE carries the serialized response and is what a replay re-emits.
_PHASE_PENDING = "pending"
_PHASE_DONE = "done"

# Redis Lua compare-and-set / compare-and-delete for fenced pending claims.
_REDIS_CAS_LUA = """
local current = redis.call('GET', KEYS[1])
if not current then
  return 0
end
local ok, decoded = pcall(cjson.decode, current)
if not ok or type(decoded) ~= 'table' then
  return 0
end
if decoded['phase'] ~= 'pending' or decoded['fp'] ~= ARGV[1] or decoded['token'] ~= ARGV[2] then
  return 0
end
if ARGV[3] == 'delete' then
  redis.call('DEL', KEYS[1])
  return 1
end
redis.call('SET', KEYS[1], ARGV[4], 'PX', tonumber(ARGV[5]))
return 1
"""


class IdempotencyState(StrEnum):
    """The verdict :meth:`SharedStateIdempotencyStore.begin` returns."""

    FRESH = "fresh"
    """First time we've seen ``(caller, key)`` — the caller must execute + complete."""

    REPLAY = "replay"
    """Same key + same fingerprint, response stored — re-emit it."""

    CONFLICT = "conflict"
    """Same key, *different* fingerprint — the client reused a key (→ 409)."""

    IN_PROGRESS = "in_progress"
    """Same key + same fingerprint, but the original is still running (→ 409 + Retry-After)."""


@dataclass(frozen=True, slots=True)
class StoredResponse:
    """A serialized upstream response held for byte-for-byte replay."""

    status_code: int
    headers: dict[str, str]
    body: bytes
    body_omitted: bool
    header_items: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class IdempotencyOutcome:
    """The result of a :meth:`SharedStateIdempotencyStore.begin` claim attempt."""

    state: IdempotencyState
    stored: StoredResponse | None = None
    retry_after_s: int = 0
    claim_id: str | None = None


def _scrub_headers(headers: dict[str, str]) -> dict[str, str]:
    """Drop headers that must not be persisted for replay.

    Security-bearing headers are never hoarded; ``Server-Timing`` describes the
    original exchange's latency, which a replay (no upstream call) must not
    re-assert as its own.
    """
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in SENSITIVE_RESPONSE_HEADERS and k.lower() not in _PER_EXCHANGE_HEADERS
    }


def _scrub_header_items(
    headers: Mapping[str, str] | Iterable[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Drop sensitive and per-exchange headers while preserving repeated fields."""
    return [
        (k, v)
        for k, v in extract_header_items(headers)
        if k.lower() not in SENSITIVE_RESPONSE_HEADERS and k.lower() not in _PER_EXCHANGE_HEADERS
    ]


def _cap_headers(headers: dict[str, str]) -> dict[str, str]:
    """Keep headers (:data:`_PRIORITY_HEADERS` first) while their JSON stays within the cap.

    Bounds the record size: a response with very many or very long headers
    replays without the ones past the cap rather than inflating every record.
    A header that does not fit is skipped, so a later, shorter one may still
    be kept.
    """
    ordered = sorted(
        headers.items(), key=lambda kv: _HEADER_RANK.get(kv[0].lower(), len(_PRIORITY_HEADERS))
    )
    kept: dict[str, str] = {}
    used = 2  # the enclosing ``{}``
    for name, value in ordered:
        cost = len(json.dumps({name: value}, separators=_SEPARATORS)) - 1
        if used + cost > MAX_RECORD_HEADER_BYTES:
            continue
        kept[name] = value
        used += cost
    return kept


def _cap_header_items(items: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    """Keep header pairs (:data:`_PRIORITY_HEADERS` first) while their JSON stays within the cap."""
    indexed = list(enumerate(items))
    ordered = sorted(
        indexed,
        key=lambda iv: (_HEADER_RANK.get(iv[1][0].lower(), len(_PRIORITY_HEADERS)), iv[0]),
    )
    kept_indexed: list[tuple[int, tuple[str, str]]] = []
    used = 2  # the enclosing ``{}`` / ``[]``
    for idx, (name, value) in ordered:
        cost = len(json.dumps({name: value}, separators=_SEPARATORS)) - 1
        if used + cost > MAX_RECORD_HEADER_BYTES:
            continue
        kept_indexed.append((idx, (name, value)))
        used += cost
    kept_indexed.sort(key=lambda iv: iv[0])
    return [pair for _, pair in kept_indexed]


def _dumps(record: Mapping[str, object]) -> bytes:
    return json.dumps(record, separators=_SEPARATORS).encode()


def _mint_claim_id() -> str:
    return secrets.token_hex(_CLAIM_TOKEN_BYTES)


def _tombstone(fingerprint: str, status_code: int, token: str = "") -> bytes:
    """A DONE record with only the status: what a retry replays when nothing else fit."""
    effective_token = token or ("0" * (_CLAIM_TOKEN_BYTES * 2))
    return _dumps(
        {
            "phase": _PHASE_DONE,
            "fp": fingerprint,
            "token": effective_token,
            "status": status_code,
            "body_omitted": True,
        }
    )


def _claim(fingerprint: str, token: str = "") -> bytes:
    """The PENDING claim, padded to the size of the largest :func:`_tombstone`.

    Promoting the claim to a tombstone is then an overwrite with a value no
    larger than the current one, which a budgeted store always accepts: once
    the upstream call has run, a record that stops a retry re-running it can
    always be written.
    """
    effective_token = token or ("0" * (_CLAIM_TOKEN_BYTES * 2))
    base = {"phase": _PHASE_PENDING, "fp": fingerprint, "token": effective_token, "pad": ""}
    pad = len(_tombstone(fingerprint, _MAX_STATUS_CODE, effective_token)) - len(_dumps(base))
    return _dumps({**base, "pad": " " * max(0, pad)})


class IdempotencyClaimLease:
    """Fenced lease for an in-flight ``Idempotency-Key`` claim.

    Renews the pending claim periodically while the request is running so a
    slow call does not outlive its claim, and releases the claim on exit only
    when the request failed before upstream dispatch occurred.
    """

    def __init__(
        self,
        store: SharedStateIdempotencyStore,
        caller: str,
        key: str,
        fingerprint: str,
        claim_id: str,
    ) -> None:
        self._store = store
        self._caller = caller
        self._key = key
        self._fingerprint = fingerprint
        self.claim_id = claim_id
        self.dispatched = False
        self.completed = False
        self._completion_error: IdempotencyCompletionError | None = None
        self._task: asyncio.Task[None] | None = None

    def mark_dispatched(self) -> None:
        """Record that the upstream call is about to be (or has been) dispatched."""
        self.dispatched = True

    def mark_pre_send_failure(self) -> None:
        """Record that the call failed before any request bytes were sent upstream."""
        self.dispatched = False

    async def complete(
        self,
        *,
        status_code: int,
        headers: Mapping[str, str] | Iterable[tuple[str, str]],
        body: bytes,
    ) -> bool:
        """Promote the leased claim to ``DONE`` or report an indeterminate execution.

        A false store result means the upstream response has no durable replay
        record. It is never a successful completion: the caller must not mistake
        the upstream response for a safely retryable idempotent result.
        """
        if self.completed:
            if self._completion_error is not None:
                raise self._completion_error
            return True
        # This is a terminal completion attempt, including when the backend
        # fails. Renewal must stop; only a new caller claim may retry the key.
        self.completed = True
        try:
            ok = await self._store.complete(
                self._caller,
                self._key,
                self._fingerprint,
                status_code=status_code,
                headers=headers,
                body=body,
                claim_id=self.claim_id,
            )
        except Exception as exc:
            self._completion_error = IdempotencyCompletionError(
                detail=(
                    "The upstream request was dispatched, but its idempotent outcome "
                    "could not be recorded; verify upstream state before retrying"
                )
            )
            raise self._completion_error from exc
        if not ok:
            self._completion_error = IdempotencyCompletionError(
                detail=(
                    "The upstream request was dispatched, but its idempotent outcome "
                    "could not be recorded; verify upstream state before retrying"
                )
            )
            raise self._completion_error
        return True

    async def _renew_loop(self) -> None:
        interval = max(0.05, self._store.pending_ttl_s / 2.0)
        while not self.completed:
            await asyncio.sleep(interval)
            if self.completed:
                return
            still_owned = await self._store.renew(
                self._caller,
                self._key,
                self._fingerprint,
                claim_id=self.claim_id,
            )
            if not still_owned:
                return

    async def __aenter__(self) -> IdempotencyClaimLease:
        self._task = asyncio.create_task(self._renew_loop())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        if exc_type is not None and not self.completed and not self.dispatched:
            with contextlib.suppress(Exception):
                await self._store.release(
                    self._caller,
                    self._key,
                    self._fingerprint,
                    claim_id=self.claim_id,
                )


class SharedStateIdempotencyStore:
    """``Idempotency-Key`` claim/replay over an :class:`AtomicStore`."""

    def __init__(
        self,
        store: IdempotencyStateStore,
        *,
        pending_ttl_s: float,
        done_ttl_s: float,
        max_response_bytes: int,
    ) -> None:
        self._store = store
        self._pending_ttl = pending_ttl_s
        self._done_ttl = done_ttl_s
        self._max_response_bytes = max_response_bytes
        self._cas_lock = asyncio.Lock()
        self._issued_tokens: dict[tuple[str, str, str], deque[str]] = {}
        self._redis_cas_script: Any = None
        redis_client = getattr(store, "_redis", None)
        if redis_client is not None and hasattr(redis_client, "register_script"):
            self._redis_cas_script = redis_client.register_script(_REDIS_CAS_LUA)

    @property
    def pending_ttl_s(self) -> float:
        """Configured pending-claim TTL in seconds."""
        return self._pending_ttl

    def lease(
        self,
        caller: str,
        key: str,
        fingerprint: str,
        claim_id: str | None = None,
    ) -> IdempotencyClaimLease:
        """Build a fenced lease context manager for an active claim."""
        token = claim_id or self._peek_claim_token(caller, key, fingerprint) or _mint_claim_id()
        return IdempotencyClaimLease(self, caller, key, fingerprint, token)

    @staticmethod
    def _caller_segment(caller: str) -> str:
        # The caller is the key's second ``:`` segment (the memory backend's
        # per-owner share), so a ``:`` inside it is escaped.
        return caller.replace("%", "%25").replace(":", "%3A")

    def _slot(self, caller: str, key: str) -> str:
        return f"{_KEY_PREFIX}{self._caller_segment(caller)}:{key}"

    def _body_slot(
        self, caller: str, key: str, fingerprint: str, claim_id: str | None = None
    ) -> str:
        # The generation is part of new keys: a stale completion must never
        # overwrite or release the body of the claim that replaced it.
        tag = fingerprint[:_BODY_FINGERPRINT_TAG_LEN]
        base = f"{_BODY_KEY_PREFIX}{self._caller_segment(caller)}:{key}:{tag}"
        return base if claim_id is None else f"{base}:{claim_id}"

    def _record_issued_token(self, caller: str, key: str, fingerprint: str, token: str) -> None:
        bucket_key = (caller, key, fingerprint)
        queue = self._issued_tokens.get(bucket_key)
        if queue is None:
            queue = deque()
            self._issued_tokens[bucket_key] = queue
        queue.append(token)
        # Bound per-slot token history in case claims expire without completing.
        while len(queue) > 8:
            queue.popleft()

    def _peek_claim_token(self, caller: str, key: str, fingerprint: str) -> str | None:
        queue = self._issued_tokens.get((caller, key, fingerprint))
        return queue[0] if queue else None

    def _consume_claim_token(
        self, caller: str, key: str, fingerprint: str, explicit_claim_id: str | None
    ) -> str | None:
        bucket_key = (caller, key, fingerprint)
        queue = self._issued_tokens.get(bucket_key)
        if explicit_claim_id is not None:
            if queue is not None:
                with contextlib.suppress(ValueError):
                    queue.remove(explicit_claim_id)
                if not queue:
                    del self._issued_tokens[bucket_key]
            return explicit_claim_id
        if not queue:
            return None
        token = queue.popleft()
        if not queue:
            del self._issued_tokens[bucket_key]
        return token

    async def _check_pending_owner(self, slot: str, fingerprint: str, token: str | None) -> bool:
        """Return True iff *slot* currently holds a PENDING record for *(fingerprint, token)*."""
        if not token:
            return False
        existing = await self._store.get(slot)
        if existing is None:
            return False
        try:
            record = json.loads(existing)
        except ValueError:
            return False
        return (
            isinstance(record, dict)
            and record.get("phase") == _PHASE_PENDING
            and record.get("fp") == fingerprint
            and record.get("token") == token
        )

    async def _cas_set(
        self,
        slot: str,
        *,
        fingerprint: str,
        token: str | None,
        value: bytes,
        ttl_s: float,
    ) -> bool:
        """Atomically overwrite *slot* only while it is PENDING with *(fingerprint, token)*."""
        if not token:
            return False
        key_fn = getattr(self._store, "_k", None)
        if self._redis_cas_script is not None and callable(key_fn):
            redis_key = key_fn(slot)
            ttl_ms = max(1, int(ttl_s * 1000))
            res = await self._redis_cas_script(
                keys=[redis_key],
                args=[fingerprint, token, "set", value, ttl_ms],
            )
            return bool(int(res))
        async with self._cas_lock:
            if not await self._check_pending_owner(slot, fingerprint, token):
                return False
            await self._store.set(slot, value, ttl_s=ttl_s)
            return True

    async def _cas_delete(
        self,
        slot: str,
        *,
        fingerprint: str,
        token: str | None,
    ) -> bool:
        """Atomically delete *slot* only while it is PENDING with *(fingerprint, token)*."""
        if not token:
            return False
        key_fn = getattr(self._store, "_k", None)
        if self._redis_cas_script is not None and callable(key_fn):
            redis_key = key_fn(slot)
            res = await self._redis_cas_script(
                keys=[redis_key],
                args=[fingerprint, token, "delete", b"", 0],
            )
            return bool(int(res))
        async with self._cas_lock:
            if not await self._check_pending_owner(slot, fingerprint, token):
                return False
            delete_fn = getattr(self._store, "_delete", None)
            kv = getattr(self._store, "_kv", None)
            if callable(delete_fn) and isinstance(kv, dict) and slot in kv:
                delete_fn(slot)
            else:
                await self._store.set(slot, b"", ttl_s=0.001)
            return True

    async def renew(
        self,
        caller: str,
        key: str,
        fingerprint: str,
        *,
        claim_id: str | None = None,
    ) -> bool:
        """Extend an in-flight PENDING claim's TTL if *claim_id* still owns it."""
        token = claim_id or self._peek_claim_token(caller, key, fingerprint)
        slot = self._slot(caller, key)
        try:
            return await self._cas_set(
                slot,
                fingerprint=fingerprint,
                token=token,
                value=_claim(fingerprint, token or ""),
                ttl_s=self._pending_ttl,
            )
        except StateBackendCapacityError:
            return False

    async def release(
        self,
        caller: str,
        key: str,
        fingerprint: str,
        *,
        claim_id: str | None = None,
    ) -> bool:
        """Release a PENDING claim after a pre-dispatch failure if *claim_id* still owns it."""
        token = self._consume_claim_token(caller, key, fingerprint, claim_id)
        slot = self._slot(caller, key)
        try:
            return await self._cas_delete(slot, fingerprint=fingerprint, token=token)
        except StateBackendCapacityError:
            return False

    async def begin(self, caller: str, key: str, fingerprint: str) -> IdempotencyOutcome:
        """Atomically claim ``(caller, key)`` or classify an existing claim.

        A successful ``set_if_absent`` ⇒ ``FRESH`` (we own it, execute). Otherwise
        decode the existing record: a different fingerprint ⇒ ``CONFLICT``; a
        matching fingerprint that is still ``PENDING`` ⇒ ``IN_PROGRESS``; a
        matching ``DONE`` record ⇒ ``REPLAY`` with the stored response.

        Raises:
            IdempotencyUnavailableError: The store refused the claim write (the
                namespace, or this caller's share of it, is full). The caller
                must not run the request: an unrecorded claim cannot dedupe a
                retry.
        """
        slot = self._slot(caller, key)
        claim_id = _mint_claim_id()
        try:
            async with self._cas_lock:
                claimed = await self._store.set_if_absent(
                    slot, _claim(fingerprint, claim_id), ttl_s=self._pending_ttl
                )
                if claimed:
                    self._record_issued_token(caller, key, fingerprint, claim_id)
        except StateBackendCapacityError as exc:
            raise IdempotencyUnavailableError(
                detail=(
                    "The Idempotency-Key claim could not be recorded, so the request "
                    "was not executed; retry after the indicated delay."
                ),
                type="idempotency_unavailable",
                headers={"Retry-After": str(exc.retry_after_s)},
            ) from exc
        if claimed:
            return IdempotencyOutcome(IdempotencyState.FRESH, claim_id=claim_id)

        existing = await self._store.get(slot)
        if existing is None:
            # The claim expired between the failed set and the read — treat as a
            # narrow in-progress race; the caller retries.
            return IdempotencyOutcome(
                IdempotencyState.IN_PROGRESS, retry_after_s=self._retry_after()
            )

        try:
            record = json.loads(existing)
        except ValueError:
            return IdempotencyOutcome(
                IdempotencyState.IN_PROGRESS, retry_after_s=self._retry_after()
            )
        if record.get("fp") != fingerprint:
            return IdempotencyOutcome(IdempotencyState.CONFLICT)
        if record.get("phase") != _PHASE_DONE:
            return IdempotencyOutcome(
                IdempotencyState.IN_PROGRESS, retry_after_s=self._retry_after()
            )
        body: bytes | None = None
        if record.get("body_ref"):
            raw_token = record.get("token")
            if isinstance(raw_token, str) and raw_token:
                body = await self._store.get(self._body_slot(caller, key, fingerprint, raw_token))
            if body is None:
                # Records already DONE at upgrade used the unversioned body key.
                body = await self._store.get(self._body_slot(caller, key, fingerprint))
        return IdempotencyOutcome(
            IdempotencyState.REPLAY, stored=self._decode_response(record, body)
        )

    async def complete(
        self,
        caller: str,
        key: str,
        fingerprint: str,
        *,
        status_code: int,
        headers: Mapping[str, str] | Iterable[tuple[str, str]],
        body: bytes,
        claim_id: str | None = None,
    ) -> bool:
        """Promote the pending claim to the stored response on the long TTL.

        Completion is fenced by the claim's generation owner token (`claim_id`):
        if the pending claim expired and was reclaimed by a newer request, or
        was already promoted to ``DONE``, this completion refuses to overwrite
        the newer record and returns ``False``.
        """
        token = self._consume_claim_token(caller, key, fingerprint, claim_id)
        slot = self._slot(caller, key)
        if not await self._check_pending_owner(slot, fingerprint, token):
            _logger.warning(
                "idempotency_stale_claim_rejected",
                reason="claim_not_owned",
            )
            return False

        omit = len(body) > self._max_response_bytes
        body_slot = self._body_slot(caller, key, fingerprint, token or "")
        body_stored = False
        if body and not omit:
            try:
                # Outlives the record, so a replay never finds the record without it.
                await self._store.set(body_slot, body, ttl_s=self._done_ttl + self._pending_ttl)
                body_stored = True
            except StateBackendCapacityError:
                omit = True
                _logger.warning("idempotency_body_not_stored", reason="body_budget_full")
        scrubbed_items = _scrub_header_items(headers)
        capped_items = _cap_header_items(scrubbed_items)
        capped_dict = _cap_headers(dict(capped_items))
        lower_counts: dict[str, int] = {}
        for k, _ in capped_items:
            lk = k.lower()
            lower_counts[lk] = lower_counts.get(lk, 0) + 1
        has_repeats = any(c > 1 for c in lower_counts.values())
        record: dict[str, object] = {
            "phase": _PHASE_DONE,
            "fp": fingerprint,
            "token": token,
            "status": status_code,
            "headers": [[k, v] for k, v in capped_items] if has_repeats else capped_dict,
            "body_omitted": omit,
            "body_ref": bool(body) and not omit,
        }
        try:
            promoted = await self._cas_set(
                slot,
                fingerprint=fingerprint,
                token=token,
                value=_dumps(record),
                ttl_s=self._done_ttl,
            )
            if promoted:
                return True
            if body_stored:
                await self._release_body(body_slot)
            _logger.warning(
                "idempotency_stale_claim_rejected",
                reason="claim_superseded_during_complete",
            )
            return False
        except StateBackendCapacityError:
            pass
        if body_stored:
            await self._release_body(body_slot)
        try:
            promoted = await self._cas_set(
                slot,
                fingerprint=fingerprint,
                token=token,
                value=_tombstone(fingerprint, status_code, token or ""),
                ttl_s=self._done_ttl,
            )
            if not promoted:
                _logger.warning(
                    "idempotency_stale_claim_rejected",
                    reason="claim_superseded_before_tombstone",
                )
                return False
        except StateBackendCapacityError:
            # Only reachable once the claim itself has expired (the call
            # outlived ``pending_ttl_s``), when there is no claim to overwrite.
            _logger.warning(
                "idempotency_record_not_stored",
                reason="store_full",
                detail=(
                    "the response could not be recorded and the pending claim had "
                    "already expired; a retry with this Idempotency-Key runs again"
                ),
            )
            return False
        _logger.warning("idempotency_record_stored_as_tombstone", reason="store_full")
        return True

    async def _release_body(self, body_slot: str) -> None:
        """Drop a stored body nothing will reference.

        Overwrites it with an empty value on a one-second TTL: the KV role has
        no delete, and an overwrite no larger than the live entry always fits
        its budget, so the release cannot itself be refused while the body is
        live.
        """
        try:
            await self._store.set(body_slot, b"", ttl_s=_RELEASED_BODY_TTL_S)
        except StateBackendCapacityError:
            # Only when the body already expired; there is nothing to release.
            return

    def _retry_after(self) -> int:
        # Round the short claim TTL up to whole seconds for the Retry-After hint.
        return max(1, int(self._pending_ttl + 0.999))

    @staticmethod
    def _decode_response(record: dict[str, object], body: bytes | None) -> StoredResponse:
        status = record.get("status")
        raw_headers = record.get("headers")
        header_items: list[tuple[str, str]] = []
        if isinstance(raw_headers, list):
            for entry in raw_headers:
                if isinstance(entry, list | tuple) and len(entry) == 2:
                    header_items.append((str(entry[0]), str(entry[1])))
            headers: dict[str, str] = ResponseHeaderMap(header_items, raw_headers=header_items)
        elif isinstance(raw_headers, dict):
            header_items = [(str(k), str(v)) for k, v in raw_headers.items()]
            headers = dict(header_items)
        else:
            headers = {}
        omitted = bool(record.get("body_omitted"))
        body_b64 = record.get("body_b64")
        if isinstance(body_b64, str) and body_b64:
            # A record written with the body inline.
            body = base64.b64decode(body_b64)
        elif record.get("body_ref") and body is None:
            # The body key expired or was lost; replay as body-omitted.
            omitted = True
        return StoredResponse(
            status_code=status if isinstance(status, int) else 0,
            headers=headers,
            body=body or b"",
            body_omitted=omitted,
            header_items=tuple(header_items),
        )


__all__ = [
    "IdempotencyClaimLease",
    "IdempotencyOutcome",
    "IdempotencyState",
    "IdempotencyStateStore",
    "SharedStateIdempotencyStore",
    "StoredResponse",
]
