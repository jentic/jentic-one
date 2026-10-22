"""CLI profile list and provisioning response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ProfileApiRole = Literal["rest", "graphql", "git", "uploads"]


class CliProfileApiResponse(BaseModel):
    """One API a CLI profile declares, and whether it is live in the registry."""

    role: ProfileApiRole = Field(
        description=(
            "What the API is to the CLI. It decides the rule kind its binding takes: path rules "
            "for `rest`/`uploads`, `graphql` rules for `graphql`, `git` rules for `git`."
        ),
        examples=["rest"],
    )
    source: str = Field(
        description=(
            "Where the API comes from: `catalog:<api_id>` (public catalog), "
            "`shipped:specs/<file>` (a spec shipped with Jentic) or `builtin:<api_id>` "
            "(served by the Broker itself)."
        ),
        examples=["catalog:github.com/api.github.com"],
    )
    api_id: str = Field(
        description="The api_id a vendor entry names the API by.",
        examples=["github.com/api.github.com"],
    )
    vendor: str = Field(description="Registry vendor of the API.", examples=["github-com"])
    name: str = Field(description="Registry API name.", examples=["api-github-com"])
    version: str | None = Field(
        default=None,
        description="API version; null for a catalog API that is not imported yet.",
        examples=["1.1.4"],
    )
    connect: str | None = Field(
        default=None,
        description=(
            "The `jentic connect <vendor>` key that creates this API's credential; null when "
            "it is connected another way."
        ),
        examples=["github"],
    )
    live: bool = Field(description="Whether the registry serves the API now.", examples=[True])


class CliProfilePresetParamResponse(BaseModel):
    """A parameter a preset is filled in with."""

    name: str = Field(
        description="Parameter name, for `--preset-param <name>=…`.", examples=["repo"]
    )
    kind: Literal["repo_list", "ref_glob", "flag"] = Field(
        description=(
            "`repo_list`: comma-separated `<owner>/<repo>` globs; `ref_glob`: a branch glob "
            "below `refs/heads/`; `flag`: `true` or `false`."
        ),
        examples=["repo_list"],
    )
    description: str = Field(
        description="What the parameter means.",
        examples=["Repositories git may fetch, comma-separated '<owner>/<repo>' globs."],
    )
    default: str | None = Field(
        default=None, description="Value used when it is not given.", examples=["false"]
    )


class CliProfilePresetResponse(BaseModel):
    """A named rule preset, expanded into concrete rules at connect time."""

    name: str = Field(
        description="Preset name, for `jentic connect --preset`.", examples=["read_only"]
    )
    description: str = Field(
        description="What the preset grants.",
        examples=["Read everything: REST GETs, every GraphQL query, git fetch."],
    )
    roles: list[ProfileApiRole] = Field(
        description="The API roles the preset has rules for.", examples=[["rest", "graphql"]]
    )
    params: list[CliProfilePresetParamResponse] = Field(description="The preset's parameters.")


class CliProfileHardDenyResponse(BaseModel):
    """A hard-deny list that applies to one of the profile's APIs."""

    role: ProfileApiRole = Field(description="The API the list applies to.", examples=["graphql"])
    kind: Literal["graphql_mutation", "graphql_held_mutation", "git_ref"] = Field(
        description="What the list holds.", examples=["graphql_mutation"]
    )
    description: str = Field(
        description="What the list guards.",
        examples=["Mutations that grant access or change repository protections."],
    )


class CliProfileResponse(BaseModel):
    """One shipped CLI profile."""

    profile: str = Field(description="Profile name.", examples=["github"])
    display_name: str = Field(description="How the profile is named.", examples=["GitHub"])
    vendor: str = Field(
        description="CLI facade vendor (`cli_facades.vendors` key).", examples=["github"]
    )
    vendor_enabled: bool = Field(
        description="Whether the vendor's facade is switched on in this deployment.",
        examples=[True],
    )
    cli: str = Field(description="The vendor CLI binary.", examples=["gh"])
    supported_range: str | None = Field(
        default=None,
        description="CLI versions the profile is written for.",
        examples=[">=2.40.0,<3"],
    )
    credential_mode: Literal["per_api", "profile_scoped"] = Field(
        description=(
            "`per_api`: each API is connected with its own credential. `profile_scoped`: "
            "`jentic connect <connect>` creates one credential covering exactly the "
            "profile's APIs, each binding rule applying to the API of its role."
        ),
        examples=["profile_scoped"],
    )
    connect: str | None = Field(
        default=None,
        description=(
            "With `profile_scoped`: the `jentic connect <vendor>` key whose one connect covers "
            "every API of the profile. Null with `per_api`."
        ),
        examples=["github"],
    )
    apis: list[CliProfileApiResponse] = Field(description="The APIs the profile declares.")
    presets: list[CliProfilePresetResponse] = Field(description="Rule presets.")
    hard_deny: list[CliProfileHardDenyResponse] = Field(
        description="Lists no rule can override, kept in code."
    )


class CliProfileListResponse(BaseModel):
    """The CLI profiles this Jentic ships."""

    data: list[CliProfileResponse] = Field(description="Shipped profiles, by name.")


ProvisionStatusLiteral = Literal[
    "live", "imported", "import_queued", "builtin", "held_for_review", "not_in_catalog", "failed"
]


class CliProfileProvisionedApiResponse(BaseModel):
    """What provisioning did for one API."""

    role: ProfileApiRole = Field(description="The API's role.", examples=["graphql"])
    source: str = Field(
        description="Where the API comes from.",
        examples=["shipped:specs/github-graphql.openapi.yaml"],
    )
    api_id: str = Field(description="The api_id.", examples=["github.com/github-graphql"])
    vendor: str = Field(description="Registry vendor.", examples=["github-com"])
    name: str = Field(description="Registry API name.", examples=["github-graphql"])
    version: str | None = Field(
        default=None, description="API version, when known.", examples=["1"]
    )
    status: ProvisionStatusLiteral = Field(
        description=(
            "`live`: already served, nothing done; `imported`: the shipped spec was imported "
            "and is live; `import_queued`: a catalog import was enqueued (see `job_id`); "
            "`builtin`: served by the Broker, nothing to import; `held_for_review`: imported "
            "as a draft because it changes the API's hosts; `not_in_catalog`: the catalog has "
            "no such entry; `failed`: see `detail`."
        ),
        examples=["imported"],
    )
    detail: str | None = Field(
        default=None, description="Why, for `failed` and the other non-live states."
    )
    job_id: str | None = Field(
        default=None,
        description="The enqueued import job, for `import_queued`.",
        examples=["job_2a…"],
    )


class CliProfileProvisionResponse(BaseModel):
    """The outcome of provisioning a CLI profile."""

    profile: str = Field(description="Profile name.", examples=["github"])
    vendor: str = Field(description="CLI facade vendor.", examples=["github"])
    vendor_enabled: bool = Field(
        description=(
            "Whether the vendor's facade is switched on. Provisioning runs either way; agents "
            "reach the APIs through the vendor's CLI only while it is on."
        ),
        examples=[True],
    )
    apis: list[CliProfileProvisionedApiResponse] = Field(description="One entry per declared API.")
