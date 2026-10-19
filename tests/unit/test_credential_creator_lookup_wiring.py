"""Composition-root wiring for the actor lookup's credential-creator relation.

``GET /actors/lookup`` labels the creator of a credential the caller can read.
The credentials live with control, so the composition root installs the
control ``CredentialService`` as ``app.state.credential_creator_lookup`` on
every app shape that serves admin and can read the control DB, and nowhere
else.
"""

from __future__ import annotations

from typing import Any

from jentic_one.__main__ import _build_app, _expand_allowed_dbs
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.wiring import build_default_container, install_credential_creator_lookup


def _ctx(sample_config_dict: dict[str, Any], apps: list[str]) -> Context:
    config = AppConfig.model_validate({**sample_config_dict, "apps": apps})
    return Context(config, allowed_dbs=_expand_allowed_dbs(apps, config))


def test_admin_with_control_carries_the_lookup(sample_config_dict: dict[str, Any]) -> None:
    container = build_default_container(_ctx(sample_config_dict, ["control", "admin"]))
    assert install_credential_creator_lookup in container.extra_installers


def test_shapes_without_admin_or_control_db_skip_the_lookup(
    sample_config_dict: dict[str, Any],
) -> None:
    for apps in (["control"], ["admin"], ["broker"]):
        container = build_default_container(_ctx(sample_config_dict, apps))
        assert install_credential_creator_lookup not in container.extra_installers, apps


def test_combined_app_installs_the_credential_service(sample_config_dict: dict[str, Any]) -> None:
    apps = ["control", "admin", "auth", "registry"]
    app = _build_app(_ctx(sample_config_dict, apps), apps)
    assert isinstance(getattr(app.state, "credential_creator_lookup", None), CredentialService)


def test_standalone_admin_app_has_no_lookup(sample_config_dict: dict[str, Any]) -> None:
    app = _build_app(_ctx(sample_config_dict, ["admin"]), ["admin"])
    assert getattr(app.state, "credential_creator_lookup", None) is None
