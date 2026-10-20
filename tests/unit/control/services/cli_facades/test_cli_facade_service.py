"""``CliFacadeService.list_served``: which facades are advertised, and at which address."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from jentic_one.control.services.cli_facades import CliFacadeService, CliFacadeView
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context

_DATABASES = {"registry": {"name": "reg"}, "admin": {"name": "admin"}, "control": {"name": "ctrl"}}


def _service(**overrides: Any) -> CliFacadeService:
    data: dict[str, Any] = {
        "databases": _DATABASES,
        "cli_facades": {"enabled": True, "vendors": {"cloudflare": {"enabled": True}}},
        "server": {"mcp": {"broker_url": "https://broker.example.com/"}},
    }
    data.update(overrides)
    ctx = cast(Context, SimpleNamespace(config=AppConfig.model_validate(data)))
    return CliFacadeService(ctx)


def test_path_mode_address_is_the_advertised_broker_url_plus_the_facade_prefix() -> None:
    assert _service().list_served() == [
        CliFacadeView(
            vendor="cloudflare",
            cli="cf",
            mode="path",
            address="https://broker.example.com/_facade/cloudflare",
            protocols=("rest",),
        )
    ]


def test_public_address_wins_over_the_broker_url() -> None:
    svc = _service(
        cli_facades={
            "enabled": True,
            "vendors": {
                "cloudflare": {"enabled": True, "public_address": "https://cf.example.com/x/"}
            },
        }
    )
    (view,) = svc.list_served()
    assert view.address == "https://cf.example.com/x"


@pytest.mark.parametrize(
    "cli_facades",
    [
        {"enabled": False, "vendors": {"cloudflare": {"enabled": True}}},
        {"enabled": True, "vendors": {"cloudflare": {"enabled": False}}},
        {"enabled": True},
    ],
    ids=["master-off", "vendor-off", "default"],
)
def test_a_switched_off_facade_is_not_listed(cli_facades: dict[str, Any]) -> None:
    assert _service(cli_facades=cli_facades).list_served() == []


def test_an_enabled_vendor_the_broker_does_not_serve_is_not_listed() -> None:
    svc = _service(cli_facades={"enabled": True, "vendors": {"aws": {"enabled": True}}})
    assert svc.list_served() == []


def test_host_mode_address_is_https_on_the_facade_host() -> None:
    svc = _service(
        cli_facades={
            "enabled": True,
            "vendors": {"github": {"enabled": True, "host": "github.jentic.example.com"}},
        }
    )
    assert svc.list_served() == [
        CliFacadeView(
            vendor="github",
            cli="gh",
            mode="host",
            address="https://github.jentic.example.com",
            protocols=("rest", "graphql", "git"),
        )
    ]


def test_git_is_listed_only_while_the_git_pipeline_is_on() -> None:
    svc = _service(
        cli_facades={
            "enabled": True,
            "vendors": {"github": {"enabled": True, "host": "github.jentic.example.com"}},
            "git": {"enabled": False},
        }
    )
    (view,) = svc.list_served()
    assert view.protocols == ("rest", "graphql")


def test_remote_backend_with_a_loopback_broker_url_has_no_address() -> None:
    svc = _service(server={"backend": "remote", "mcp": {"broker_url": "http://127.0.0.1:8100"}})
    (view,) = svc.list_served()
    assert view.address is None


def test_local_backend_advertises_the_loopback_broker_url() -> None:
    svc = _service(server={"mcp": {"broker_url": "http://127.0.0.1:8100"}})
    (view,) = svc.list_served()
    assert view.address == "http://127.0.0.1:8100/_facade/cloudflare"
