/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AfterExpiry } from './AfterExpiry';
import type { PermissionRuleSchema } from './PermissionRuleSchema';
/**
 * Confirm an OAuth session: the scopes to request and the agent's rules.
 *
 * The default variant — a body without ``kind`` is this one.
 */
export type OAuthConfirmSessionRequest = {
    /**
     * What a call through the binding gets once it has ended. `hold` (the default): the first such call is held as a `grant_renewal` for the agent's owner to renew the binding and run it; later calls get `403 credential_binding_expired` while that review is pending. `refuse`: every call gets `403 credential_binding_expired`, whose directive tells the agent to ask its owner to renew. Applies to the new binding.
     */
    after_expiry?: AfterExpiry;
    /**
     * Agent to bind when the session has none; ignored if the session already has one.
     */
    agent_id?: (string | null);
    /**
     * Upstream scopes the approver confirmed (at most 100).
     */
    confirmed_scopes: Array<string>;
    /**
     * Review digest echoed from the review data; a mismatch is 409 review_stale. Required for an API target.
     */
    digest?: (string | null);
    /**
     * Agent ID from the review the approver saw; a mismatch is 409 review_stale. Required for an API target.
     */
    expected_agent_id?: (string | null);
    /**
     * When the new binding stops authorizing calls (RFC 3339, in the future), as the approver chose it; worked out when the approver confirms. Null binds with no expiry (refused under an org maximum). A body that sets none of `expires_at`, `max_idle_period` and `max_quota` gets the org's default policy. Ignored when the agent is already bound to the credential: that binding keeps its own end date.
     */
    expires_at?: (string | null);
    /**
     * Warn the agent's owner before the binding ends: once a day a job raises a `credential_grant.expiring` event when its end date is within 48 hours (for a binding granted for 4 days or more), and a `credential_grant.idle_expiring` event when it will go idle within 48 hours (for idle limits of 4 days or more); each once per end date or idle period. Off unless set. Applies to the new binding.
     */
    idle_warning?: boolean;
    /**
     * Confirm variant; `oauth` (the default when omitted).
     */
    kind?: string;
    /**
     * The new binding's idle limit, as an ISO 8601 duration (P1D to P999D, e.g. P14D), as the approver chose it: the binding ends once it goes this long without a call. Null or absent sets none. Ignored when the agent is already bound to the credential.
     */
    max_idle_period?: (string | null);
    /**
     * How many calls the new binding allows before it ends (1 is single use). Null or absent sets no count. Ignored when the agent is already bound to the credential.
     */
    max_quota?: (number | null);
    /**
     * Permission rules to bind for the agent (at most 100).
     */
    permission_rules?: Array<PermissionRuleSchema>;
    /**
     * The revision the approver reviewed (`revision.id`); required. Missing is 422 revision_required, a newer one 409 revision_changed.
     */
    revision_id?: (string | null);
};

