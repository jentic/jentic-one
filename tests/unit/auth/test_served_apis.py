"""What a bound credential serves: its stored scope, or a CLI profile's declared APIs."""

from __future__ import annotations

from jentic_one.auth.repos import CredentialRef
from jentic_one.auth.services.agent_service import _served_apis


def _ref(**overrides: object) -> CredentialRef:
    fields: dict[str, object] = {
        "id": "cred_1",
        "name": "GitHub",
        "created_by": "usr_1",
        "active": True,
        "api_vendor": "github-com",
        "api_name": None,
        "api_version": None,
    }
    fields.update(overrides)
    return CredentialRef(**fields)  # type: ignore[arg-type]


def test_a_profile_credential_serves_each_declared_api_with_its_role() -> None:
    served = _served_apis(_ref(cli_profile="github"))
    assert [(s.api_vendor, s.api_name, s.api_version, s.api_role) for s in served] == [
        ("github-com", "api-github-com", None, "rest"),
        ("github-com", "github-graphql", "1", "graphql"),
        ("github-com", "github-git", "1", "git"),
    ]


def test_any_other_credential_serves_its_stored_scope() -> None:
    (served,) = _served_apis(_ref())
    assert (served.api_vendor, served.api_name, served.api_version, served.api_role) == (
        "github-com",
        None,
        None,
        None,
    )


def test_a_profile_this_release_does_not_ship_serves_nothing() -> None:
    assert _served_apis(_ref(cli_profile="gitlab")) == []
