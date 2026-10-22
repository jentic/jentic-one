"""Run the smoke-upstream harness as a standalone server.

Used for local iteration (``uv run python -m tests.harness.smoke_upstream``) and as
the container entrypoint for the deployed ``smoke-upstream`` image. The HTTP
listen port defaults to 8084 and the TLS listen port (for the ``https://`` OAuth2
token stub, #1779) defaults to 8443 (set ``TLS_PORT=0`` to disable);
``SMOKE_UPSTREAM_PUBLIC_URL`` and ``SMOKE_UPSTREAM_TLS_PUBLIC_URL`` control the
addresses the served live spec advertises (see ``routers.live_spec``).
"""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from tests.harness.smoke_upstream.app import build_smoke_app
from tests.harness.smoke_upstream.routers.live_spec import DEFAULT_TLS_PORT


def write_self_signed_cert(directory: Path) -> tuple[Path, Path]:
    """Write a self-signed certificate and private key for the smoke-upstream TLS listener."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "jentic-smoke-upstream")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.DNSName("jentic-smoke-upstream"),
                    x509.DNSName("jentic-smoke-upstream.default.svc.cluster.local"),
                    x509.DNSName("jentic-smoke-upstream.jentic.svc.cluster.local"),
                    x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "cert.pem"
    key_path = directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


async def _serve_both(http_cfg: uvicorn.Config, https_cfg: uvicorn.Config) -> None:
    await asyncio.gather(
        uvicorn.Server(http_cfg).serve(),
        uvicorn.Server(https_cfg).serve(),
    )


def main() -> None:
    app = build_smoke_app()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8084"))
    tls_port = int(os.environ.get("TLS_PORT", str(DEFAULT_TLS_PORT)))
    if tls_port <= 0:
        uvicorn.run(app, host=host, port=port)
        return

    cert_env = os.environ.get("SSL_CERTFILE")
    key_env = os.environ.get("SSL_KEYFILE")
    with TemporaryDirectory() as tmp_dir:
        if cert_env and key_env:
            cert_path, key_path = Path(cert_env), Path(key_env)
        else:
            cert_path, key_path = write_self_signed_cert(Path(tmp_dir))
        http_cfg = uvicorn.Config(app, host=host, port=port)
        https_cfg = uvicorn.Config(
            app,
            host=host,
            port=tls_port,
            ssl_certfile=str(cert_path),
            ssl_keyfile=str(key_path),
        )
        asyncio.run(_serve_both(http_cfg, https_cfg))


if __name__ == "__main__":
    main()
