"""GitHub GraphQL policy: the endpoint, fields no rule can allow, and rule presets.

Every call whose upstream URL is the GitHub GraphQL endpoint
(:func:`is_github_graphql_url`), whichever registered API or operation it
resolves to, is inspected (``shared.permissions.graphql_document``) and its
binding rules are evaluated per top-level field. On top of the rules:

- :data:`HARD_DENIED_MUTATIONS` are always denied. They create lasting access
  or credentials, or change the protections that guard a repository, so no
  rule (an allow-all included) can allow them.
- :data:`HELD_MUTATIONS` move, create or delete Git refs. Rules cannot yet
  scope them to a repository or branch, so a rule that allows one holds it
  for human approval instead; a rule can still deny it.

The mutation names are those of GitHub's public GraphQL schema
(``https://docs.github.com/public/fpt/schema.docs.graphql``). Deploy keys,
SSH and GPG keys, webhooks, Actions/Dependabot/Codespaces secrets, Codespaces,
repository visibility, repository transfer and the default branch have no
mutation there (``updateRepository`` changes neither visibility nor the
default branch); they are REST-only.

:data:`GITHUB_GRAPHQL_PRESETS` are rule lists in the permission-rule shape the
credentials API accepts. They cover the GraphQL endpoint only.

Pure module: no FastAPI, httpx or DB imports.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from jentic_one.shared.permissions.graphql_rules import GRAPHQL_MATCH_MODE
from jentic_one.shared.url_path import normalize_path

GITHUB_GRAPHQL_HOST = "api.github.com"
GITHUB_GRAPHQL_PATH = "/graphql"

_BRANCH_PROTECTION = "it changes branch protection or rulesets"
_ACCESS = "it grants, changes or removes access to a repository, organization or enterprise"
_CREDENTIALS = "it stores, returns or uses a credential"
_SECURITY_SETTING = "it changes a security setting that guards repositories"
_DEPLOYMENT_PROTECTION = "it changes or bypasses deployment protection"
_TRANSFER = "it transfers an organization"

#: Top-level mutation fields no rule can allow, each with why.
HARD_DENIED_MUTATIONS: Mapping[str, str] = {
    # Branch protection and rulesets.
    "createBranchProtectionRule": _BRANCH_PROTECTION,
    "updateBranchProtectionRule": _BRANCH_PROTECTION,
    "deleteBranchProtectionRule": _BRANCH_PROTECTION,
    "createRepositoryRuleset": _BRANCH_PROTECTION,
    "updateRepositoryRuleset": _BRANCH_PROTECTION,
    "deleteRepositoryRuleset": _BRANCH_PROTECTION,
    "addPullRequestCreationCapBypassUsers": _BRANCH_PROTECTION,
    # Collaborators, teams, invitations and roles.
    "removeOutsideCollaborator": _ACCESS,
    "updateTeamsRepository": _ACCESS,
    "updateProjectV2Collaborators": _ACCESS,
    "inviteEnterpriseAdmin": _ACCESS,
    "inviteEnterpriseMember": _ACCESS,
    "acceptEnterpriseAdministratorInvitation": _ACCESS,
    "acceptEnterpriseMemberInvitation": _ACCESS,
    "addEnterpriseOrganizationMember": _ACCESS,
    "createAttributionInvitation": _ACCESS,
    "grantMigratorRole": _ACCESS,
    "grantEnterpriseOrganizationsMigratorRole": _ACCESS,
    "updateEnterpriseAdministratorRole": _ACCESS,
    "updateEnterpriseOwnerOrganizationRole": _ACCESS,
    "accessUserNamespaceRepository": _ACCESS,
    # Credentials: migrations carry a personal access token; recovery codes
    # and domain tokens are returned.
    "createMigrationSource": _CREDENTIALS,
    "startRepositoryMigration": _CREDENTIALS,
    "startOrganizationMigration": _CREDENTIALS,
    "regenerateEnterpriseIdentityProviderRecoveryCodes": _CREDENTIALS,
    "regenerateVerifiableDomainToken": _CREDENTIALS,
    "setEnterpriseIdentityProvider": _CREDENTIALS,
    "removeEnterpriseIdentityProvider": _CREDENTIALS,
    # Security settings.
    "createIpAllowListEntry": _SECURITY_SETTING,
    "updateIpAllowListEntry": _SECURITY_SETTING,
    "deleteIpAllowListEntry": _SECURITY_SETTING,
    "updateIpAllowListEnabledSetting": _SECURITY_SETTING,
    "updateIpAllowListForInstalledAppsEnabledSetting": _SECURITY_SETTING,
    "updateIpAllowListUserLevelEnforcementEnabledSetting": _SECURITY_SETTING,
    "updateEnterpriseDeployKeySetting": _SECURITY_SETTING,
    "updateEnterpriseMembersCanChangeRepositoryVisibilitySetting": _SECURITY_SETTING,
    "updateEnterpriseMembersCanUpdateProtectedBranchesSetting": _SECURITY_SETTING,
    "updateEnterpriseMembersCanInviteCollaboratorsSetting": _SECURITY_SETTING,
    "updateEnterpriseDefaultRepositoryPermissionSetting": _SECURITY_SETTING,
    "updateEnterpriseAllowPrivateRepositoryForkingSetting": _SECURITY_SETTING,
    "updateOrganizationAllowPrivateRepositoryForkingSetting": _SECURITY_SETTING,
    "updateEnterpriseTwoFactorAuthenticationRequiredSetting": _SECURITY_SETTING,
    "updateEnterpriseTwoFactorAuthenticationDisallowedMethodsSetting": _SECURITY_SETTING,
    # Deployment protection.
    "updateEnvironment": _DEPLOYMENT_PROTECTION,
    "deleteEnvironment": _DEPLOYMENT_PROTECTION,
    "approveDeployments": _DEPLOYMENT_PROTECTION,
    # Transfer.
    "transferEnterpriseOrganization": _TRANSFER,
}

#: Top-level mutation fields that move, create or delete Git refs: held for
#: approval even when a rule allows them, until rules can scope refs.
HELD_MUTATIONS: frozenset[str] = frozenset(
    {
        "createCommitOnBranch",
        "createRef",
        "updateRef",
        "updateRefs",
        "deleteRef",
        "mergeBranch",
        "mergePullRequest",
        "enablePullRequestAutoMerge",
        "enqueuePullRequest",
        "updatePullRequestBranch",
        "createLinkedBranch",
        "revertPullRequest",
    }
)

#: The mutations a pull-request workflow (``gh pr create``, ``gh pr comment``)
#: sends.
PR_WRITE_MUTATIONS: tuple[str, ...] = ("createPullRequest", "addComment", "requestReviews")


def _rule(effect: str, selector: str, comment: str) -> dict[str, Any]:
    return {
        "effect": effect,
        "path": selector,
        "match_mode": GRAPHQL_MATCH_MODE,
        "_comment": comment,
    }


_PR_SELECTOR = f"mutation:{','.join(PR_WRITE_MUTATIONS)}"

#: Rule presets for the GitHub GraphQL endpoint, by name.
#:
#: - ``read_only``: every query allowed, every mutation denied.
#: - ``pr_write``: queries and the pull-request mutations allowed; every other
#:   mutation held for approval.
#: - ``pr_write_with_approval``: queries allowed; the pull-request mutations
#:   held for approval; every other mutation denied.
#:
#: Hard-denied and held mutations stay so under every preset.
GITHUB_GRAPHQL_PRESETS: Mapping[str, tuple[Mapping[str, Any], ...]] = {
    "read_only": (
        _rule("allow", "query", "GitHub GraphQL: every query"),
        _rule("deny", "mutation", "GitHub GraphQL: no mutation"),
    ),
    "pr_write": (
        _rule("allow", "query", "GitHub GraphQL: every query"),
        _rule("allow", _PR_SELECTOR, "GitHub GraphQL: pull-request mutations"),
        _rule("require-approval", "mutation", "GitHub GraphQL: any other mutation, held"),
    ),
    "pr_write_with_approval": (
        _rule("allow", "query", "GitHub GraphQL: every query"),
        _rule("require-approval", _PR_SELECTOR, "GitHub GraphQL: pull-request mutations, held"),
        _rule("deny", "mutation", "GitHub GraphQL: no other mutation"),
    ),
}


def is_github_graphql_url(upstream_url: str) -> bool:
    """Whether ``upstream_url`` is the GitHub GraphQL endpoint, on any scheme, port or query.

    The path is compared after normalization, ignoring case, repeated slashes
    and a trailing slash, so no spelling of the endpoint escapes inspection.
    """
    try:
        hostname = urlsplit(upstream_url).hostname
        path = urlsplit(upstream_url).path
    except ValueError:
        return False
    if hostname is None or hostname.rstrip(".").lower() != GITHUB_GRAPHQL_HOST:
        return False
    normalized = re.sub(r"/+", "/", normalize_path(path or "/")).rstrip("/").lower()
    return normalized == GITHUB_GRAPHQL_PATH


def hard_deny_reason(field: str) -> str | None:
    """Why top-level mutation field ``field`` can never be allowed, or ``None``."""
    return HARD_DENIED_MUTATIONS.get(field)


__all__ = [
    "GITHUB_GRAPHQL_HOST",
    "GITHUB_GRAPHQL_PATH",
    "GITHUB_GRAPHQL_PRESETS",
    "HARD_DENIED_MUTATIONS",
    "HELD_MUTATIONS",
    "PR_WRITE_MUTATIONS",
    "hard_deny_reason",
    "is_github_graphql_url",
]
