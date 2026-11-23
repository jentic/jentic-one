"""Argon2id password hashing and verification."""

from __future__ import annotations

import asyncio
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeGuard

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from jentic_one.admin.services.errors import PasswordHasherBusyError

# Single source of truth for the minimum password length. The Pydantic request
# schemas enforce this for the web path (Field(min_length=...)); the service
# layer re-checks it for the CLI path that bypasses the schema. Keep this in
# lockstep with the Go CLI's `minPasswordLen` and the UI's MIN_PASSWORD_LENGTH.
MIN_PASSWORD_LENGTH = 12
# Reused so every "too short" rejection reads identically across call sites.
PASSWORD_TOO_SHORT_MESSAGE = f"Password must be at least {MIN_PASSWORD_LENGTH} characters"

DEFAULT_ARGON2_MAX_CONCURRENCY = 4
DEFAULT_ARGON2_ACQUIRE_TIMEOUT_S = 5.0

_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    """Hash a plaintext password using argon2id."""
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against an argon2id hash."""
    try:
        return _hasher.verify(hashed, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# Hashed once per process, with the *current* hasher parameters, so the dummy
# verification below costs the same as a real one even if argon2's defaults
# move. The preimage is irrelevant — dummy_verify_password never passes.
_DUMMY_HASH: str | None = None
_DUMMY_HASH_LOCK = threading.Lock()


def dummy_password_hash() -> str:
    """Return the cached argon2id hash used as the timing-equalizer target."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        with _DUMMY_HASH_LOCK:
            if _DUMMY_HASH is None:
                _DUMMY_HASH = _hasher.hash("jentic-one-timing-equalizer")
    return _DUMMY_HASH


def dummy_verify_password() -> None:
    """Burn one argon2id verification against a static dummy hash.

    Called on the credential-check arms that would otherwise return *before*
    any hash verification (unknown email, inactive user, missing secret,
    active lockout) so response timing does not separate "account exists"
    from "account does not": every rejection pays the same argon2 cost.
    The result is deliberately discarded.
    """
    verify_password("timing-equalizer-mismatch", dummy_password_hash())


def needs_rehash(hashed: str) -> bool:
    """Check if a hash needs to be re-hashed with updated parameters."""
    return _hasher.check_needs_rehash(hashed)


@dataclass(frozen=True, slots=True)
class _Argon2Gate:
    """Process-wide semaphore bounding concurrent argon2 executions on a loop."""

    loop: asyncio.AbstractEventLoop
    limit: int
    semaphore: asyncio.Semaphore


_max_concurrency: int = DEFAULT_ARGON2_MAX_CONCURRENCY
_acquire_timeout_s: float = DEFAULT_ARGON2_ACQUIRE_TIMEOUT_S
_gate: _Argon2Gate | None = None


def _is_real_int(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_real_number(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool)


def configure_argon2(
    *,
    max_concurrency: int | None = None,
    acquire_timeout_s: float | None = None,
) -> None:
    """Configure the process-wide argon2 concurrency limit and slot wait timeout."""
    global _max_concurrency, _acquire_timeout_s, _gate
    if (
        _is_real_int(max_concurrency)
        and max_concurrency >= 1
        and max_concurrency != _max_concurrency
    ):
        _max_concurrency = max_concurrency
        _gate = None
    if _is_real_number(acquire_timeout_s) and acquire_timeout_s > 0:
        _acquire_timeout_s = float(acquire_timeout_s)


def configure_argon2_from_config(config: Any) -> None:
    """Apply ``admin.auth`` argon2 bounds from an ``AppConfig`` or ``AdminAuthConfig``."""
    auth_cfg = getattr(getattr(config, "admin", None), "auth", config)
    max_conc = getattr(auth_cfg, "argon2_max_concurrency", None)
    timeout_s = getattr(auth_cfg, "argon2_acquire_timeout_s", None)
    configure_argon2(max_concurrency=max_conc, acquire_timeout_s=timeout_s)


def reset_argon2_gate() -> None:
    """Reset the process-wide argon2 gate to default bounds. For testing only."""
    global _max_concurrency, _acquire_timeout_s, _gate
    _max_concurrency = DEFAULT_ARGON2_MAX_CONCURRENCY
    _acquire_timeout_s = DEFAULT_ARGON2_ACQUIRE_TIMEOUT_S
    _gate = None


def _argon2_gate(limit: int) -> _Argon2Gate:
    """Return the process-wide argon2 semaphore gate for the running event loop."""
    global _gate
    loop = asyncio.get_running_loop()
    if _gate is None or _gate.loop is not loop or _gate.limit != limit:
        _gate = _Argon2Gate(loop=loop, limit=limit, semaphore=asyncio.Semaphore(limit))
    return _gate


async def run_argon2[T](
    fn: Callable[..., T],
    /,
    *args: Any,
    config: Any | None = None,
    max_concurrency: int | None = None,
    acquire_timeout_s: float | None = None,
) -> T:
    """Run an argon2 callable off the event loop under the process-wide semaphore.

    At most ``max_concurrency`` argon2 operations execute concurrently across
    the process. Callers beyond the cap wait up to ``acquire_timeout_s`` for a
    slot on the event loop before submitting to the executor or allocating the
    64 MiB argon2 buffer, and raise :class:`PasswordHasherBusyError` on timeout.
    """
    if config is not None:
        configure_argon2_from_config(config)
    if max_concurrency is not None or acquire_timeout_s is not None:
        configure_argon2(max_concurrency=max_concurrency, acquire_timeout_s=acquire_timeout_s)

    limit = _max_concurrency
    timeout_s = _acquire_timeout_s
    gate = _argon2_gate(limit)
    retry_after_s = max(1, math.ceil(timeout_s))
    try:
        async with asyncio.timeout(timeout_s):
            await gate.semaphore.acquire()
    except TimeoutError as exc:
        raise PasswordHasherBusyError(retry_after_s=retry_after_s) from exc

    try:
        fut = gate.loop.run_in_executor(None, fn, *args)
    except BaseException:
        gate.semaphore.release()
        raise

    def _release_slot(done_fut: asyncio.Future[T]) -> None:
        if not done_fut.cancelled():
            _ = done_fut.exception()
        gate.semaphore.release()

    fut.add_done_callback(_release_slot)
    return await asyncio.shield(fut)


async def hash_password_async(plain: str, *, config: Any | None = None) -> str:
    """Hash a plaintext password off the event loop under the process-wide concurrency bound."""
    return await run_argon2(hash_password, plain, config=config)


async def verify_password_async(plain: str, hashed: str, *, config: Any | None = None) -> bool:
    """Verify a plaintext password off the event loop under the process-wide concurrency bound."""
    return await run_argon2(verify_password, plain, hashed, config=config)


async def dummy_verify_password_async(*, config: Any | None = None) -> None:
    """Run the timing-equalizer argon2 verification off the event loop under the cap."""
    await run_argon2(dummy_verify_password, config=config)
