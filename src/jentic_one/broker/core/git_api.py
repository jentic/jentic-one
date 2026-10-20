"""The built-in GitHub git API the governed git pipeline runs under.

git smart-HTTP to github.com is not an OpenAPI operation the registry can
discover: the pipeline serves three fixed endpoints on a fixed upstream. It
still needs an API identity, because that is what credentials are scoped to,
what bindings are derived for and what execution records and events name. This
module fixes that identity (vendor ``github-com``, the slug of ``github.com``;
name ``github-git``) and the three operations.

The identity is built in, not a registry row: no registry entry routes these
URLs, so the execute pipeline can never serve a git endpoint under a REST
path rule (it refuses github.com git URLs outright, see
``broker.core.git_protocol.is_github_git_path``). A GitHub credential covers
the git API when its API scope is the vendor only (``github.com``, no API
name) or names this API.

git authenticates to github.com with HTTP Basic, the token as the password of
the user ``x-access-token`` (:func:`git_basic_authorization`); the compare API
the force-push check calls takes the same token as a bearer token.

The identity's axes live in ``shared.builtin_apis``, where the control surface
reads them too: a vendor-registry entry naming ``github.com/github-git``
connects a credential scoped to this API only.

Pure module: no FastAPI, httpx or DB imports.
"""

from __future__ import annotations

import base64

from jentic_one.broker.core.git_protocol import GitEndpoint, GitService, GitTarget
from jentic_one.shared.builtin_apis import (
    GITHUB_GIT_API_NAME,
    GITHUB_GIT_API_VENDOR,
    GITHUB_GIT_API_VERSION,
)
from jentic_one.shared.schemas import APIReference, OperationInfo

#: The git API's identity, as credentials and execution records name it.
GITHUB_GIT_API = APIReference(
    vendor=GITHUB_GIT_API_VENDOR, name=GITHUB_GIT_API_NAME, version=GITHUB_GIT_API_VERSION
)

#: ``GET /{owner}/{repo}.git/info/refs?service=…``: the ref advertisement.
INFO_REFS_OPERATION = OperationInfo(
    id="github-git.info-refs", path="/{owner}/{repo}.git/info/refs", method="GET"
)
#: ``POST /{owner}/{repo}.git/git-upload-pack``: a clone or fetch.
UPLOAD_PACK_OPERATION = OperationInfo(
    id="github-git.upload-pack", path="/{owner}/{repo}.git/git-upload-pack", method="POST"
)
#: ``POST /{owner}/{repo}.git/git-receive-pack``: a push.
RECEIVE_PACK_OPERATION = OperationInfo(
    id="github-git.receive-pack", path="/{owner}/{repo}.git/git-receive-pack", method="POST"
)

#: The user name git sends with a token as its password to github.com.
GIT_TOKEN_USER = "x-access-token"


def operation_for(target: GitTarget) -> OperationInfo:
    """The git API operation a smart-HTTP request runs as."""
    if target.endpoint is GitEndpoint.INFO_REFS:
        return INFO_REFS_OPERATION
    if target.service is GitService.UPLOAD_PACK:
        return UPLOAD_PACK_OPERATION
    return RECEIVE_PACK_OPERATION


def bearer_token(auth_headers: dict[str, str]) -> str | None:
    """The token in an injected ``Authorization: Bearer <token>`` (or ``token``) header.

    ``None`` for any other injection (Basic, an API key in another header,
    SigV4): git to github.com needs a token.
    """
    value = next((v for k, v in auth_headers.items() if k.lower() == "authorization"), None)
    if value is None:
        return None
    scheme, _, token = value.strip().partition(" ")
    token = token.strip()
    if scheme.lower() not in ("bearer", "token") or not token:
        return None
    return token


def git_basic_authorization(token: str) -> str:
    """The ``Authorization`` value git sends to github.com for ``token``."""
    encoded = base64.b64encode(f"{GIT_TOKEN_USER}:{token}".encode()).decode("ascii")
    return f"Basic {encoded}"


__all__ = [
    "GITHUB_GIT_API",
    "GIT_TOKEN_USER",
    "INFO_REFS_OPERATION",
    "RECEIVE_PACK_OPERATION",
    "UPLOAD_PACK_OPERATION",
    "bearer_token",
    "git_basic_authorization",
    "operation_for",
]
