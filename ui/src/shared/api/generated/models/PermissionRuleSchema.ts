/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Permission rule for an agent↔credential binding.
 *
 * Rules are evaluated first-match-wins. If no rule matches, the request is
 * denied (default-deny). A binding with zero rules therefore blocks all
 * operations — users must explicitly add at least one allow rule.
 */
export type PermissionRuleSchema = {
    /**
     * Whether this rule allows the matched request, denies it, or holds it for human approval before the broker executes it.
     */
    effect: PermissionRuleSchema.effect;
    /**
     * How `path` is interpreted: `regex` (full-match), `prefix` (string prefix), or `exact` (equality). Defaults to `regex` for backwards compatibility. `graphql` makes the rule match the top-level fields of a GraphQL request (the GitHub GraphQL endpoint) by operation type and field name, one field at a time; it matches no other request.
     */
    match_mode?: PermissionRuleSchema.match_mode;
    /**
     * HTTP methods to match (case-insensitive). None matches all.
     */
    methods?: (Array<string> | null);
    /**
     * OpenAPI operation IDs to match. None matches all operations.
     */
    operations?: (Array<string> | null);
    /**
     * Path pattern to match, relative to the API's server URL — the spec's path (`/widgets`), never the full upstream path with the server's base path (`/eu/widgets` for a server `https://host/{region}`). Interpreted per `match_mode`: `regex` uses full-match semantics (the pattern must describe the whole path); `prefix` and `exact` are literal. None matches all paths. With `match_mode` `graphql`, `path` is required and is a GraphQL selector instead: `query`, `mutation`, or `mutation:createPullRequest,addComment` (top-level field names, never aliases).
     */
    path?: (string | null);
};
export namespace PermissionRuleSchema {
    /**
     * Whether this rule allows the matched request, denies it, or holds it for human approval before the broker executes it.
     */
    export enum effect {
        ALLOW = 'allow',
        DENY = 'deny',
        REQUIRE_APPROVAL = 'require-approval',
    }
    /**
     * How `path` is interpreted: `regex` (full-match), `prefix` (string prefix), or `exact` (equality). Defaults to `regex` for backwards compatibility. `graphql` makes the rule match the top-level fields of a GraphQL request (the GitHub GraphQL endpoint) by operation type and field name, one field at a time; it matches no other request.
     */
    export enum match_mode {
        REGEX = 'regex',
        PREFIX = 'prefix',
        EXACT = 'exact',
        GRAPHQL = 'graphql',
    }
}

