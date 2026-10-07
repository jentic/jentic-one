/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Dry-run result matching :class:`PermissionTestResult`.
 */
export type PermissionTestResponse = {
    /**
     * Whether the broker would allow this request under the binding's rules.
     */
    allowed: boolean;
    /**
     * The binding whose rule list contributed the matching rule.
     */
    credential_id?: (string | null);
    /**
     * Effect of the matching rule (`allow`/`deny`); null when no match.
     */
    effect?: (string | null);
    /**
     * True when the matching rule was written by the system; null when no match.
     */
    is_system?: (boolean | null);
    /**
     * Whether any rule matched; when false, the outcome is default-deny.
     */
    matched: boolean;
    /**
     * Zero-based index in the binding's ordered rule list; null when no match.
     */
    rule_index?: (number | null);
};

