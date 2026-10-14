/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Response when an invite token is issued.
 */
export type InviteIssuedResponse = {
    /**
     * When the invite token expires (RFC 3339).
     */
    expires_at: string;
    /**
     * One-time invite token the user redeems to set a password.
     */
    token: string;
};

