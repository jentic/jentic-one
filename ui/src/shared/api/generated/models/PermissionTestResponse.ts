/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Dry-run result matching :class:`PermissionTestResult`.
 */
export type PermissionTestResponse = {
    /**
     * Whether the broker would allow this request under the pooled rules.
     */
    allowed: boolean;
    /**
     * Which binding contributed the matching rule — vendor pooling means this may not equal the credential in the request URL.
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
     * Zero-based index in the vendor-pooled rule list; null when no match.
     */
    rule_index?: (number | null);
};

