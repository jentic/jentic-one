"""Behavioural contract for shared-state backends (``register_state_backend``).

The signature bases in :mod:`jentic_one.testing.compliance` prove a seam's
*shape*; a shared-state backend also has to *behave*: a claim is won once,
counters keep their first deadline, buckets refill at their rate, entries
expire. :class:`BaseStateBackendContract` runs every method of the
:class:`~jentic_one.shared.state.SharedStateBackend` protocol against a fresh
backend per test, the same suite the built-in backends pass.

Like the compliance bases it is named ``Base*`` so pytest does not collect it
directly. The tests are ``async def``: run the subclass under pytest-asyncio
in ``auto`` mode, or mark it with ``pytestmark = pytest.mark.asyncio``.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from jentic_one.shared.state import (
    AtomicStore,
    KeyValueStore,
    RateLimitStore,
    SharedStateBackend,
)
from jentic_one.testing.compliance import assert_signature_matches

#: Every method of the shared-state protocol, with the role protocol declaring it.
_PROTOCOL_METHODS: tuple[tuple[type, str], ...] = (
    (KeyValueStore, "get"),
    (KeyValueStore, "set"),
    (RateLimitStore, "token_bucket"),
    (AtomicStore, "incr_with_ttl"),
    (AtomicStore, "set_if_absent"),
    (AtomicStore, "aclose"),
)


class BaseStateBackendContract:
    """Subclass and override ``backend_factory`` to prove a backend behaves.

    ``backend_factory`` returns a fresh, ready backend; each test closes the
    one it builds. Expiry and refill are driven through :meth:`advance`, which
    sleeps by default; a backend with an injectable clock overrides it to move
    the clock instead::

        class TestMyBackendContract(BaseStateBackendContract):
            def backend_factory(self) -> SharedStateBackend:
                return MyBackend(...)

    Keys are unique per test, so a backend shared between test runs (one
    Redis, say) is safe as long as the factory isolates its key space.
    """

    #: TTL (seconds) the expiry tests write with. Real-time backends wait
    #: about twice this per expiry test; raise it if the backend's TTL
    #: resolution is coarser.
    ttl_s: float = 0.2

    def backend_factory(self) -> SharedStateBackend:
        raise NotImplementedError("Subclass must override backend_factory()")

    async def advance(self, seconds: float) -> None:
        """Let *seconds* pass for the backend (override for an injected clock)."""
        await asyncio.sleep(seconds)

    @asynccontextmanager
    async def _backend(self) -> AsyncIterator[SharedStateBackend]:
        backend = self.backend_factory()
        try:
            yield backend
        finally:
            await backend.aclose()

    @staticmethod
    def _key(namespace: str = "contract") -> str:
        return f"{namespace}:{uuid.uuid4().hex}"

    # -- shape ---------------------------------------------------------------

    async def test_is_shared_state_backend(self) -> None:
        async with self._backend() as backend:
            for role in (SharedStateBackend, KeyValueStore, RateLimitStore, AtomicStore):
                assert isinstance(backend, role), (
                    f"{type(backend).__name__} is not a {role.__name__}"
                )

    async def test_method_signatures(self) -> None:
        async with self._backend() as backend:
            impl = type(backend)
        for protocol, method in _PROTOCOL_METHODS:
            assert_signature_matches(impl, protocol, method)

    # -- key/value -----------------------------------------------------------

    async def test_get_missing_key_is_none(self) -> None:
        async with self._backend() as backend:
            assert await backend.get(self._key()) is None

    async def test_set_then_get_round_trips_bytes(self) -> None:
        async with self._backend() as backend:
            key = self._key()
            await backend.set(key, b"\x00value\xff", ttl_s=60.0)
            assert await backend.get(key) == b"\x00value\xff"

    async def test_set_overwrites(self) -> None:
        async with self._backend() as backend:
            key = self._key()
            await backend.set(key, b"first", ttl_s=60.0)
            await backend.set(key, b"second", ttl_s=60.0)
            assert await backend.get(key) == b"second"

    async def test_set_expires_after_its_ttl(self) -> None:
        async with self._backend() as backend:
            key = self._key()
            await backend.set(key, b"v", ttl_s=self.ttl_s)
            assert await backend.get(key) == b"v"
            await self.advance(self.ttl_s * 2)
            assert await backend.get(key) is None

    async def test_keys_are_independent(self) -> None:
        async with self._backend() as backend:
            a, b = self._key(), self._key()
            await backend.set(a, b"a", ttl_s=60.0)
            assert await backend.get(b) is None

    # -- claims --------------------------------------------------------------

    async def test_set_if_absent_first_claim_wins(self) -> None:
        async with self._backend() as backend:
            key = self._key("idem")
            assert await backend.set_if_absent(key, b"first", ttl_s=60.0) is True
            assert await backend.set_if_absent(key, b"second", ttl_s=60.0) is False
            assert await backend.get(key) == b"first"

    async def test_set_if_absent_refuses_a_key_written_by_set(self) -> None:
        async with self._backend() as backend:
            key = self._key("idem")
            await backend.set(key, b"stored", ttl_s=60.0)
            assert await backend.set_if_absent(key, b"claim", ttl_s=60.0) is False
            assert await backend.get(key) == b"stored"

    async def test_set_if_absent_succeeds_again_after_expiry(self) -> None:
        async with self._backend() as backend:
            key = self._key("idem")
            assert await backend.set_if_absent(key, b"first", ttl_s=self.ttl_s) is True
            await self.advance(self.ttl_s * 2)
            assert await backend.set_if_absent(key, b"second", ttl_s=60.0) is True
            assert await backend.get(key) == b"second"

    async def test_concurrent_claims_have_one_winner(self) -> None:
        async with self._backend() as backend:
            key = self._key("idem")
            results = await asyncio.gather(
                *(backend.set_if_absent(key, str(i).encode(), ttl_s=60.0) for i in range(20))
            )
            assert results.count(True) == 1

    # -- counters ------------------------------------------------------------

    async def test_incr_with_ttl_accumulates(self) -> None:
        async with self._backend() as backend:
            key = self._key("cb")
            assert await backend.incr_with_ttl(key, ttl_s=60.0) == 1
            assert await backend.incr_with_ttl(key, ttl_s=60.0) == 2
            assert await backend.incr_with_ttl(key, ttl_s=60.0, amount=5) == 7

    async def test_concurrent_increments_are_atomic(self) -> None:
        async with self._backend() as backend:
            key = self._key("cb")
            values = await asyncio.gather(
                *(backend.incr_with_ttl(key, ttl_s=60.0) for _ in range(50))
            )
            assert sorted(values) == list(range(1, 51))

    async def test_incr_with_ttl_keeps_the_first_deadline(self) -> None:
        # A later increment must not extend the window, or a steady trickle of
        # failures would keep a circuit-breaker window open forever.
        async with self._backend() as backend:
            key = self._key("cb")
            await backend.incr_with_ttl(key, ttl_s=self.ttl_s * 2)
            await self.advance(self.ttl_s * 1.2)
            assert await backend.incr_with_ttl(key, ttl_s=self.ttl_s * 2) == 2
            await self.advance(self.ttl_s * 1.6)
            assert await backend.incr_with_ttl(key, ttl_s=self.ttl_s * 2) == 1

    async def test_incr_with_ttl_rejects_a_non_positive_amount(self) -> None:
        async with self._backend() as backend:
            for amount in (0, -1):
                try:
                    await backend.incr_with_ttl(self._key("cb"), ttl_s=60.0, amount=amount)
                except ValueError:
                    continue
                raise AssertionError(f"incr_with_ttl accepted amount={amount}")

    # -- token buckets -------------------------------------------------------

    async def test_token_bucket_allows_the_burst_then_denies(self) -> None:
        async with self._backend() as backend:
            key = self._key("rl")
            allowed = [await backend.token_bucket(key, rate=0.01, burst=3) for _ in range(3)]
            assert [d.allowed for d in allowed] == [True, True, True]
            assert [d.remaining for d in allowed] == [2, 1, 0]
            assert all(d.limit == 3 and d.retry_after_s == 0.0 for d in allowed)
            denied = await backend.token_bucket(key, rate=0.01, burst=3)
            assert denied.allowed is False
            assert denied.remaining == 0
            assert denied.limit == 3
            assert denied.retry_after_s > 0

    async def test_token_bucket_charges_the_cost(self) -> None:
        async with self._backend() as backend:
            key = self._key("rl")
            first = await backend.token_bucket(key, rate=0.01, burst=5, cost=3)
            assert first.allowed is True
            assert first.remaining == 2
            assert (await backend.token_bucket(key, rate=0.01, burst=5, cost=3)).allowed is False
            assert (await backend.token_bucket(key, rate=0.01, burst=5, cost=2)).allowed is True

    async def test_token_bucket_refills_at_its_rate(self) -> None:
        async with self._backend() as backend:
            key = self._key("rl")
            rate = 2.0 / self.ttl_s
            assert (await backend.token_bucket(key, rate=rate, burst=1)).allowed is True
            assert (await backend.token_bucket(key, rate=rate, burst=1)).allowed is False
            await self.advance(self.ttl_s)
            assert (await backend.token_bucket(key, rate=rate, burst=1)).allowed is True

    async def test_token_buckets_are_independent(self) -> None:
        async with self._backend() as backend:
            a, b = self._key("rl"), self._key("rl")
            assert (await backend.token_bucket(a, rate=0.01, burst=1)).allowed is True
            assert (await backend.token_bucket(a, rate=0.01, burst=1)).allowed is False
            assert (await backend.token_bucket(b, rate=0.01, burst=1)).allowed is True

    # -- lifecycle -----------------------------------------------------------

    async def test_aclose_releases_the_backend(self) -> None:
        """``aclose()`` completes once the backend has been used.

        The process calls it exactly once, at shutdown, so it need not be
        idempotent; a backend whose ``aclose()`` raises after use fails here.
        """
        backend = self.backend_factory()
        await backend.set(self._key(), b"v", ttl_s=60.0)
        await backend.aclose()
