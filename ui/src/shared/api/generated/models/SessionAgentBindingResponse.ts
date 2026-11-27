/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The session agent's own binding to a credential it already holds.
 *
 * Binding that credential again creates nothing: the binding keeps these
 * limits, and the limits chosen on the review do not apply to it.
 */
export type SessionAgentBindingResponse = {
    /**
     * Whether one of its limits has been reached.
     */
    expired: boolean;
    /**
     * When the binding stops authorizing calls (RFC 3339); null never expires.
     */
    expires_at?: (string | null);
    /**
     * Its idle limit as an ISO 8601 duration; null without one.
     */
    max_idle_period?: (string | null);
    /**
     * Its use count (1 is single use); null without one.
     */
    max_quota?: (number | null);
    /**
     * The uses it has left; null without a use count.
     */
    quota_remaining?: (number | null);
    /**
     * Whether the binding is suspended.
     */
    suspended: boolean;
};

