/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Front-channel session-continue exchange posted from the login page.
 *
 * ``state`` is the signed ``login``-purpose carry-through token (``ls``)
 * minted by rung 3 of ``flow.resolve_identity_gate`` for this exact
 * authorize request — the exchange never accepts bare flow parameters, so a
 * caller cannot probe arbitrary client ids through it.
 *
 * ``state`` is deliberately NOT marked x-sensitive, for the same reason as
 * ``OAuthApprovalDecisionRequest.state``: the CLI's redaction backstop
 * unions bare field names globally, and the blob is not a lasting bearer
 * credential (HMAC-signed, purpose-discriminated, TTL'd; the endpoint
 * additionally requires an authenticated platform user).
 */
export type OAuthSessionContinueRequest = {
    state: string;
};

