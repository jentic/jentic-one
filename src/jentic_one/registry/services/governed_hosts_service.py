"""Identity-scoped governed-host derivation for ``GET /governed-hosts`` (#1278).

Derives the minimum host set an integrator needs to scope interception for one
identity (per-agent proxy catch-lists, least-knowledge host filtering), plus a
content-derived digest for O(1) ETag change-polling. The pipeline is the one the
broker already runs per-request (admin bindings → control credential scopes →
registry resolution), inverted to enumerate rather than match — see
``registry/repos/governed_hosts_repo.py``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass

import structlog

from jentic_one.registry.repos.governed_hosts_repo import GovernedHostsRepository
from jentic_one.registry.services.errors import GovernedHostsUnavailableError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType

logger = structlog.get_logger(__name__)


def canonical_hosts(hosts: Iterable[str]) -> list[str]:
    """Canonicalise a host set: normalised entries, deduplicated, sorted.

    Per-entry normalisation: lowercased, whitespace-stripped, FQDN root dot
    removed, and internationalised names IDNA-encoded to the A-label
    (punycode) form — the only form a TLS/SNI- or DNS-keyed gate ever sees.
    Entries that IDNA cannot encode are kept verbatim (lowercased) rather
    than dropped: under-inclusion is the unsafe direction here.
    """
    canonical: set[str] = set()
    for host in hosts:
        entry = host.strip().lower().rstrip(".") if host else ""
        if not entry:
            continue
        if not entry.isascii():
            # Unencodable entries are kept verbatim rather than dropped —
            # under-inclusion is the unsafe direction for a divert list.
            with suppress(UnicodeError):
                entry = entry.encode("idna").decode("ascii")
        canonical.add(entry)
    return sorted(canonical)


def compute_hosts_digest(hosts: Iterable[str]) -> str:
    """SHA-256 hex digest over the canonical host list.

    Canonical form: :func:`canonical_hosts`, newline-joined — exactly the
    ``data`` list the endpoint returns, so the digest (and the ETag built from
    it) always identifies the full response body. Content-derived — no version
    counter, no extra table — so it is correct across the three source
    databases by construction, and two deployments with the same host set
    produce the same digest. The empty set has a stable digest (the hash of
    the empty string).
    """
    return hashlib.sha256("\n".join(canonical_hosts(hosts)).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class GovernedHostsView:
    """The identity's full governed host set with its change digest."""

    hosts: tuple[str, ...]
    digest: str


_EMPTY_VIEW = GovernedHostsView(hosts=(), digest=compute_hosts_digest(()))


class GovernedHostsService:
    """Derives the caller's governed host set across the three databases.

    **Always self-scoped**: the set is derived for the authenticated identity's
    own toolkit bindings — there is deliberately no cross-actor or admin
    variant.
    """

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def get_governed_hosts(self, identity: Identity) -> GovernedHostsView:
        """Derive the host set for ``identity`` (canonical order, with digest).

        A toolkit key authenticates *as the toolkit itself* (its ``sub`` is the
        toolkit id — see ``broker/repos/toolkit_key_resolver.py``), so the
        admin binding leg short-circuits. Other actor types resolve through
        ``agent_toolkit_bindings`` — a plain **user** token therefore yields an
        empty set (toolkits bind to agents, not users; the OAuth agent-consent
        flow is what leaves an integrator holding an agent-scoped token).

        Hosts are the literal URL-index patterns the broker's discovery
        matches; variable-bearing (``{var}``) hosts are excluded because
        discovery never matches them (see ``governed_hosts_repo.py``).

        Every early return logs its reason — an empty set is what a
        misconfigured caller also sees, so the distinction must be
        operator-visible even though the wire body is the same.

        Raises :class:`GovernedHostsUnavailableError` (→ 503) when the process
        lacks a required database leg — a misdeployment must fail loudly, not
        as a bare 500 (or worse, an empty 200 a gate would read as "govern
        nothing").
        """
        for db_name in ("admin", "control", "registry"):
            if not self._ctx.is_db_allowed(db_name):
                raise GovernedHostsUnavailableError(db_name)

        if identity.actor_type is ActorType.TOOLKIT:
            toolkit_ids = {identity.sub}
        else:
            async with self._ctx.admin_db.session() as session:
                toolkit_ids = await GovernedHostsRepository.toolkit_ids_for_identity(
                    session, sub=identity.sub
                )
        if not toolkit_ids:
            logger.info(
                "governed_hosts_empty",
                reason="no_toolkit_bindings",
                actor_type=identity.actor_type,
            )
            return _EMPTY_VIEW

        async with self._ctx.control_db.session() as session:
            scopes = await GovernedHostsRepository.credential_scopes_for_toolkits(
                session, toolkit_ids=toolkit_ids
            )
        if not scopes:
            logger.info(
                "governed_hosts_empty",
                reason="no_credential_scopes",
                actor_type=identity.actor_type,
                toolkit_count=len(toolkit_ids),
            )
            return _EMPTY_VIEW

        async with self._ctx.registry_db.session() as session:
            hosts = await GovernedHostsRepository.hosts_for_scopes(session, scopes=scopes)

        canonical = canonical_hosts(hosts)
        digest = compute_hosts_digest(canonical)
        # Counts and digest only — the host list itself is credential-coverage
        # metadata and stays out of the logs.
        logger.info(
            "governed_hosts_derived",
            actor_type=identity.actor_type,
            toolkit_count=len(toolkit_ids),
            scope_count=len(scopes),
            host_count=len(canonical),
            digest=digest,
        )
        return GovernedHostsView(hosts=tuple(canonical), digest=digest)
