/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Minimal tri-state for the consent page's pending-agent awaiting page (P4).
 *
 * Deliberately carries nothing else — no agent name, owner, or scopes — so
 * the anonymous poll endpoint cannot be used to read agent details. The
 * poll is keyed by a signed ``agent-status`` blob bound to one agent id,
 * never a bare id, and non-terminal lifecycle states (disabled, archived, a
 * vanished row) all read as ``pending`` so the endpoint is not a lifecycle
 * oracle either.
 */
export type ConsentAgentStatusResponse = {
    status: ConsentAgentStatusResponse.status;
};
export namespace ConsentAgentStatusResponse {
    export enum status {
        PENDING = 'pending',
        APPROVED = 'approved',
        DENIED = 'denied',
    }
}

