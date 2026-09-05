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
from dataclasses import dataclass

from jentic_one.registry.repos.governed_hosts_repo import GovernedApi, GovernedHostsRepository
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType


def compute_hosts_digest(hosts: Iterable[str]) -> str:
    """SHA-256 hex digest over the canonical host list.

    Canonical form: lowercased, stripped, deduplicated, sorted, newline-joined.
    Content-derived — no version counter, no extra table — so it is correct
    across the three source databases by construction, and two deployments with
    the same host set produce the same digest. The empty set has a stable digest
    (the hash of the empty string).
    """
    canonical = sorted({host.strip().lower() for host in hosts if host and host.strip()})
    return hashlib.sha256("\n".join(canonical).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class GovernedHostView:
    """One governed host and the identity's APIs behind it."""

    host: str
    apis: tuple[GovernedApi, ...]


@dataclass(frozen=True, slots=True)
class GovernedHostsView:
    """The identity's full governed host set with its change digest."""

    data: tuple[GovernedHostView, ...]
    digest: str


_EMPTY_VIEW = GovernedHostsView(data=(), digest=compute_hosts_digest(()))


class GovernedHostsService:
    """Derives the caller's governed host set across the three databases.

    **Always self-scoped**: the set is derived for the authenticated identity's
    own toolkit bindings — there is deliberately no cross-actor variant (admins
    inspect other actors through the toolkit/binding admin reads).
    """

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def get_governed_hosts(self, identity: Identity) -> GovernedHostsView:
        """Derive the host set for ``identity`` (sorted by host, with digest).

        A toolkit key authenticates *as the toolkit itself* (its ``sub`` is the
        toolkit id — see ``broker/repos/toolkit_key_resolver.py``), so the
        admin binding leg short-circuits. APIs whose current revision has no
        resolvable server host are omitted: the response is keyed by host, so a
        hostless API has no divert-list contribution.
        """
        if identity.actor_type is ActorType.TOOLKIT:
            toolkit_ids = {identity.sub}
        else:
            async with self._ctx.admin_db.session() as session:
                toolkit_ids = await GovernedHostsRepository.toolkit_ids_for_identity(
                    session, sub=identity.sub
                )
        if not toolkit_ids:
            return _EMPTY_VIEW

        async with self._ctx.control_db.session() as session:
            scopes = await GovernedHostsRepository.credential_scopes_for_toolkits(
                session, toolkit_ids=toolkit_ids
            )
        if not scopes:
            return _EMPTY_VIEW

        async with self._ctx.registry_db.session() as session:
            apis = await GovernedHostsRepository.apis_for_scopes(session, scopes=scopes)

        by_host: dict[str, list[GovernedApi]] = {}
        for api in apis:
            if api.host is None:
                continue
            by_host.setdefault(api.host.strip().lower(), []).append(api)

        data = tuple(
            GovernedHostView(
                host=host,
                apis=tuple(sorted(by_host[host], key=lambda a: (a.vendor, a.name, a.version))),
            )
            for host in sorted(by_host)
        )
        return GovernedHostsView(data=data, digest=compute_hosts_digest(by_host))
