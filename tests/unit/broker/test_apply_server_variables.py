"""Unit tests for the shared apply_server_variables() utility."""

from jentic_one.shared.url import (
    apply_server_variables,
    decode_server_variable_placeholders,
    has_host_server_variable,
    server_variables_compatible,
)


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


def test_defaults_fill_placeholders_without_a_credential_value() -> None:
    url = "https://api.example.com/{region}/{tier}/widgets"
    result = apply_server_variables(url, {"tier": "gold"}, {"region": "us", "tier": "free"})
    assert result == "https://api.example.com/us/gold/widgets"


def test_substitutes_percent_encoded_placeholder() -> None:
    url = "https://api.example.com/%7Bregion%7D/widgets"
    assert apply_server_variables(url, {"region": "eu"}) == "https://api.example.com/eu/widgets"


def test_never_rewrites_a_concrete_url() -> None:
    url = "https://api.example.com/eu/widgets"
    assert apply_server_variables(url, {"region": "us"}, {"region": "us"}) == url


def test_backslash_value_is_encoded_literally() -> None:
    url = "https://api.example.com/{region}"
    assert apply_server_variables(url, {"region": "a\\1"}) == "https://api.example.com/a%5C1"


def test_server_variables_compatible_matching_scope() -> None:
    assert server_variables_compatible({"region": "us"}, {"region": "us"})


def test_server_variables_compatible_compares_exactly() -> None:
    # Path server variables may be case-sensitive upstream.
    assert not server_variables_compatible({"tenant": "Acme"}, {"tenant": "acme"})


def test_server_variables_compatible_fails_closed_when_unresolved() -> None:
    assert not server_variables_compatible({"region": "us"}, None, unresolved=True)
    assert not server_variables_compatible({"region": "us"}, {}, unresolved=True)
    # An unscoped credential is unaffected.
    assert server_variables_compatible(None, None, unresolved=True)


def test_server_variables_compatible_mismatch_is_no_match() -> None:
    assert not server_variables_compatible({"region": "us"}, {"region": "eu"})


def test_server_variables_compatible_unconstrained_cases() -> None:
    # Unscoped credential, URL without resolved values, or a variable the URL
    # does not resolve: no constraint.
    assert server_variables_compatible(None, {"region": "eu"})
    assert server_variables_compatible({}, {"region": "eu"})
    assert server_variables_compatible({"region": "us"}, None)
    assert server_variables_compatible({"region": "us"}, {"tier": "gold"})


def test_has_host_server_variable_detects_percent_encoded_placeholder() -> None:
    """An encoded-brace host is still a templated host (#2089).

    Many HTTP clients percent-encode ``{`` and ``}`` on their own, so the same
    templated host arrives both spellings. ``apply_server_variables`` already
    substitutes either (``test_substitutes_percent_encoded_placeholder``), so
    treating only the literal form as templated splits one host into two
    behaviours.
    """
    url = "https://bedrock-runtime.%7Bregion%7D.amazonaws.com/model/x/converse"
    assert has_host_server_variable(url) is True


def test_has_host_server_variable_detects_encoded_placeholder_case_insensitively() -> None:
    """``%7b``/``%7d`` are as valid as ``%7B``/``%7D`` (RFC 3986 §2.1)."""
    assert has_host_server_variable("https://%7bregion%7d.posthog.com/api") is True


def test_decode_server_variable_placeholders_decodes_only_braces() -> None:
    """Brace escapes fold; every other escape is left for the forwarder."""
    assert (
        decode_server_variable_placeholders("bedrock-runtime.%7Bregion%7D.amazonaws.com")
        == "bedrock-runtime.{region}.amazonaws.com"
    )
    # Either case, and a half-encoded spelling, name the same variable.
    assert decode_server_variable_placeholders("%7bregion%7d.posthog.com") == "{region}.posthog.com"
    assert decode_server_variable_placeholders("%7Bregion}.posthog.com") == "{region}.posthog.com"
    # A literal host, and a literal template, are returned unchanged.
    assert decode_server_variable_placeholders("api.posthog.com") == "api.posthog.com"
    assert decode_server_variable_placeholders("{region}.posthog.com") == "{region}.posthog.com"
    # An escape that could spell a real DNS label is never folded: routing on a
    # decoded host the broker does not forward would inject a credential for an
    # operation the request never reaches.
    assert decode_server_variable_placeholders("%41pi.example.com") == "%41pi.example.com"
    # An empty name is not a placeholder.
    assert decode_server_variable_placeholders("%7B%7D.example.com") == "%7B%7D.example.com"
