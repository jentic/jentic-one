"""Shared credential protocols (cross-surface DI seams).

The credentials and their read scoping live with the control surface; other
surfaces reach them only through these protocols, installed by the
composition root (``jentic_one.wiring``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from jentic_one.shared.auth.identity import Identity


class CredentialCreatorLookupProtocol(Protocol):
    """Tell which actor ids created a credential the caller can read.

    Lets the admin actor lookup label the creator of a credential the caller
    can see without importing the control surface. The control surface
    implements it with its credential read scoping, extension-registered
    visibility (e.g. shares) included; the composition root installs it on
    the app as ``app.state.credential_creator_lookup``.
    """

    async def visible_creator_ids(
        self, actor_ids: Sequence[str], *, identity: Identity
    ) -> set[str]:
        """Return the subset of ``actor_ids`` that created a credential ``identity`` can read."""
        ...
