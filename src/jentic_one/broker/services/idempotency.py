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
token.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from jentic_one.broker.core.headers import SENSITIVE_RESPONSE_HEADERS
from jentic_one.shared.state.backend import AtomicStore, KeyValueStore


@runtime_checkable
class IdempotencyStateStore(KeyValueStore, AtomicStore, Protocol):
    """The store roles idempotency needs: ``get``/``set`` (KV) + ``set_if_absent`` (atomic).

    A narrow union (ISP) over the shared-state backend — the claim is an atomic
    ``set_if_absent``; the pending read and the DONE promotion are plain KV
    ``get``/``set``. Both the memory and Redis backends satisfy it.
    """


_KEY_PREFIX = "idem:"
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

    def _slot(self, caller: str, key: str) -> str:
        return f"{_KEY_PREFIX}{caller}:{key}"

    async def begin(self, caller: str, key: str, fingerprint: str) -> IdempotencyOutcome:
        """Atomically claim ``(caller, key)`` or classify an existing claim.

        A successful ``set_if_absent`` ⇒ ``FRESH`` (we own it, execute). Otherwise
        decode the existing record: a different fingerprint ⇒ ``CONFLICT``; a
        matching fingerprint that is still ``PENDING`` ⇒ ``IN_PROGRESS``; a
        matching ``DONE`` record ⇒ ``REPLAY`` with the stored response.
        """
        slot = self._slot(caller, key)
        claim = json.dumps({"phase": _PHASE_PENDING, "fp": fingerprint}).encode()
        if await self._store.set_if_absent(slot, claim, ttl_s=self._pending_ttl):
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
        return IdempotencyOutcome(IdempotencyState.REPLAY, stored=self._decode_response(record))

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

        Sensitive response headers are scrubbed before serialization, and a body
        over ``max_response_bytes`` is dropped (``body_omitted``) — the
        no-duplicate-execution guarantee is preserved; only byte-identical replay
        of a rare oversized body is lost.
        """
        omit = len(body) > self._max_response_bytes
        record = {
            "phase": _PHASE_DONE,
            "fp": fingerprint,
            "status": status_code,
            "headers": _scrub_headers(headers),
            "body_omitted": omit,
            "body_b64": "" if omit else base64.b64encode(body).decode(),
        }
        slot = self._slot(caller, key)
        await self._store.set(slot, json.dumps(record).encode(), ttl_s=self._done_ttl)

    def _retry_after(self) -> int:
        # Round the short claim TTL up to whole seconds for the Retry-After hint.
        return max(1, int(self._pending_ttl + 0.999))

    @staticmethod
    def _decode_response(record: dict[str, object]) -> StoredResponse:
        status = record.get("status")
        body_b64 = record.get("body_b64")
        body_str = body_b64 if isinstance(body_b64, str) else ""
        raw_headers = record.get("headers")
        headers = (
            {str(k): str(v) for k, v in raw_headers.items()}
            if isinstance(raw_headers, dict)
            else {}
        )
        return StoredResponse(
            status_code=status if isinstance(status, int) else 0,
            headers=headers,
            body=base64.b64decode(body_str) if body_str else b"",
            body_omitted=bool(record.get("body_omitted")),
        )


__all__ = [
    "IdempotencyOutcome",
    "IdempotencyState",
    "IdempotencyStateStore",
    "SharedStateIdempotencyStore",
    "StoredResponse",
]
