"""An API's live revision as the control connect flow needs it.

Backs ``SecuritySchemesLookupProtocol`` (``shared/catalog/protocols.py``): the
declared security schemes, the origin set the revision's servers can target,
the host-position server variables that are not limited to an ``enum``, and
the revision's provenance. Wired onto the control app by ``wiring.py`` so
control never imports the registry.
"""

from __future__ import annotations

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.security_schemes import SecurityScheme
from jentic_one.registry.core.schema.servers import Server
from jentic_one.registry.core.server_hosts import hosts_from_servers, unpinned_host_variables
from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.shared.catalog import ApiProvenance, ApiSecurityView, DeclaredSecurityScheme
from jentic_one.shared.context import Context
from jentic_one.shared.models import ApiRevisionState

#: Revision states that serve traffic (the "live" revision).
_LIVE_REVISION_STATES = frozenset(
    {ApiRevisionState.PUBLISHED.value, ApiRevisionState.IMPORTED.value}
)


def _declared_scheme(scheme: SecurityScheme) -> DeclaredSecurityScheme:
    scopes: list[str] = []
    authorization_url: str | None = None
    token_url: str | None = None
    for flow in scheme.flows:
        for name in flow.scopes or {}:
            if name not in scopes:
                scopes.append(str(name))
        if flow.flow_type == "authorizationCode":
            authorization_url = flow.authorization_url
            token_url = flow.token_url
    return DeclaredSecurityScheme(
        name=scheme.name,
        type=scheme.type,
        http_scheme=scheme.scheme.lower() if scheme.scheme else None,
        location=scheme.in_location,
        field_name=scheme.param_name,
        oauth_scopes=tuple(scopes),
        authorization_url=authorization_url,
        token_url=token_url,
    )


def _server_values(server: Server) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """``(candidate values, enum values)`` per variable of one server."""
    values: dict[str, list[str]] = {}
    enums: dict[str, list[str]] = {}
    for variable in server.variables:
        candidates: list[str] = []
        if variable.default_value is not None:
            candidates.append(variable.default_value)
        enum = (
            [str(v) for v in variable.enum if v is not None]
            if isinstance(variable.enum, list)
            else []
        )
        candidates.extend(v for v in enum if v not in candidates)
        values[variable.name] = candidates
        enums[variable.name] = enum
    return values, enums


class ApiSecurityLookupService:
    """Read-only view of an API's current revision for connect sessions."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def lookup(self, *, vendor: str, name: str, version: str) -> ApiSecurityView | None:
        async with self._ctx.registry_db.session() as session:
            api = await ApiRepository.get_by_identifier_with_current_revision(
                session, vendor, name, version
            )
            if api is None or api.current_revision is None:
                return None
            revision: ApiRevision = api.current_revision
            if revision.state not in _LIVE_REVISION_STATES:
                return None

            specs: list[tuple[str, dict[str, list[str]]]] = []
            unpinned: list[str] = []
            for server in revision.servers:
                values, enums = _server_values(server)
                specs.append((server.url, values))
                for variable in unpinned_host_variables(server.url, enums):
                    if variable not in unpinned:
                        unpinned.append(variable)

            return ApiSecurityView(
                vendor=api.vendor,
                name=api.name,
                version=api.version,
                display_name=api.display_name,
                revision_id=str(revision.id),
                schemes=tuple(
                    _declared_scheme(s)
                    for s in sorted(revision.security_schemes, key=lambda s: s.name)
                ),
                hosts=tuple(sorted(hosts_from_servers(specs))),
                provenance=ApiProvenance(
                    origin=revision.origin,
                    catalog_api_id=api.catalog_api_id,
                    submitted_by=revision.submitted_by,
                    source_url=revision.source_url,
                ),
                unpinned_host_variables=tuple(unpinned),
            )
