"""Canonical operation identity model shared across layers."""

from pydantic import BaseModel


class OperationInfo(BaseModel):
    """Identifies a resolved API operation.

    Bundles the registry's opaque operation ``id`` (the ``op_…`` hash) with the
    human-readable identity — the spec's path template and HTTP method — so the
    trio rides discovery → broker context → execution record as one object
    (mirroring how :class:`APIReference` carries the API identity). ``name`` /
    ``method`` are optional: legacy in-flight job payloads and historical rows
    carry only the id.
    """

    id: str
    # The operation's path template from the spec, e.g. ``/repos/{owner}/{repo}``.
    name: str | None = None
    # The operation's HTTP method, e.g. ``GET``.
    method: str | None = None
