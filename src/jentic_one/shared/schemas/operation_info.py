"""Canonical operation identity model shared across layers."""

from pydantic import BaseModel, ConfigDict


class OperationInfo(BaseModel):
    """Identifies a resolved API operation.

    Bundles the registry's opaque operation ``id`` (the ``op_…`` hash) with the
    human-readable identity — the spec's path template and HTTP method — so the
    trio rides discovery → broker context → execution record as one object
    (mirroring how :class:`APIReference` carries the API identity). ``name`` /
    ``method`` are optional: legacy in-flight job payloads and historical rows
    carry only the id.
    """

    # Frozen: the identity is resolved once at discovery and must never be
    # edited mid-pipeline (it also rides frozen dataclasses like ResolveResult).
    model_config = ConfigDict(frozen=True)

    id: str
    # The operation's path template from the spec, e.g. ``/repos/{owner}/{repo}``
    # — "name" (not "path") to mirror the flat ``operation_name`` column/API field.
    name: str | None = None
    method: str | None = None
