"""An exception no handler claims still answers RFC 9457 Problem Details."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.web.app_factory import create_combined_app


def test_unclaimed_exception_answers_generic_problem_details(
    sample_config_dict: dict[str, Any],
) -> None:
    app = create_combined_app(Context(AppConfig.model_validate(sample_config_dict)), ["control"])

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    resp = TestClient(app, raise_server_exceptions=False).get("/boom")

    assert resp.status_code == 500
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json() == {
        "type": "server_error",
        "title": "Internal Server Error",
        "status": 500,
        "detail": "An unexpected error occurred.",
        "instance": "/boom",
    }
    assert "secret internal detail" not in resp.text
