/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Create request for an authorization-code registration.
 */
export type AuthorizationCodeRegistrationCreateRequest = {
    api_vendor: string;
    authorize_url: string;
    client_id: string;
    client_secret: string;
    default_scopes?: (Array<string> | null);
    flow_kind: string;
    name: string;
    token_url: string;
};

