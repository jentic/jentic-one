/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AfterExpiry } from './AfterExpiry';
/**
 * Re-grant access for an ended binding.
 *
 * Every field left out sets no such limit, except the end date: left out, the
 * binding runs again for as long as it was granted for, capped at the org's
 * maximum (``null`` sets no end date).
 */
export type GrantRenewalRequest = {
    /**
     * What a call through the binding gets once it has ended. `hold` (the default): the first such call is held as a `grant_renewal` for the agent's owner to renew the binding and run it; later calls get `403 credential_binding_expired` while that review is pending. `refuse`: every call gets `403 credential_binding_expired`, whose directive tells the agent to ask its owner to renew. Left out, the binding keeps its own.
     */
    after_expiry?: (AfterExpiry | null);
    /**
     * New end date (RFC 3339, in the future); null for no end date. Left out, the binding runs again for as long as it was granted for, capped at the org's maximum.
     */
    expires_at?: (string | null);
    /**
     * Warn the agent's owner before the binding ends (its end date or idle limit).
     */
    idle_warning?: boolean;
    /**
     * New idle limit, as an ISO 8601 duration (P1D to P999D, e.g. P14D); null for none.
     */
    max_idle_period?: (string | null);
    /**
     * New use count (1 is single use); null for unlimited calls.
     */
    max_quota?: (number | null);
};

