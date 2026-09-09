"""Unit tests for the governed-hosts misdeployment guard (#1278).

A process serving ``GET /governed-hosts`` needs all three databases (admin
bindings → control credential scopes → registry hosts). A context missing a
leg must raise ``GovernedHostsUnavailableError`` (→ 503) before any query
runs — never a bare 500 from the context's access gate, and never an empty
200 a gate would read as "govern nothing".
"""

from __future__ import annotations

from typing import Any

import pytest

from jentic_one.registry.services.errors import GovernedHostsUnavailableError
from jentic_one.registry.services.governed_hosts_service import GovernedHostsService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType

_IDENTITY = Identity(sub="agt_test", actor_type=ActorType.AGENT, permissions=["toolkits:read"])


@pytest.mark.parametrize("missing", ["admin", "control"])
async def test_missing_db_leg_raises_unavailable(
    sample_config_dict: dict[str, Any], missing: str
) -> None:
    allowed = {"registry", "admin", "control"} - {missing}
    ctx = Context(AppConfig.model_validate(sample_config_dict), allowed_dbs=allowed)
    svc = GovernedHostsService(ctx)

    with pytest.raises(GovernedHostsUnavailableError) as exc_info:
        await svc.get_governed_hosts(_IDENTITY)
    assert exc_info.value.db_name == missing
