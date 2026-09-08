/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Permission rule for an access request item.
 */
export type jentic_one__control__web__schemas__access_requests__PermissionRuleSchema = {
    effect: jentic_one__control__web__schemas__access_requests__PermissionRuleSchema.effect;
    /**
     * How `path` is interpreted: `regex` (full-match), `prefix` (string prefix), or `exact` (equality). Defaults to `regex` for backwards compatibility.
     */
    match_mode?: jentic_one__control__web__schemas__access_requests__PermissionRuleSchema.match_mode;
    /**
     * HTTP methods to match (case-insensitive). None matches all.
     */
    methods?: (Array<string> | null);
    /**
     * OpenAPI operation IDs to match. None matches all operations.
     */
    operations?: (Array<string> | null);
    /**
     * Path pattern to match. Interpreted per `match_mode`: `regex` uses full-match semantics (the pattern must describe the whole path); `prefix` and `exact` are literal. None matches all paths.
     */
    path?: (string | null);
};
export namespace jentic_one__control__web__schemas__access_requests__PermissionRuleSchema {
    export enum effect {
        ALLOW = 'allow',
        DENY = 'deny',
        REQUIRE_APPROVAL = 'require-approval',
    }
    /**
     * How `path` is interpreted: `regex` (full-match), `prefix` (string prefix), or `exact` (equality). Defaults to `regex` for backwards compatibility.
     */
    export enum match_mode {
        REGEX = 'regex',
        PREFIX = 'prefix',
        EXACT = 'exact',
    }
}

