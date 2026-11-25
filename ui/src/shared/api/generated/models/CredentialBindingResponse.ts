/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AfterExpiry } from './AfterExpiry';
import type { ApiLastUseResponse } from './ApiLastUseResponse';
import type { GrantEndReason } from './GrantEndReason';
import type { ServedApiRef } from './ServedApiRef';
/**
 * Direct agent↔credential binding representation in API responses.
 */
export type CredentialBindingResponse = {
    /**
     * What a call through the binding gets once it has ended. `hold` (the default): the first such call is held as a `grant_renewal` for the agent's owner to renew the binding and run it; later calls get `403 credential_binding_expired` while that review is pending. `refuse`: every call gets `403 credential_binding_expired`, whose directive tells the agent to ask its owner to renew.
     */
    after_expiry?: AfterExpiry;
    /**
     * ID of the agent the credential is bound to.
     */
    agent_id: string;
    /**
     * When the credential was bound (RFC 3339).
     */
    bound_at: string;
    /**
     * ID of the bound credential.
     */
    credential_id: string;
    /**
     * Whether `expires_at` has passed.
     */
    expired?: boolean;
    /**
     * Which limit ended the binding while `expired` is true: `end_date` (`expires_at` passed), `idle` (unused for `max_idle_period`) or `uses` (no uses left). Null while the binding holds.
     */
    expired_reason?: (GrantEndReason | null);
    /**
     * When the binding stops authorizing calls (RFC 3339); null if it never expires.
     */
    expires_at?: (string | null);
    /**
     * Unique ID of the binding.
     */
    id: string;
    /**
     * When the idle limit ends the binding if it is not used before then (RFC 3339); null without an idle limit. Each use moves it later.
     */
    idle_expires_at?: (string | null);
    /**
     * Warn the agent's owner before the binding ends: once a day a job raises a `credential_grant.expiring` event when its end date is within 48 hours (for a binding granted for 4 days or more), and a `credential_grant.idle_expiring` event when it will go idle within 48 hours (for idle limits of 4 days or more); each once per end date or idle period. Off unless set.
     */
    idle_warning?: boolean;
    /**
     * When the agent last made a call through this credential, whatever its outcome (RFC 3339); null if it never has.
     */
    last_used_at?: (string | null);
    /**
     * `last_used_at` per API the agent called through this credential, most recent first; a vendor-wide credential has one entry per API used.
     */
    last_used_by_api?: Array<ApiLastUseResponse>;
    /**
     * Idle limit, as an ISO 8601 duration of weeks, days, hours, minutes or seconds (P1D to P999D, e.g. P14D; no years or months): the binding ends once it has gone this long without a call, and the broker then refuses calls through it with 403 `credential_binding_expired`. Each call restarts the clock (counted at most about hourly), and so does every renewal; a binding that is never used ends one idle period after it was granted. Null when it has no idle limit.
     */
    max_idle_period?: (string | null);
    /**
     * How many calls the binding allows before it ends (1 is single use); each authorized call takes one, whatever the upstream answers, and renewing restores the count. Null for unlimited calls.
     */
    max_quota?: (number | null);
    /**
     * Name of the bound credential; null if it cannot be resolved.
     */
    name?: (string | null);
    /**
     * How many calls the binding still allows; null without a use count.
     */
    quota_remaining?: (number | null);
    /**
     * When the binding was granted or last renewed (RFC 3339); the idle clock restarts at the later of this and its last use.
     */
    renewed_at?: (string | null);
    /**
     * ID of the shared permission rule set attached; null when inline rules apply.
     */
    rule_set_id?: (string | null);
    /**
     * The APIs the bound credential serves; empty if unresolvable.
     */
    serves?: Array<ServedApiRef>;
    /**
     * Whether the binding is suspended, cutting off the agent's access to it.
     */
    suspended: boolean;
    /**
     * Why the binding is suspended: null for a manual suspension, `api_deleted` when the API the credential serves was deleted; cleared on resume.
     */
    suspended_reason?: (string | null);
};

