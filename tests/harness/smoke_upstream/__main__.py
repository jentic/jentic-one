"""Run the smoke-upstream harness as a standalone server.

Used for local iteration (``uv run python -m tests.harness.smoke_upstream``) and as
the container entrypoint for the deployed ``smoke-upstream`` image. The
listen port defaults to 8084; ``SMOKE_UPSTREAM_PUBLIC_URL`` controls the address the
served live spec advertises (see ``routers.live_spec``).
"""

from __future__ import annotations

import os

import uvicorn

from tests.harness.smoke_upstream.app import build_smoke_app


def main() -> None:
    uvicorn.run(
        build_smoke_app(),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8084")),
    )


if __name__ == "__main__":
    main()
