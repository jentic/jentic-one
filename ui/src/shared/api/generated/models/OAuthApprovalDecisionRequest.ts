/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Inline admin approve/deny posted from the approval-pending page.
 *
 * ``state`` is the signed approval-state blob minted by ``/authorize`` for
 * this exact authorize request — the decision endpoint never accepts a bare
 * ``client_id``.
 */
export type OAuthApprovalDecisionRequest = {
    action: OAuthApprovalDecisionRequest.action;
    state: string;
};
export namespace OAuthApprovalDecisionRequest {
    export enum action {
        APPROVE = 'approve',
        DENY = 'deny',
    }
}

