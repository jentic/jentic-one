"""Protocol & payload edge-case router.

Covers HTTP/transport edge cases the broker must proxy without corruption:
chunked streaming, raw binary, multipart, form-urlencoded, empty bodies, and
large HTML error bodies.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from typing import Final

from fastapi import APIRouter, Request, Response
from starlette.responses import HTMLResponse, StreamingResponse

router = APIRouter(prefix="/edge", tags=["edge"])

# Sized to a round 1 MiB so broker body-cap tests can target the boundary:
# a cap set just below this asserts truncation/502, a cap above asserts
# full relay. Kept a constant so both sides reference one source of truth.
EDGE_CHUNKED_BYTES: Final = 1024 * 1024

# ~5000-char HTML error body, exercising verbatim passthrough of an oversized
# non-JSON error payload alongside the mirrored upstream status.
_HTML_ERROR_TARGET_CHARS: Final = 5000
_HTML_ERROR_STATUS: Final = 502

_CHUNK_SIZE: Final = 64 * 1024
_CHUNK_FILL_BYTE: Final = b"x"


def _build_html_error() -> str:
    filler = "A" * _HTML_ERROR_TARGET_CHARS
    return f"<html><body><h1>Upstream Error</h1><p>{filler}</p></body></html>"


_HTML_ERROR_BODY: Final = _build_html_error()


async def _chunked_stream() -> AsyncIterator[bytes]:
    remaining = EDGE_CHUNKED_BYTES
    while remaining > 0:
        size = min(_CHUNK_SIZE, remaining)
        yield _CHUNK_FILL_BYTE * size
        remaining -= size


@router.post("/chunked")
async def edge_chunked() -> StreamingResponse:
    return StreamingResponse(_chunked_stream(), media_type="text/plain")


@router.post("/binary")
async def edge_binary(request: Request) -> dict[str, object]:
    raw = await request.body()
    return {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


@router.post("/multipart")
async def edge_multipart(request: Request) -> dict[str, object]:
    form = await request.form()
    fields: dict[str, str] = {}
    files: list[dict[str, object]] = []
    for key, value in form.multi_items():
        if isinstance(value, str):
            fields[key] = value
        else:
            contents = await value.read()
            # sha256 (like /edge/binary) so a length-preserving corruption
            # of a part fails the round-trip assertion, not just a resize.
            files.append(
                {
                    "field": key,
                    "filename": value.filename,
                    "size": len(contents),
                    "sha256": hashlib.sha256(contents).hexdigest(),
                }
            )
    return {"fields": fields, "files": files}


@router.post("/form-urlencoded")
async def edge_form_urlencoded(request: Request) -> dict[str, object]:
    form = await request.form()
    parsed = {key: value for key, value in form.multi_items() if isinstance(value, str)}
    return {"form": parsed}


@router.get("/empty-204")
async def edge_empty_204() -> Response:
    return Response(status_code=204)


@router.get("/html-error")
async def edge_html_error() -> HTMLResponse:
    return HTMLResponse(content=_HTML_ERROR_BODY, status_code=_HTML_ERROR_STATUS)


__all__ = ["EDGE_CHUNKED_BYTES", "router"]
