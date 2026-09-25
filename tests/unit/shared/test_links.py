"""Tests for the request-scoped public-URL helpers in ``shared.web.links``."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.requests import Request

from jentic_one.shared.config import AppConfig
from jentic_one.shared.web.links import deployment_base_url, public_base_url


def _config(server: dict[str, Any] | None = None, auth: dict[str, Any] | None = None) -> AppConfig:
    return AppConfig.model_validate(
        {
            "databases": {
                "registry": {"name": "reg"},
                "admin": {"name": "admin"},
                "control": {"name": "ctrl"},
            },
            "server": server or {},
            "auth": auth or {},
        }
    )


def _request(host: str, scheme: str = "http", root_path: str = "") -> Request:
    return Request(
        {
            "type": "http",
            "scheme": scheme,
            "method": "GET",
            "path": "/",
            "root_path": root_path,
            "query_string": b"",
            "headers": [(b"host", host.encode())],
            "server": (host.split(":")[0], 0),
        }
    )


@pytest.mark.parametrize("alias", ["localhost:8020", "127.0.0.1:8020", "[::1]:8020"])
def test_deployment_base_url_folds_direct_loopback_hits_onto_bind(alias: str) -> None:
    # Discovery issuer / token_endpoint must equal the request-less JWT-Bearer
    # audience base whichever loopback alias the client used.
    config = _config(server={"host": "127.0.0.1", "port": 8020})
    assert deployment_base_url(config, _request(alias)) == "http://127.0.0.1:8020"


def test_deployment_base_url_keeps_mapped_or_proxied_origin() -> None:
    config = _config(server={"host": "0.0.0.0", "port": 8000})
    # A port mapping onto a different loopback port, a gateway path prefix and
    # a non-loopback host all keep the request's own origin.
    assert deployment_base_url(config, _request("localhost:30080")) == "http://localhost:30080"
    assert (
        deployment_base_url(config, _request("127.0.0.1:8000", root_path="/gw"))
        == "http://127.0.0.1:8000/gw"
    )
    assert deployment_base_url(config, _request("jentic.lan:8000")) == "http://jentic.lan:8000"


def test_deployment_base_url_config_wins() -> None:
    config = _config(server={"public_base_url": "https://jentic.example.com"})
    assert deployment_base_url(config, _request("localhost:8000")) == "https://jentic.example.com"
    config = _config(auth={"canonical_base_url": "https://auth.example.com"})
    assert deployment_base_url(config, _request("localhost:8000")) == "https://auth.example.com"


def test_public_base_url_keeps_exact_request_origin() -> None:
    # The connect callback must land on the SPA's own origin (same-origin popup
    # postMessage), so no loopback folding here.
    config = _config(server={"host": "127.0.0.1", "port": 8020})
    assert public_base_url(config, _request("localhost:8020")) == "http://localhost:8020"
