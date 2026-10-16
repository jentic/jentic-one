"""A non-production broker verifies a trusted-issuer JWT against a plain-http JWKS (#1723).

The JWKS is served from a real loopback socket (``http.server``), and the token
goes through the validator ``install_broker_auth`` wires onto the broker app —
config validation, ``build_jwt_verifier``, PyJWT's ``PyJWKClient`` fetch and the
claim checks, with nothing stubbed.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from jwt.algorithms import ECAlgorithm

from jentic_one.broker.core.setup import install_broker_auth
from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.config import AppConfig, BrokerConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType

pytestmark = pytest.mark.integration

_ISS = "https://idp.perf.local"
_AUD = "jentic-broker"
_KID = "k1"


class _JwksServer:
    """Serves one JWKS document on 127.0.0.1 over plain http and counts fetches."""

    def __init__(self, jwks: dict[str, Any]) -> None:
        body = json.dumps(jwks).encode()
        self.fetches = 0
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                outer.fetches += 1
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/jwks.json"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> _JwksServer:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture()
def signing_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture()
def jwks_server(signing_key: ec.EllipticCurvePrivateKey) -> Iterator[_JwksServer]:
    public_jwk = json.loads(ECAlgorithm.to_jwk(signing_key.public_key()))
    public_jwk |= {"kid": _KID, "alg": "ES256", "use": "sig"}
    with _JwksServer({"keys": [public_jwk]}) as server:
        yield server


def _broker_config(jwks_url: str) -> BrokerConfig:
    with patch.dict(os.environ, {"JENTIC_ENV": "development"}, clear=False):
        return BrokerConfig.model_validate(
            {
                "jwt_verification": {
                    "audience": _AUD,
                    "trusted_issuers": [{"issuer": _ISS, "jwks_url": jwks_url}],
                }
            }
        )


def _token(key: ec.EllipticCurvePrivateKey, *, iss: str = _ISS) -> str:
    claims = {
        "iss": iss,
        "aud": _AUD,
        "sub": "agnt_http_jwks",
        "exp": int(time.time()) + 300,
        "actor_type": ActorType.AGENT.value,
        "scopes": ["broker:execute"],
    }
    return jwt.encode(claims, key, algorithm="ES256", headers={"kid": _KID})


async def test_broker_verifies_jwt_against_plain_http_jwks_outside_production(
    integration_config: AppConfig,
    jwks_server: _JwksServer,
    signing_key: ec.EllipticCurvePrivateKey,
) -> None:
    config = integration_config.model_copy(update={"broker": _broker_config(jwks_server.url)})
    app = FastAPI()
    async with Context(config) as ctx:
        install_broker_auth(app, ctx)
        validator = app.state.broker_token_validator

        identity = await validator.validate(_token(signing_key))
        assert identity.sub == "agnt_http_jwks"
        assert identity.actor_type is ActorType.AGENT
        assert jwks_server.fetches == 1

        # ``iss`` enforcement is unchanged on the http path: a token from an
        # issuer that isn't trusted is refused before any key fetch.
        with pytest.raises(TokenValidationError, match="jwt_untrusted_issuer"):
            await validator.validate(_token(signing_key, iss="https://evil.local"))
        # A token signed by a key absent from the served JWKS still fails.
        with pytest.raises(TokenValidationError, match="jwt_invalid"):
            await validator.validate(_token(ec.generate_private_key(ec.SECP256R1())))
