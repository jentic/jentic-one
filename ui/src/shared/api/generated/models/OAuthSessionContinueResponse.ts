/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The resume leg for a successful session-continue exchange.
 *
 * ``redirect_url`` is a same-origin, relative ``/authorize`` URL re-running
 * the ORIGINAL authorize request plus the short-TTL ``session``-purpose
 * continuation blob (``sc``) pinning the platform user at exchange time.
 * Relative by design (mirrors the approval-pending page's resume URL): the
 * page script navigates within its own origin, never to a caller-influenced
 * absolute URL.
 */
export type OAuthSessionContinueResponse = {
    redirect_url: string;
};

