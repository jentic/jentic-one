/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Token endpoint request (form-encoded or JSON body).
 */
export type TokenRequest = {
    assertion?: (string | null);
    client_id?: (string | null);
    client_secret?: (string | null);
    code?: (string | null);
    code_verifier?: (string | null);
    grant_type: string;
    redirect_uri?: (string | null);
    refresh_token?: (string | null);
};

