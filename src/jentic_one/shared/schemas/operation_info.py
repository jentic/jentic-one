"""Canonical operation identity model shared across layers."""

from pydantic import BaseModel, ConfigDict


class OperationInfo(BaseModel):
    """Identifies a resolved API operation.

    Bundles the registry's opaque operation ``id`` (the ``op_…`` hash) with the
    human-readable identity — the spec's path template and HTTP method — so the
    trio rides discovery → broker context → execution record as one object
    (mirroring how :class:`APIReference` carries the API identity). ``path`` /
    ``method`` are optional: legacy in-flight job payloads and historical rows
    carry only the id.
    """

    # Frozen: the identity is resolved once at discovery and must never be
    # edited mid-pipeline (it also rides frozen dataclasses like ResolveResult).
    model_config = ConfigDict(frozen=True)

    id: str
    # The operation's path template from the spec, e.g. ``/repos/{owner}/{repo}``
    # — persisted flat as the record's ``operation_path`` column/API field.
    path: str | None = None
    method: str | None = None
    # The concrete request path relative to the API's server URL, normalized —
    # e.g. ``/repos/octocat/hello`` for ``https://host/v3/repos/octocat/hello``
    # against server ``https://host/v3``. Binding permission rules are authored
    # against spec paths, so this (never the full upstream path, which carries
    # the server base) is what they are enforced on (#1424). Per-request, not
    # persisted on the record; ``None`` when discovery could not rebuild it or
    # on payloads written before it existed.
    relative_path: str | None = None

    @property
    def display(self) -> str:
        """The human-readable label, e.g. ``GET /repos/{owner}/{repo}``.

        Falls back to the opaque id when the path is missing (legacy in-flight
        job payloads that carry only the flat ``operation_id``) — an
        identity-less label would be useless in an ops signal.
        """
        if not self.path:
            return self.id
        return f"{self.method} {self.path}" if self.method else self.path
