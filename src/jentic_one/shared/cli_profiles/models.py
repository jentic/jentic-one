"""The CLI profile manifest: one document per vendor CLI that Jentic governs.

A profile declares everything Jentic needs to run a vendor's CLI through the
broker: the CLI binary, the vendor's APIs and where each one comes from, how
the broker's facade and the container egress proxy route the CLI's calls, the
agent-side environment, the hard-deny lists that apply, and rule presets that
expand into concrete permission rules.

The models are strict (``extra="forbid"``, frozen): a misspelt key in a shipped
manifest fails at load, not at the first connect. Cross-field checks that need
the package's own data (shipped spec files, rule-set references) live in
:mod:`jentic_one.shared.cli_profiles.loader`.

Pure module: no FastAPI, httpx or DB imports.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: The manifest format version this loader reads.
FORMAT_VERSION = 1

_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$")
_PARAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SPEC_FILE_RE = re.compile(r"^specs/[a-z0-9][a-z0-9._-]*\.(?:ya?ml|json)$")
_COMPARATOR = r"(?:>=|<=|>|<|==)\d+(?:\.\d+){0,2}"
_RANGE_RE = re.compile(rf"^{_COMPARATOR}(?:\s*,\s*{_COMPARATOR})*$")
_HARD_DENY_REF_RE = re.compile(r"^jentic_one(?:\.[a-z_][a-z0-9_]*)+:[A-Za-z_][A-Za-z0-9_]*$")
_RULE_SET_REF_RE = re.compile(r"^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$")

#: Placeholders an ``env_set`` value may be, filled in by the CLI side:
#: ``{address}`` the facade address, ``{address_host}`` its hostname,
#: ``{token}`` the command's invocation token and ``{placeholder}`` the egress
#: proxy's fixed non-secret placeholder token.
ENV_PLACEHOLDERS: frozenset[str] = frozenset(
    {"{address}", "{address_host}", "{token}", "{placeholder}"}
)

#: A preset template reference: ``${name}``. Distinct from the ``{name}``
#: OpenAPI path placeholders a REST rule path may carry.
TEMPLATE_RE = re.compile(r"\$\{([a-z][a-z0-9_]*)\}")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApiRole(StrEnum):
    """What one of a profile's APIs is to the vendor's CLI.

    The role decides which rule kind applies to the API's binding: ``rest``
    and ``uploads`` take path rules (``regex``, ``prefix``, ``exact``),
    ``graphql`` takes GraphQL top-level-field rules and ``git`` takes git
    repository/ref rules.
    """

    REST = "rest"
    GRAPHQL = "graphql"
    GIT = "git"
    UPLOADS = "uploads"


class SourceKind(StrEnum):
    """Where an API's definition comes from."""

    #: The public API catalog (``catalog:<catalog api_id>``): imported on demand.
    CATALOG = "catalog"
    #: An OpenAPI document shipped in this package (``shipped:specs/<file>``):
    #: imported and made live on demand.
    SHIPPED = "shipped"
    #: An API the broker serves itself (``builtin:<catalog api_id>``): nothing to import.
    BUILTIN = "builtin"


#: The rule ``match_mode`` values each role accepts.
ROLE_MATCH_MODES: dict[ApiRole, frozenset[str]] = {
    ApiRole.REST: frozenset({"regex", "prefix", "exact"}),
    ApiRole.UPLOADS: frozenset({"regex", "prefix", "exact"}),
    ApiRole.GRAPHQL: frozenset({"graphql"}),
    ApiRole.GIT: frozenset({"git"}),
}


class ApiIdentity(_Strict):
    """The registry identity an API resolves to (slug form, as the registry stores it)."""

    vendor: str = Field(description="Registry vendor slug, e.g. `github-com`.")
    name: str = Field(description="Registry API name slug, e.g. `api-github-com`.")
    version: str | None = Field(
        default=None,
        description=(
            "API version. Required for `shipped` and `builtin` sources; unset for a "
            "`catalog` source, whose version is the imported spec's."
        ),
    )


class ProfileApi(_Strict):
    """One API of the vendor that the CLI reaches through Jentic."""

    role: ApiRole = Field(description="What the API is to the CLI; stable across releases.")
    source: str = Field(
        description=(
            "Where the API comes from: `catalog:<catalog api_id>`, "
            "`shipped:specs/<file>` (a spec in this package) or `builtin:<catalog api_id>`."
        ),
        examples=["catalog:github.com/api.github.com"],
    )
    api_id: str | None = Field(
        default=None,
        description=(
            "The catalog-style api_id (`<domain>/<name>`) a vendor entry names the API "
            "by. Required for a `shipped` source; for `catalog` and `builtin` it is the "
            "source's own id and may be omitted."
        ),
    )
    identity: ApiIdentity = Field(description="The registry identity the API resolves to.")
    connect: str | None = Field(
        default=None,
        description=(
            "The `vendors.entries` key whose connect flow creates this API's credential "
            "(per-API credential mode). Unset when the API is connected another way "
            "(an API-target connect or a hand-entered credential)."
        ),
    )
    description: str = Field(default="", description="What the API covers, for operators.")

    @field_validator("source")
    @classmethod
    def _source_shape(cls, value: str) -> str:
        kind, sep, ref = value.partition(":")
        if not sep or not ref or kind not in {k.value for k in SourceKind}:
            raise ValueError(
                f"source {value!r} must be 'catalog:<api_id>', 'shipped:specs/<file>' "
                "or 'builtin:<api_id>'"
            )
        if kind == SourceKind.SHIPPED and _SPEC_FILE_RE.fullmatch(ref) is None:
            raise ValueError(
                f"shipped source {ref!r} must name a file under specs/ ending in "
                ".yaml, .yml or .json"
            )
        if kind != SourceKind.SHIPPED and ("/" in ref.strip("/").split("/", 1)[0] or " " in ref):
            raise ValueError(f"source {value!r} names an invalid api_id")
        return value

    @model_validator(mode="after")
    def _api_id_matches_source(self) -> ProfileApi:
        if self.source_kind == SourceKind.SHIPPED:
            if not self.api_id:
                raise ValueError(f"a shipped API ({self.source}) needs an api_id")
        elif self.api_id is not None and self.api_id != self.source_ref:
            raise ValueError(
                f"api_id {self.api_id!r} must equal the source's id {self.source_ref!r}"
            )
        if self.source_kind != SourceKind.CATALOG and self.identity.version is None:
            raise ValueError(f"a {self.source_kind} API needs identity.version")
        if self.source_kind == SourceKind.CATALOG and self.identity.version is not None:
            raise ValueError("a catalog API's version is the imported spec's; unset it")
        return self

    @property
    def source_kind(self) -> SourceKind:
        """The kind of :attr:`source`."""
        return SourceKind(self.source.partition(":")[0])

    @property
    def source_ref(self) -> str:
        """What :attr:`source` names after its kind: an api_id or a spec file."""
        return self.source.partition(":")[2]

    @property
    def catalog_api_id(self) -> str:
        """The api_id a vendor entry names this API by."""
        return self.api_id if self.api_id is not None else self.source_ref


class CliBinary(_Strict):
    """The vendor CLI the profile governs."""

    binary: str = Field(description="Executable name looked up on PATH, e.g. `gh`.")
    supported_range: str | None = Field(
        default=None,
        description="CLI versions the profile is written for, e.g. `>=2.40.0,<3`.",
        examples=[">=2.40.0,<3"],
    )

    @field_validator("binary")
    @classmethod
    def _binary_name(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", value):
            raise ValueError(f"binary {value!r} must be a bare executable name")
        return value

    @field_validator("supported_range")
    @classmethod
    def _range_shape(cls, value: str | None) -> str | None:
        if value is not None and _RANGE_RE.fullmatch(value.strip()) is None:
            raise ValueError(
                f"supported_range {value!r} must be comma-separated comparators, e.g. '>=2.40.0,<3'"
            )
        return value


class VendorSpec(_Strict):
    """The vendor whose CLI the profile governs."""

    id: str = Field(
        description=(
            "The vendor's `cli_facades.vendors` key and invocation-token vendor, e.g. `github`."
        )
    )
    display_name: str = Field(description="How the vendor is named to people.")
    registry_domains: tuple[str, ...] = Field(
        min_length=1,
        description=(
            "Registry vendor domains whose APIs this vendor's invocation tokens reach at the "
            "proxy route; errors on a host at or below one take the vendor's error shape."
        ),
    )
    error_shape: Literal["github", "cloudflare"] | None = Field(
        default=None,
        description="The error body shape the vendor's CLI understands; unset keeps RFC 9457.",
    )

    @field_validator("id")
    @classmethod
    def _id_slug(cls, value: str) -> str:
        if _SLUG_RE.fullmatch(value) is None:
            raise ValueError(f"vendor id {value!r} must be a lowercase slug")
        return value

    @field_validator("registry_domains")
    @classmethod
    def _domains(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for domain in value:
            if _DOMAIN_RE.fullmatch(domain) is None:
                raise ValueError(f"{domain!r} is not a lowercase DNS domain")
        return value


class CredentialPolicy(_Strict):
    """How credentials for the profile's APIs are held."""

    mode: Literal["per_api", "profile_scoped"] = Field(
        default="per_api",
        description=(
            "`per_api`: each API has its own credential, created by its own `connect` entry. "
            "`profile_scoped` (reserved, not accepted yet): one credential covering exactly "
            "the profile's APIs, created by the profile's `connect`."
        ),
    )


class ProfileConnect(_Strict):
    """The single connect that covers the whole profile (``profile_scoped`` credentials)."""

    vendor_entry: str = Field(description="The `vendors.entries` key the profile connects with.")
    flow: Literal["device_authorization", "authorization_code"] | None = Field(
        default=None, description="The flow to use; unset takes the entry's first flow."
    )


class FacadeRoute(_Strict):
    """One request layout the vendor facade maps to an upstream."""

    api: ApiRole = Field(description="The role of the API the route's calls resolve to.")
    prefix: str | None = Field(
        default=None,
        description=(
            "A path prefix: `<prefix>/<rest>` maps to `<upstream>/<rest>`. In `path` mode it "
            "is below the facade's own `/_facade/<vendor>`; `/` is that root."
        ),
    )
    path: str | None = Field(
        default=None, description="An exact path that maps to `upstream` itself."
    )
    upstream: str = Field(description="The `https://` upstream the route forwards to.")

    @model_validator(mode="after")
    def _one_matcher(self) -> FacadeRoute:
        if (self.prefix is None) == (self.path is None):
            raise ValueError("a facade route takes exactly one of prefix or path")
        matcher = self.prefix if self.prefix is not None else self.path
        assert matcher is not None
        root = self.prefix == "/"
        if not matcher.startswith("/") or (matcher.endswith("/") and not root) or "//" in matcher:
            raise ValueError(f"{matcher!r} must start with '/' and not end with one")
        if not self.upstream.startswith("https://") or self.upstream.endswith("/"):
            raise ValueError(f"upstream {self.upstream!r} must be https:// with no trailing '/'")
        return self


class FacadeRouting(_Strict):
    """How the broker's facade for the vendor is laid out."""

    mode: Literal["host", "path"] = Field(
        description="`host`: on a hostname of its own; `path`: under `/_facade/<vendor>`."
    )
    routes: tuple[FacadeRoute, ...] = Field(min_length=1, description="Request layouts served.")


class EgressRouting(_Strict):
    """How the egress proxy of ``jentic run --container`` treats the vendor."""

    governed_domains: tuple[str, ...] = Field(
        min_length=1,
        description=(
            "Domains whose hosts the proxy TLS-terminates and relays to the broker with the "
            "vendor's invocation token."
        ),
    )

    @field_validator("governed_domains")
    @classmethod
    def _domains(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for domain in value:
            if _DOMAIN_RE.fullmatch(domain) is None:
                raise ValueError(f"{domain!r} is not a lowercase DNS domain")
        return value


class Routing(_Strict):
    """Facade and proxy routing for the vendor."""

    facade: FacadeRouting
    egress: EgressRouting


def _check_env(env_set: dict[str, str], env_strip: tuple[str, ...]) -> None:
    for name in (*env_set, *env_strip):
        if _ENV_NAME_RE.fullmatch(name) is None:
            raise ValueError(f"{name!r} is not an environment variable name")
    for name, value in env_set.items():
        if ("{" in value or "}" in value) and value not in ENV_PLACEHOLDERS:
            raise ValueError(
                f"{name}={value!r}: a placeholder value must be exactly one of "
                f"{sorted(ENV_PLACEHOLDERS)}"
            )
    overlap = set(env_set) & set(env_strip)
    if overlap:
        raise ValueError(f"set and stripped at once: {sorted(overlap)}")


class WrapperWiring(_Strict):
    """The environment the CLI wrapper (``jentic <cli>``) gives the vendor CLI."""

    env_set: dict[str, str] = Field(
        description=(
            "Variables to set; a value is a literal or one of `{address}`, `{address_host}`, "
            "`{token}`."
        )
    )
    env_strip: tuple[str, ...] = Field(
        default=(),
        description=(
            "Credential variables the CLI could authenticate with instead; removed so only "
            "the invocation token is presented."
        ),
    )

    @model_validator(mode="after")
    def _env(self) -> WrapperWiring:
        _check_env(self.env_set, self.env_strip)
        if "{placeholder}" in self.env_set.values():
            raise ValueError("{placeholder} is for the container egress only")
        if "{token}" not in self.env_set.values():
            raise ValueError("the wrapper must hand the CLI its invocation token ({token})")
        return self


class ContainerWiring(_Strict):
    """The environment ``jentic run --container`` gives the agent for this vendor."""

    env_set: dict[str, str] = Field(
        default_factory=dict,
        description="Variables to set; a value is a literal or `{placeholder}`.",
    )
    env_strip: tuple[str, ...] = Field(
        default=(), description="Variables removed from the agent's environment."
    )
    tunnel_hosts: tuple[str, ...] = Field(
        default=(),
        description=(
            "Hosts the vendor's tools need reached directly (tunnelled, never decrypted); "
            "an exact host or one with `*` for one whole label."
        ),
    )

    @model_validator(mode="after")
    def _env(self) -> ContainerWiring:
        _check_env(self.env_set, self.env_strip)
        if set(self.env_set.values()) & (ENV_PLACEHOLDERS - {"{placeholder}"}):
            raise ValueError("the container egress only fills in {placeholder}")
        return self


class AgentWiring(_Strict):
    """Agent-side wiring of the vendor CLI."""

    wrapper: WrapperWiring | None = Field(
        default=None, description="`jentic <cli>` on the agent's machine; unset: not wrapped."
    )
    container: ContainerWiring | None = Field(
        default=None, description="`jentic run --container`; unset: nothing to set."
    )


class HardDenySource(_Strict):
    """A hard-deny list that applies to one of the profile's APIs.

    The list itself lives in code (``source`` names it); the manifest only
    references it, so there is one source of truth for what no rule can allow.
    """

    api: ApiRole = Field(description="The role of the API the list applies to.")
    kind: Literal["graphql_mutation", "graphql_held_mutation", "git_ref"] = Field(
        description=(
            "`graphql_mutation`: top-level mutations always denied; `graphql_held_mutation`: "
            "mutations always held for approval; `git_ref`: refs no push may touch."
        )
    )
    source: str = Field(
        description="The code that holds the list, as `<module>:<attribute>`.",
        examples=["jentic_one.shared.permissions.github_graphql:HARD_DENIED_MUTATIONS"],
    )
    description: str = Field(default="", description="What the list guards, for operators.")

    @field_validator("source")
    @classmethod
    def _ref(cls, value: str) -> str:
        if _HARD_DENY_REF_RE.fullmatch(value) is None:
            raise ValueError(f"source {value!r} must be 'jentic_one.<module>:<attribute>'")
        return value


class PresetParam(_Strict):
    """A parameter a preset's rules are filled in with."""

    kind: Literal["repo_list", "ref_glob", "flag"] = Field(
        description=(
            "`repo_list`: comma-separated `<owner>/<repo>` globs; `ref_glob`: a branch glob "
            "below `refs/heads/` (e.g. `agent/*`); `flag`: `true` or `false`."
        )
    )
    description: str = Field(description="What the parameter means, shown to the agent.")
    default: str | None = Field(
        default=None, description="Value used when the parameter is not given."
    )


class PresetRule(_Strict):
    """One rule (or rule set) a preset expands into, for one of the profile's APIs.

    Either a rule of its own (``effect`` plus ``methods``/``path``/
    ``match_mode``/``operations``, with ``${param}`` references) or an
    ``include`` of a named rule set kept in code.
    """

    api: ApiRole = Field(description="The role of the API whose binding gets the rule.")
    include: str | None = Field(
        default=None,
        description="A named rule set kept in code, e.g. `github_graphql:pr_write`.",
    )
    effect: Literal["allow", "deny", "require-approval"] | None = None
    methods: tuple[str, ...] | None = None
    path: str | None = None
    match_mode: Literal["regex", "prefix", "exact", "graphql", "git"] | None = None
    operations: tuple[str, ...] | None = None
    for_each: str | None = Field(
        default=None,
        description=(
            "A `repo_list` parameter: the rule is repeated once per listed repository, "
            "with `${<param>}` standing for one repository."
        ),
    )
    when: str | None = Field(
        default=None, description="A `flag` parameter: the rule applies only when it is true."
    )

    @field_validator("include")
    @classmethod
    def _include_ref(cls, value: str | None) -> str | None:
        if value is not None and _RULE_SET_REF_RE.fullmatch(value) is None:
            raise ValueError(f"include {value!r} must be '<set>:<name>'")
        return value

    @model_validator(mode="after")
    def _rule_or_include(self) -> PresetRule:
        own = (self.effect, self.methods, self.path, self.match_mode, self.operations)
        if self.include is not None:
            if any(v is not None for v in own) or self.for_each is not None:
                raise ValueError("an include takes no rule fields and no for_each")
            return self
        if self.effect is None or self.match_mode is None:
            raise ValueError("a preset rule needs effect and match_mode (or an include)")
        if self.match_mode not in ROLE_MATCH_MODES[self.api]:
            raise ValueError(
                f"a {self.api} API's rules use match_mode "
                f"{sorted(ROLE_MATCH_MODES[self.api])}, not {self.match_mode!r}"
            )
        return self

    def references(self) -> set[str]:
        """Every parameter the rule refers to (templates, ``for_each``, ``when``)."""
        names = set(TEMPLATE_RE.findall(self.path or ""))
        if self.for_each is not None:
            names.add(self.for_each)
        if self.when is not None:
            names.add(self.when)
        return names


class Preset(_Strict):
    """A named set of rules across the profile's APIs."""

    description: str = Field(description="What the preset grants, shown to agent and approver.")
    params: dict[str, PresetParam] = Field(default_factory=dict)
    rules: tuple[PresetRule, ...] = Field(min_length=1)

    @field_validator("params")
    @classmethod
    def _param_names(cls, value: dict[str, PresetParam]) -> dict[str, PresetParam]:
        for name in value:
            if _PARAM_NAME_RE.fullmatch(name) is None:
                raise ValueError(f"parameter name {name!r} must be a lowercase identifier")
        return value

    @model_validator(mode="after")
    def _references_declared(self) -> Preset:
        for rule in self.rules:
            for name in rule.references():
                if name not in self.params:
                    raise ValueError(f"a rule refers to undeclared parameter {name!r}")
            if rule.for_each is not None and self.params[rule.for_each].kind != "repo_list":
                raise ValueError(f"for_each {rule.for_each!r} must be a repo_list parameter")
            if rule.when is not None and self.params[rule.when].kind != "flag":
                raise ValueError(f"when {rule.when!r} must be a flag parameter")
        return self


class CliProfile(_Strict):
    """One vendor CLI's manifest."""

    format_version: Literal[1] = Field(description="Manifest format version.")
    profile: str = Field(description="The profile's name, e.g. `github`.")
    display_name: str = Field(description="How the profile is named to people.")
    cli: CliBinary
    vendor: VendorSpec
    credential: CredentialPolicy = Field(default_factory=CredentialPolicy)
    connect: ProfileConnect | None = Field(
        default=None,
        description=(
            "The single connect for the whole profile. Used by `profile_scoped` credentials; "
            "with `per_api`, each API's own `connect` is used."
        ),
    )
    apis: tuple[ProfileApi, ...] = Field(min_length=1)
    routing: Routing
    agent: AgentWiring = Field(default_factory=AgentWiring)
    hard_deny: tuple[HardDenySource, ...] = Field(default=())
    presets: dict[str, Preset] = Field(default_factory=dict)

    @field_validator("profile")
    @classmethod
    def _profile_slug(cls, value: str) -> str:
        if _SLUG_RE.fullmatch(value) is None:
            raise ValueError(f"profile {value!r} must be a lowercase slug")
        return value

    @field_validator("presets")
    @classmethod
    def _preset_names(cls, value: dict[str, Preset]) -> dict[str, Preset]:
        for name in value:
            if _PARAM_NAME_RE.fullmatch(name) is None:
                raise ValueError(f"preset name {name!r} must be a lowercase identifier")
        return value

    @model_validator(mode="after")
    def _cross_checks(self) -> CliProfile:
        roles = [api.role for api in self.apis]
        if len(set(roles)) != len(roles):
            raise ValueError("each API role may appear once in a profile")
        declared = set(roles)
        for route in self.routing.facade.routes:
            if route.api not in declared:
                raise ValueError(f"facade route for undeclared API role {route.api!r}")
        for source in self.hard_deny:
            if source.api not in declared:
                raise ValueError(f"hard_deny for undeclared API role {source.api!r}")
        for name, preset in self.presets.items():
            for rule in preset.rules:
                if rule.api not in declared:
                    raise ValueError(f"preset {name!r} has a rule for undeclared role {rule.api!r}")
        if self.credential.mode == "profile_scoped":
            raise ValueError(
                "credential.mode 'profile_scoped' is reserved: this release keeps one "
                "credential per API (per_api)"
            )
        connects = [api.connect for api in self.apis if api.connect is not None]
        if len(set(connects)) != len(connects):
            raise ValueError("two APIs name the same connect entry; each API has its own")
        return self

    def api(self, role: ApiRole) -> ProfileApi | None:
        """The profile's API with ``role``, or ``None``."""
        return next((a for a in self.apis if a.role == role), None)

    def api_for_connect(self, vendor_entry: str) -> ProfileApi | None:
        """The API whose credential the ``vendor_entry`` connect creates, or ``None``."""
        return next((a for a in self.apis if a.connect == vendor_entry), None)

    def api_for_catalog_api_id(self, api_id: str) -> ProfileApi | None:
        """The API a vendor entry names by ``api_id``, or ``None``."""
        key = api_id.strip().lower()
        return next((a for a in self.apis if a.catalog_api_id.lower() == key), None)

    def api_for_identity(self, vendor: str, name: str) -> ProfileApi | None:
        """The API registered as ``vendor``/``name`` (slug form), or ``None``."""
        return next(
            (a for a in self.apis if (a.identity.vendor, a.identity.name) == (vendor, name)),
            None,
        )


__all__ = [
    "ENV_PLACEHOLDERS",
    "FORMAT_VERSION",
    "ROLE_MATCH_MODES",
    "TEMPLATE_RE",
    "AgentWiring",
    "ApiIdentity",
    "ApiRole",
    "CliBinary",
    "CliProfile",
    "ContainerWiring",
    "CredentialPolicy",
    "EgressRouting",
    "FacadeRoute",
    "FacadeRouting",
    "HardDenySource",
    "Preset",
    "PresetParam",
    "PresetRule",
    "ProfileApi",
    "ProfileConnect",
    "Routing",
    "SourceKind",
    "VendorSpec",
    "WrapperWiring",
]
