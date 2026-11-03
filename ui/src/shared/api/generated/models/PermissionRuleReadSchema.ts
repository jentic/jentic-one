/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Permission rule response (includes system fields).
 */
export type PermissionRuleReadSchema = {
    /**
     * Optional note attached to the rule.
     */
    _comment?: (string | null);
    /**
     * True when the rule is platform-managed rather than user-authored.
     */
    _system?: boolean;
    /**
     * Whether this rule allows, denies, or holds the matched request for human approval (`require-approval`).
     */
    effect: PermissionRuleReadSchema.effect;
    /**
     * ID of the rule (`apr_…` on a binding, `prr_…` in a rule set), so a connect-session edit can name it in `remove` or `before`.
     */
    id?: (string | null);
    /**
     * How `path` is interpreted: `regex` (full-match), `prefix` or `exact`.
     */
    match_mode?: PermissionRuleReadSchema.match_mode;
    /**
     * HTTP methods the rule matches; null matches all.
     */
    methods?: (Array<string> | null);
    /**
     * OpenAPI operation IDs the rule matches; null matches all.
     */
    operations?: (Array<string> | null);
    /**
     * Path pattern relative to the API's server URL, interpreted per `match_mode`; null matches all paths.
     */
    path?: (string | null);
};
export namespace PermissionRuleReadSchema {
    /**
     * Whether this rule allows, denies, or holds the matched request for human approval (`require-approval`).
     */
    export enum effect {
        ALLOW = 'allow',
        DENY = 'deny',
        REQUIRE_APPROVAL = 'require-approval',
    }
    /**
     * How `path` is interpreted: `regex` (full-match), `prefix` or `exact`.
     */
    export enum match_mode {
        REGEX = 'regex',
        PREFIX = 'prefix',
        EXACT = 'exact',
    }
}

