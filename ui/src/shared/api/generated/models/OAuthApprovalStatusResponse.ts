/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Minimal tri-state approval status for a pending-client authorize request.
 *
 * Deliberately carries nothing else — no client name, redirect URIs, or
 * metadata — so the anonymous poll endpoint cannot be used to read client
 * details out of the registry.
 */
export type OAuthApprovalStatusResponse = {
    status: OAuthApprovalStatusResponse.status;
};
export namespace OAuthApprovalStatusResponse {
    export enum status {
        PENDING = 'pending',
        APPROVED = 'approved',
        DENIED = 'denied',
    }
}

