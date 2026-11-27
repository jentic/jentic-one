/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AfterExpiry } from './AfterExpiry';
/**
 * Request body for directly binding a credential to an agent.
 */
export type CredentialBindRequest = {
    /**
     * What a call through the binding gets once it has ended. `hold` (the default): the first such call is held as a `grant_renewal` for the agent's owner to renew the binding and run it; later calls get `403 credential_binding_expired` while that review is pending. `refuse`: every call gets `403 credential_binding_expired`, whose directive tells the agent to ask its owner to renew.
     */
    after_expiry?: AfterExpiry;
    /**
     * ID of the credential to bind to the agent.
     */
    credential_id: string;
    /**
     * When the binding stops authorizing calls (RFC 3339, in the future). After this moment the broker refuses the agent's calls through it with 403 `credential_binding_expired`, whatever its token. The binding keeps its permission rules; an owner renews it by setting a later time or null. Null for a binding that never expires (refused under an org maximum). A body that sets none of `expires_at`, `max_idle_period` and `max_quota` gets the org's default policy (`GET /agents/credentials:policy`; none unless the org sets one).
     */
    expires_at?: (string | null);
    /**
     * Warn the agent's owner before the binding ends: once a day a job raises a `credential_grant.expiring` event when its end date is within 48 hours (for a binding granted for 4 days or more), and a `credential_grant.idle_expiring` event when it will go idle within 48 hours (for idle limits of 4 days or more); each once per end date or idle period. Off unless set.
     */
    idle_warning?: boolean;
    /**
     * Idle limit, as an ISO 8601 duration of weeks, days, hours, minutes or seconds (P1D to P999D, e.g. P14D; no years or months): the binding ends once it has gone this long without a call, and the broker then refuses calls through it with 403 `credential_binding_expired`. Each call restarts the clock (counted at most about hourly), and so does every renewal; a binding that is never used ends one idle period after it was granted. Omit for no idle limit.
     */
    max_idle_period?: (string | null);
    /**
     * How many calls the binding allows before it ends (1 is single use); each authorized call takes one, whatever the upstream answers, and renewing restores the count. Omit for unlimited calls.
     */
    max_quota?: (number | null);
};

