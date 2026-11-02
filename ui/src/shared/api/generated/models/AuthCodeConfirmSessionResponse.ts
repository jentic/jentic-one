/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Authorization-code result — browser redirect target; completion
 * lands server-side at ``/credentials/oauth/callback`` and the client
 * observes it via ``/status``.
 */
export type AuthCodeConfirmSessionResponse = {
    authorize_url: string;
    kind?: string;
};

