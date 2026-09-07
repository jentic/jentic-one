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
 *
 * ``state`` is deliberately NOT marked x-sensitive: the CLI's GEN-21
 * redaction backstop unions every sensitive field's BARE name globally, and
 * "state" is generic enough to redact unrelated CLI output (e.g. the MCP
 * session-diagnosis ``state`` field). The blob is not a lasting bearer
 * credential — it is HMAC-signed, purpose-discriminated, TTL'd (600 s), and
 * the decision endpoint additionally requires an authenticated admin with
 * ``oauth-clients:write`` — so global redaction buys nothing worth that
 * collision.
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

