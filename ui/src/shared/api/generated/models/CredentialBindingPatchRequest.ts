/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AfterExpiry } from './AfterExpiry';
/**
 * Request body for renewing a credential binding: re-certify it, and change its limits.
 *
 * Every renewal restarts the binding's idle clock. Each limit sent is set, and
 * null clears it; an absent field is never read as clearing. An end date left
 * out runs the binding again for as long as it was granted for, capped at the
 * org's maximum; any other limit left out stays as it is. An empty body
 * re-certifies ("still needed").
 */
export type CredentialBindingPatchRequest = {
    /**
     * What a call through the binding gets once it has ended. `hold` (the default): the first such call is held as a `grant_renewal` for the agent's owner to renew the binding and run it; later calls get `403 credential_binding_expired` while that review is pending. `refuse`: every call gets `403 credential_binding_expired`, whose directive tells the agent to ask its owner to renew. Leave it out to keep it as it is.
     */
    after_expiry?: (AfterExpiry | null);
    /**
     * When the binding stops authorizing calls (RFC 3339, in the future). After this moment the broker refuses the agent's calls through it with 403 `credential_binding_expired`, whatever its token. The binding keeps its permission rules; an owner renews it by setting a later time or null. Send null to clear the end date. Left out, the binding runs again for as long as it was granted for (its end date less when it was granted or last renewed), capped at the org's maximum; one with no end date takes the maximum, or keeps none without one.
     */
    expires_at?: (string | null);
    /**
     * Warn the agent's owner before the binding ends: once a day a job raises a `credential_grant.expiring` event when its end date is within 48 hours (for a binding granted for 4 days or more), and a `credential_grant.idle_expiring` event when it will go idle within 48 hours (for idle limits of 4 days or more); each once per end date or idle period. Off unless set. Leave it out to keep it as it is.
     */
    idle_warning?: (boolean | null);
    /**
     * Idle limit, as an ISO 8601 duration of weeks, days, hours, minutes or seconds (P1D to P999D, e.g. P14D; no years or months): the binding ends once it has gone this long without a call, and the broker then refuses calls through it with 403 `credential_binding_expired`. Each call restarts the clock (counted at most about hourly), and so does every renewal; a binding that is never used ends one idle period after it was granted. Send null to remove the idle limit; leave it out to keep it.
     */
    max_idle_period?: (string | null);
    /**
     * How many calls the binding allows before it ends (1 is single use); each authorized call takes one, whatever the upstream answers, and renewing restores the count. Send null to remove the count; leave it out to keep it (every renewal restores the uses left).
     */
    max_quota?: (number | null);
};

