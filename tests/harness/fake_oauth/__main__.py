"""Run the fake OAuth server standalone (``uv run python -m tests.harness.fake_oauth``).

``PORT`` (default 8085) and ``HOST`` pick the bind; ``FAKE_OAUTH_PUBLIC_URL``
sets the base URL the served spec and device responses advertise.
"""

from __future__ import annotations

import os

import uvicorn

from tests.harness.fake_oauth.app import build_fake_oauth_app


def main() -> None:
    uvicorn.run(
        build_fake_oauth_app(),
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8085")),
    )


if __name__ == "__main__":
    main()
