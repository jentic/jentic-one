"""Problem Details error examples in the generated OpenAPI document.

Every error response gets the canonical per-status example unless the route
declares its own (``conflict(example=...)``), which names its specific ``type``.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from jentic_one.shared.web.openapi_meta import install_openapi_metadata
from jentic_one.shared.web.openapi_responses import PROBLEM_JSON, STATUS_EXAMPLES, conflict

_SPECIFIC = {
    "type": "widget_locked",
    "title": "Conflict",
    "status": 409,
    "detail": "Widget 'w1' is locked",
    "instance": "/widgets/w1",
}


def _spec() -> dict[str, Any]:
    app = FastAPI()

    @app.post("/generic", responses=conflict("Generic conflict."))
    async def generic() -> None:
        return None

    @app.post("/specific", responses=conflict("Widget locked.", example=_SPECIFIC))
    async def specific() -> None:
        return None

    install_openapi_metadata(app)
    return app.openapi()


def _conflict_media(spec: dict[str, Any], path: str) -> dict[str, Any]:
    content: dict[str, Any] = spec["paths"][path]["post"]["responses"]["409"]["content"]
    assert list(content) == [PROBLEM_JSON]
    return dict(content[PROBLEM_JSON])


def test_a_route_without_an_example_gets_the_per_status_one() -> None:
    media = _conflict_media(_spec(), "/generic")
    assert media["example"] == STATUS_EXAMPLES["409"]
    assert media["schema"] == {"$ref": "#/components/schemas/ProblemDetail"}


def test_a_route_declared_example_is_kept() -> None:
    media = _conflict_media(_spec(), "/specific")
    assert media["example"] == _SPECIFIC
    assert media["schema"] == {"$ref": "#/components/schemas/ProblemDetail"}
