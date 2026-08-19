"""Webhook signing-secret storage, resolution and rotation.

**Why this exists.** Every other secret in the platform is hashed — a password
or invite token only ever needs to be *compared*, so a one-way digest is both
sufficient and safer. A webhook secret is different: HMAC signing recomputes
a signature from the key, so the key itself must be recoverable. Hashing it makes
signing impossible, which is why these secrets are stored encrypted.

So secrets are **encrypted, not hashed**, via the ``EncryptionService`` facade
(AES-256-GCM, versioned keys). Compared with a hash we accept that a database
leak *plus* the encryption key yields usable secrets; the mitigation is that the
two live in different places, which is the same trade already made for stored
upstream credentials.

``secret_hash`` is still populated, but only as a non-reversible fingerprint for
reuse detection and for comparing without decrypting. It is never used to verify.

**Rotation.** Replacing a secret outright breaks every request already in flight
and forces a synchronised change on the far side. Instead ``rotate`` keeps the old
secret as ``previous_secret_encrypted`` with an expiry: during the grace window
both keys are offered on outbound deliveries, so the two sides can be updated
independently. ``resolve_secrets`` returns the candidates newest-first, and drops
the previous one once its expiry passes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog

from jentic_one.admin.core.schema.webhook_endpoints import WebhookEndpoint
from jentic_one.admin.services._support.webhook_secrets import generate_webhook_secret
from jentic_one.shared.context import Context
from jentic_one.shared.crypto import DecryptionError
from jentic_one.shared.webhooks.signing import hash_secret

logger = structlog.get_logger(__name__)

DEFAULT_ROTATION_GRACE = timedelta(hours=24)


class WebhookSecretService:
    """Encrypts, resolves and rotates per-endpoint signing secrets."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    def new_secret(self) -> tuple[str, str, str]:
        """Mint a secret. Returns ``(plaintext, encrypted, fingerprint)``.

        The plaintext is returned so the caller can show it to the operator
        **once**, at creation — it is never recoverable through the API
        afterwards, only internally for signing outbound deliveries.
        """
        plaintext = generate_webhook_secret()
        return plaintext, self.encrypt(plaintext), hash_secret(plaintext)

    def encrypt(self, plaintext: str) -> str:
        return self._ctx.encryption.encrypt(plaintext)

    def resolve_secrets(self, endpoint: WebhookEndpoint) -> list[str]:
        """Plaintext secrets valid for this endpoint right now, newest first.

        Returns a *list* because rotation can leave two keys live: a sender that
        has not yet picked up the new secret is still signing with the old one,
        and rejecting it would mean dropping legitimate events. Callers should try
        each candidate.

        A secret that cannot be decrypted is **skipped, not raised** — if the
        current key is undecryptable (say the keyset was rolled without
        re-encrypting) but the previous one still works, signing should keep
        working rather than fail closed on every delivery. An empty list means "no
        usable secret", which callers must treat as *refuse*, never as *allow*.
        """
        candidates: list[str] = []

        current = self._safe_decrypt(endpoint.secret_encrypted, endpoint.id, "current")
        if current is not None:
            candidates.append(current)

        if endpoint.previous_secret_encrypted and self._previous_is_live(endpoint):
            previous = self._safe_decrypt(
                endpoint.previous_secret_encrypted, endpoint.id, "previous"
            )
            if previous is not None:
                candidates.append(previous)

        return candidates

    def resolve_signing_secret(self, endpoint: WebhookEndpoint) -> str | None:
        """The single secret to *sign* outbound deliveries with.

        Signing always uses the newest key — unlike resolution for the grace
        window, there is no ambiguity about which to use, and continuing to sign
        with a rotated-out secret would defeat the point of rotating.
        """
        candidates = self.resolve_secrets(endpoint)
        return candidates[0] if candidates else None

    def rotate(
        self,
        endpoint: WebhookEndpoint,
        *,
        grace: timedelta = DEFAULT_ROTATION_GRACE,
    ) -> str:
        """Issue a new secret, keeping the old one valid for ``grace``.

        Mutates the endpoint in the caller's session; the caller controls the
        transaction (repositories and services here never commit). Returns the new
        plaintext to hand to the operator once.

        Passing ``grace=timedelta(0)`` revokes the old secret immediately — the
        right move if it has leaked, at the cost of dropping in-flight events.
        """
        plaintext, encrypted, fingerprint = self.new_secret()

        if grace > timedelta(0):
            endpoint.previous_secret_encrypted = endpoint.secret_encrypted
            endpoint.previous_secret_expires_at = datetime.now(UTC) + grace
        else:
            endpoint.previous_secret_encrypted = None
            endpoint.previous_secret_expires_at = None

        endpoint.secret_encrypted = encrypted
        endpoint.secret_hash = fingerprint

        logger.info(
            "webhook_secret_rotated",
            endpoint_id=endpoint.id,
            grace_seconds=int(grace.total_seconds()),
        )
        return plaintext

    def _previous_is_live(self, endpoint: WebhookEndpoint) -> bool:
        """Whether the previous secret is still inside its grace window.

        A missing expiry is treated as **expired**, not eternal: failing closed is
        the safe reading of ambiguous data for a credential.
        """
        expires_at = endpoint.previous_secret_expires_at
        if expires_at is None:
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at > datetime.now(UTC)

    def _safe_decrypt(self, blob: str, endpoint_id: str, which: str) -> str | None:
        try:
            return self._ctx.encryption.decrypt(blob)
        except DecryptionError:
            # Logged loudly: this means a configured endpoint has become
            # unusable, which is an operator problem, not a request problem.
            logger.error(
                "webhook_secret_undecryptable",
                endpoint_id=endpoint_id,
                which=which,
            )
            return None
