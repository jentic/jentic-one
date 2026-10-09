"""Run the fake OAuth authorization server standalone.

``uv run python -m tests.harness.fake_oauth_as`` — listens on ``HOST``
(default ``127.0.0.1``) and ``PORT`` (default ``8085``); serves https when
``SSL_CERTFILE`` and ``SSL_KEYFILE`` are set (shared-app registrations only
accept https endpoints).
"""

from __future__ import annotations

import os

import uvicorn

from tests.harness.fake_oauth_as.app import build_fake_as_app


def main() -> None:
    uvicorn.run(
        build_fake_as_app(),
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8085")),
        log_level="warning",
        ssl_certfile=os.environ.get("SSL_CERTFILE") or None,
        ssl_keyfile=os.environ.get("SSL_KEYFILE") or None,
    )


if __name__ == "__main__":
    main()
