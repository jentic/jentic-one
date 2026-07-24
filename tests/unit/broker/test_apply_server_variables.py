"""Unit tests for the shared apply_server_variables() utility."""

from jentic_one.shared.url import apply_server_variables, has_host_server_variable


def test_single_variable_substitution() -> None:
    url = "https://{your-domain}.atlassian.net/rest/api/3"
    result = apply_server_variables(url, {"your-domain": "acme"})
    assert result == "https://acme.atlassian.net/rest/api/3"


def test_multiple_variables_in_one_url() -> None:
    url = "https://{region}.{domain}.example.com/{version}"
    variables = {"region": "us", "domain": "acme", "version": "v2"}
    result = apply_server_variables(url, variables)
    assert result == "https://us.acme.example.com/v2"


def test_url_encodes_special_characters() -> None:
    url = "https://{tenant}.example.com/{path}"
    variables = {"tenant": "my company", "path": "foo/bar"}
    result = apply_server_variables(url, variables)
    assert result == "https://my%20company.example.com/foo%2Fbar"


def test_returns_url_unchanged_when_variables_empty() -> None:
    url = "https://{your-domain}.atlassian.net/rest/api/3"
    result = apply_server_variables(url, {})
    assert result == url


def test_leaves_unmatched_placeholders_intact() -> None:
    url = "https://{region}.example.com/{path_param}/items"
    variables = {"region": "eu"}
    result = apply_server_variables(url, variables)
    assert result == "https://eu.example.com/{path_param}/items"


def test_handles_repeated_placeholder() -> None:
    url = "https://{host}.{host}.example.com"
    result = apply_server_variables(url, {"host": "api"})
    assert result == "https://api.api.example.com"


def test_has_host_server_variable_detects_templated_host() -> None:
    assert has_host_server_variable("https://{region}.posthog.com/api/projects") is True


def test_has_host_server_variable_detects_templated_subdomain() -> None:
    assert has_host_server_variable("https://{your-domain}.atlassian.net/rest/api/3") is True


def test_has_host_server_variable_false_for_static_host() -> None:
    assert has_host_server_variable("https://api.posthog.com/api/projects") is False


def test_has_host_server_variable_ignores_path_parameters() -> None:
    # A path parameter is not a server variable — it must not trigger the hint.
    assert has_host_server_variable("https://api.example.com/users/{id}/items") is False


def test_has_host_server_variable_ignores_query_placeholders() -> None:
    assert has_host_server_variable("https://api.example.com/search?q={term}") is False


def test_has_host_server_variable_ignores_empty_braces() -> None:
    assert has_host_server_variable("https://api.example.com/{}/x") is False
