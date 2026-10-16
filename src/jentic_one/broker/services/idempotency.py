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

A request that fails before it can have reached the upstream gives its claim
back with :meth:`release`, so the client's same-key retry re-claims ``FRESH`` at
once instead of waiting out ``pending_ttl_s``. Whether a failed request releases,
stores its failure for replay, or holds the claim is decided by the web layer's
``ClaimLease`` (``broker/web/claim_lease.py``). Every claim carries a random
token and the release is a compare-and-delete on the exact pending record, so a
slow request whose claim already expired can never release a newer claim on the
same key. The pending TTL stays the crash guard for a request that never gets
to release (process killed, store unreachable).

Per-caller keyspace (``idem:{caller}:{key}``) stops one caller replaying
another's response; ``caller`` is the resolved ``actor_id``, never the raw
token. A record is small and bounded (status, fingerprint, capped headers);
the response body is a separate ``idem-body:{caller}:{key}:{tag}`` entry
(``tag`` is the start of the request fingerprint), so on the memory backend
bodies spend their own budget and a full body budget only drops bodies from
replays, never refuses claims.
"""

from __future__ import annotations

import base64
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

import structlog

from jentic_one.broker.core.exceptions import IdempotencyUnavailableError
from jentic_one.broker.core.headers import SENSITIVE_RESPONSE_HEADERS
from jentic_one.shared.state.backend import (
    AtomicStore,
    KeyValueStore,
    StateBackendCapacityError,
)

_logger = structlog.get_logger(__name__)


@runtime_checkable
class IdempotencyStateStore(KeyValueStore, AtomicStore, Protocol):
    """The store roles idempotency needs: ``get``/``set`` (KV) + the atomic claim/release.

    A narrow union (ISP) over the shared-state backend — the claim is an atomic
    ``set_if_absent`` and its release an atomic ``delete_if_equals``; the
    pending read and the DONE promotion are plain KV ``get``/``set``. Both the
    memory and Redis backends satisfy it.
    """


_KEY_PREFIX = "idem:"
# Stored bodies live in their own namespace so they have their own budget.
_BODY_KEY_PREFIX = "idem-body:"
# Fingerprint characters (hex) a body key carries to bind it to its record.
_BODY_FINGERPRINT_TAG_LEN = 16
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
# Record phases. PENDING carries only the fingerprint (no response yet); DONE
# carries the serialized response and is what a replay re-emits.
_PHASE_PENDING = "pending"
_PHASE_DONE = "done"


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


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    """The pending claim a ``FRESH`` :meth:`SharedStateIdempotencyStore.begin` took.

    ``record`` is the exact pending value written to the store (it embeds the
    claim's random token); :meth:`SharedStateIdempotencyStore.release` deletes
    the slot only while it still holds these bytes.
    """

    caller: str
    key: str
    record: bytes


@dataclass(frozen=True, slots=True)
class IdempotencyOutcome:
    """The result of a :meth:`SharedStateIdempotencyStore.begin` claim attempt."""

    state: IdempotencyState
    stored: StoredResponse | None = None
    retry_after_s: int = 0
    claim: IdempotencyClaim | None = None
    """Set only on ``FRESH``: the claim this request now owns."""


def _scrub_headers(headers: dict[str, str]) -> dict[str, str]:
    """Drop security-bearing headers before a response is persisted."""
    return {k: v for k, v in headers.items() if k.lower() not in SENSITIVE_RESPONSE_HEADERS}


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


def _dumps(record: Mapping[str, object]) -> bytes:
    return json.dumps(record, separators=_SEPARATORS).encode()


def _tombstone(fingerprint: str, status_code: int) -> bytes:
    """A DONE record with only the status: what a retry replays when nothing else fit."""
    return _dumps(
        {"phase": _PHASE_DONE, "fp": fingerprint, "status": status_code, "body_omitted": True}
    )


def _claim(fingerprint: str, token: str) -> bytes:
    """The PENDING claim, padded to the size of the largest :func:`_tombstone`.

    ``token`` is the claim's random token: it makes the record unique to this
    claim, so :meth:`SharedStateIdempotencyStore.release` (a compare-and-delete
    on the exact record) can never free a newer claim on the same key.

    Promoting the claim to a tombstone is then an overwrite with a value no
    larger than the current one, which a budgeted store always accepts: once
    the upstream call has run, a record that stops a retry re-running it can
    always be written.
    """
    base = {"phase": _PHASE_PENDING, "fp": fingerprint, "tok": token, "pad": ""}
    pad = len(_tombstone(fingerprint, _MAX_STATUS_CODE)) - len(_dumps(base))
    return _dumps({**base, "pad": " " * max(0, pad)})


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

    @staticmethod
    def _caller_segment(caller: str) -> str:
        # The caller is the key's second ``:`` segment (the memory backend's
        # per-owner share), so a ``:`` inside it is escaped.
        return caller.replace("%", "%25").replace(":", "%3A")

    def _slot(self, caller: str, key: str) -> str:
        return f"{_KEY_PREFIX}{self._caller_segment(caller)}:{key}"

    def _body_slot(self, caller: str, key: str, fingerprint: str) -> str:
        # Keyed by the request's fingerprint too, so a record only ever reads the
        # body its own request stored: when a claim expires mid-call and the key
        # is re-claimed by a different request, the two bodies cannot cross.
        tag = fingerprint[:_BODY_FINGERPRINT_TAG_LEN]
        return f"{_BODY_KEY_PREFIX}{self._caller_segment(caller)}:{key}:{tag}"

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
        claim = _claim(fingerprint, secrets.token_hex(16))
        try:
            claimed = await self._store.set_if_absent(slot, claim, ttl_s=self._pending_ttl)
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
            return IdempotencyOutcome(
                IdempotencyState.FRESH,
                claim=IdempotencyClaim(caller=caller, key=key, record=claim),
            )

        existing = await self._store.get(slot)
        if existing is None:
            # The claim expired between the failed set and the read — treat as a
            # narrow in-progress race; the caller retries.
            return IdempotencyOutcome(
                IdempotencyState.IN_PROGRESS, retry_after_s=self._retry_after()
            )

        record = json.loads(existing)
        if record.get("fp") != fingerprint:
            return IdempotencyOutcome(IdempotencyState.CONFLICT)
        if record.get("phase") != _PHASE_DONE:
            return IdempotencyOutcome(
                IdempotencyState.IN_PROGRESS, retry_after_s=self._retry_after()
            )
        body: bytes | None = None
        if record.get("body_ref"):
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
        headers: dict[str, str],
        body: bytes,
    ) -> None:
        """Promote the pending claim to the stored response on the long TTL.

        The record holds the status, the scrubbed headers (capped at
        :data:`MAX_RECORD_HEADER_BYTES`) and the fingerprint; the body is
        stored under its own key (namespace ``idem-body``) so bodies have their
        own budget and never crowd out claims. A body over
        ``max_response_bytes``, or one the store refuses, is omitted
        (``body_omitted``): the replay still prevents a duplicate side effect,
        only its byte-for-byte body is lost.

        When the store refuses the record too, the claim is overwritten with a
        status-only tombstone, which is never larger than the claim and so is
        always accepted while the claim is live. A retry then replays the
        status instead of running the upstream call again. The tombstone
        carries no body reference, so a body stored for it is released rather
        than left spending the caller's ``idem-body`` share.
        """
        omit = len(body) > self._max_response_bytes
        body_slot = self._body_slot(caller, key, fingerprint)
        body_stored = False
        if body and not omit:
            try:
                # Outlives the record, so a replay never finds the record without it.
                await self._store.set(body_slot, body, ttl_s=self._done_ttl + self._pending_ttl)
                body_stored = True
            except StateBackendCapacityError:
                omit = True
                _logger.warning("idempotency_body_not_stored", reason="body_budget_full")
        record = {
            "phase": _PHASE_DONE,
            "fp": fingerprint,
            "status": status_code,
            "headers": _cap_headers(_scrub_headers(headers)),
            "body_omitted": omit,
            "body_ref": bool(body) and not omit,
        }
        slot = self._slot(caller, key)
        try:
            await self._store.set(slot, _dumps(record), ttl_s=self._done_ttl)
            return
        except StateBackendCapacityError:
            pass
        if body_stored:
            await self._release_body(body_slot)
        try:
            await self._store.set(slot, _tombstone(fingerprint, status_code), ttl_s=self._done_ttl)
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
            return
        _logger.warning("idempotency_record_stored_as_tombstone", reason="store_full")

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

    async def release(self, claim: IdempotencyClaim) -> bool:
        """Give back a pending claim whose request failed before reaching the upstream.

        A compare-and-delete on the exact pending record: it is a no-op when the
        claim already expired and another request re-claimed the key, or when
        the record was promoted to ``DONE``. Returns whether the claim was
        released. Store errors propagate; the caller decides how to degrade.
        """
        return await self._store.delete_if_equals(self._slot(claim.caller, claim.key), claim.record)

    def _retry_after(self) -> int:
        # Round the short claim TTL up to whole seconds for the Retry-After hint.
        return max(1, int(self._pending_ttl + 0.999))

    @staticmethod
    def _decode_response(record: dict[str, object], body: bytes | None) -> StoredResponse:
        status = record.get("status")
        raw_headers = record.get("headers")
        headers = (
            {str(k): str(v) for k, v in raw_headers.items()}
            if isinstance(raw_headers, dict)
            else {}
        )
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
        )


__all__ = [
    "IdempotencyClaim",
    "IdempotencyOutcome",
    "IdempotencyState",
    "IdempotencyStateStore",
    "SharedStateIdempotencyStore",
    "StoredResponse",
]
