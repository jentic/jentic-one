/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Authorization-code redirect challenge.
 *
 * Client opens ``authorize_url`` in a popup; completion lands
 * server-side via ``/credentials/oauth/callback``.
 */
export type AuthCodeConnectChallengeResponse = {
    /**
     * Vendor authorization URL to open in the browser that called this endpoint.
     */
    authorize_url: string;
    kind?: string;
    /**
     * Opaque CSRF state bound to this challenge; the callback accepts it only from the browser that started the flow.
     */
    state: string;
};

