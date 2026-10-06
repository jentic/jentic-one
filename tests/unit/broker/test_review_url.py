"""The held envelope's ``review_url`` — the web UI page a reviewer opens.

The UI is served beside the admin API, so the URL is rooted at the admin
API's public origin when one is configured; the request's own origin is only
the last resort (a combined deployment reached directly).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from starlette.requests import Request

from jentic_one.broker.web.routers.execute import _review_url
from jentic_one.shared.config import load_config
from jentic_one.shared.context import Context


def _ctx(tmp_path: Path, base: dict[str, Any], **sections: dict[str, Any]) -> Context:
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump({**base, **sections}))
    return Context(load_config(path))


def _broker_request() -> Request:
    """A request as it reaches a standalone broker on :8100."""
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "server": ("127.0.0.1", 8100),
            "path": "/api.example.com/v1/charges",
            "root_path": "",
            "query_string": b"",
            "headers": [(b"host", b"127.0.0.1:8100")],
        }
    )


def test_review_url_roots_on_the_admin_api_origin(
    tmp_path: Path, sample_config_dict: dict[str, Any]
) -> None:
    ctx = _ctx(
        tmp_path,
        sample_config_dict,
        broker={"jobs_api_base_url": "http://127.0.0.1:8000/"},
        server={"public_base_url": "https://broker.example.com"},
    )
    assert _review_url(ctx, _broker_request(), "exap_1") == (
        "http://127.0.0.1:8000/app/approvals/exap_1"
    )


def test_review_url_falls_back_to_the_public_base_url(
    tmp_path: Path, sample_config_dict: dict[str, Any]
) -> None:
    ctx = _ctx(
        tmp_path, sample_config_dict, server={"public_base_url": "https://jentic.example.com"}
    )
    assert _review_url(ctx, _broker_request(), "exap_1") == (
        "https://jentic.example.com/app/approvals/exap_1"
    )


def test_review_url_uses_the_request_origin_when_nothing_is_configured(
    tmp_path: Path, sample_config_dict: dict[str, Any]
) -> None:
    ctx = _ctx(tmp_path, sample_config_dict)
    assert _review_url(ctx, _broker_request(), "exap_1") == (
        "http://127.0.0.1:8100/app/approvals/exap_1"
    )


@pytest.mark.parametrize("target", ["start-broker", "start-local"])
def test_local_split_broker_links_to_the_combined_app(target: str) -> None:
    """The Makefile's standalone broker (:8100) roots its absolute links on the
    combined app (``config/local.yaml``'s port), where the admin API and the UI
    live — its own request origin would name the broker."""
    root = Path(__file__).resolve().parents[3]
    makefile = (root / "Makefile").read_text()
    local = load_config(root / "config" / "local.yaml")
    expected = f"JENTIC__BROKER__JOBS_API_BASE_URL=http://127.0.0.1:{local.server.port}"
    assert expected in makefile
    assert "LOCAL_BROKER_ENV :=" in makefile
    recipe = makefile.split(f"\n{target}:", 1)[1].split("\n\n", 1)[0]
    assert "$(LOCAL_BROKER_ENV)" in recipe
