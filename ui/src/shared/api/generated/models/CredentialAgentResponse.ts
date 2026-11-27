/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AfterExpiry } from './AfterExpiry';
import type { GrantEndReason } from './GrantEndReason';
/**
 * Agent directly bound to a credential.
 */
export type CredentialAgentResponse = {
    /**
     * What a call through the binding gets once it has ended: `hold` holds the first call for the owner to renew; `refuse` answers `403 credential_binding_expired`.
     */
    after_expiry?: AfterExpiry;
    /**
     * ID of the bound agent.
     */
    agent_id: string;
    /**
     * Name of the bound agent.
     */
    agent_name: string;
    /**
     * When the agent was bound to the credential (RFC 3339).
     */
    bound_at: string;
    /**
     * Whether `expires_at` has passed: the agent cannot execute through this credential until the binding is renewed.
     */
    expired?: boolean;
    /**
     * Which limit ended the binding while `expired` is true: `end_date` (`expires_at` passed), `idle` (unused for `max_idle_period`) or `uses` (no uses left). Null while the binding holds.
     */
    expired_reason?: (GrantEndReason | null);
    /**
     * When the binding stops authorizing calls (RFC 3339); null if it never expires. Independent of `suspended`.
     */
    expires_at?: (string | null);
    /**
     * When the idle limit ends the binding if it is not used before then (RFC 3339); null without an idle limit.
     */
    idle_expires_at?: (string | null);
    /**
     * Whether the agent's owner is warned before the binding ends (its end date or idle limit).
     */
    idle_warning?: boolean;
    /**
     * When the agent last made a call through this credential, whatever its outcome (RFC 3339); null if it never has.
     */
    last_used_at?: (string | null);
    /**
     * Idle limit (an ISO 8601 duration, e.g. P14D): the binding ends once it goes this long without a call. Null when it has none.
     */
    max_idle_period?: (string | null);
    /**
     * How many calls the binding allows before it ends; null for unlimited.
     */
    max_quota?: (number | null);
    /**
     * ID of the user who owns the bound agent; null for an unclaimed self-registration. Changing the binding (suspending, resuming or renewing it) takes the agent's owner or an `org:admin`, on top of the credential's own checks.
     */
    owner_id?: (string | null);
    /**
     * How many calls it still allows; null without a use count.
     */
    quota_remaining?: (number | null);
    /**
     * Shared permission rule set this binding points at, if any. While attached, the set's ordered list is the binding's effective policy; null means the binding's inline rules apply.
     */
    rule_set_id?: (string | null);
    /**
     * Status of the bound agent.
     */
    status: string;
    /**
     * True when the binding is soft-suspended (reversible cut-off): the binding and its rules survive, but the agent cannot execute through this credential until resumed.
     */
    suspended: boolean;
    /**
     * Why the binding is suspended: `api_deleted` when the API the credential serves was deleted; null for an active binding or a manual suspension. Cleared on resume.
     */
    suspended_reason?: (string | null);
};

