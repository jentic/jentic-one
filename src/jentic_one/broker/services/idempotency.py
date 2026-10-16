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
the response body is a separate ``idem-body:{caller}:{key}`` entry, so on the
memory backend bodies spend their own budget and a full body budget only
drops bodies from replays, never refuses claims.
"""

from __future__ import annotations

import base64
import json
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
    """The store roles idempotency needs: ``get``/``set`` (KV) + ``set_if_absent`` (atomic).

    A narrow union (ISP) over the shared-state backend — the claim is an atomic
    ``set_if_absent``; the pending read and the DONE promotion are plain KV
    ``get``/``set``. Both the memory and Redis backends satisfy it.
    """


_KEY_PREFIX = "idem:"
# Stored bodies live in their own namespace so they have their own budget.
_BODY_KEY_PREFIX = "idem-body:"
# Most serialized header bytes one record carries.
MAX_RECORD_HEADER_BYTES = 512
_MAX_STATUS_CODE = 999
_SEPARATORS = (",", ":")
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
class IdempotencyOutcome:
    """The result of a :meth:`SharedStateIdempotencyStore.begin` claim attempt."""

    state: IdempotencyState
    stored: StoredResponse | None = None
    retry_after_s: int = 0


def _scrub_headers(headers: dict[str, str]) -> dict[str, str]:
    """Drop security-bearing headers before a response is persisted."""
    return {k: v for k, v in headers.items() if k.lower() not in SENSITIVE_RESPONSE_HEADERS}


def _cap_headers(headers: dict[str, str]) -> dict[str, str]:
    """Keep headers (``content-type`` first) while their JSON stays within the cap.

    Bounds the record size: a response with very many or very long headers
    replays without the ones past the cap rather than inflating every record.
    """
    ordered = sorted(headers.items(), key=lambda kv: kv[0].lower() != "content-type")
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


def _claim(fingerprint: str) -> bytes:
    """The PENDING claim, padded to the size of the largest :func:`_tombstone`.

    Promoting the claim to a tombstone is then an overwrite with a value no
    larger than the current one, which a budgeted store always accepts: once
    the upstream call has run, a record that stops a retry re-running it can
    always be written.
    """
    base = {"phase": _PHASE_PENDING, "fp": fingerprint, "pad": ""}
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

    def _body_slot(self, caller: str, key: str) -> str:
        return f"{_BODY_KEY_PREFIX}{self._caller_segment(caller)}:{key}"

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
        try:
            claimed = await self._store.set_if_absent(
                slot, _claim(fingerprint), ttl_s=self._pending_ttl
            )
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
            return IdempotencyOutcome(IdempotencyState.FRESH)

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
            body = await self._store.get(self._body_slot(caller, key))
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
        status instead of running the upstream call again.
        """
        omit = len(body) > self._max_response_bytes
        body_slot = self._body_slot(caller, key)
        if body and not omit:
            try:
                # Outlives the record, so a replay never finds the record without it.
                await self._store.set(body_slot, body, ttl_s=self._done_ttl + self._pending_ttl)
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
    "IdempotencyOutcome",
    "IdempotencyState",
    "IdempotencyStateStore",
    "SharedStateIdempotencyStore",
    "StoredResponse",
]
