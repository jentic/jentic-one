"""Response schemas for the governed-hosts endpoint (#1278)."""

from __future__ import annotations

from pydantic import BaseModel

from jentic_one.registry.web.schemas.apis import ApiReferenceResponse


class GovernedHostResponse(BaseModel):
    """One governed host with the caller's APIs behind it."""

    host: str
    apis: list[ApiReferenceResponse]


class GovernedHostsResponse(BaseModel):
    """The caller's governed host set, sorted by host, with its change digest.

    Deliberately **unpaginated**: the set is bounded by the caller's own toolkit
    bindings (tens of hosts, not thousands) and the digest must cover the whole
    set atomically — a paginated digest would be meaningless. This is a
    documented deviation from the list-endpoint pagination convention.
    """

    data: list[GovernedHostResponse]
    digest: str
