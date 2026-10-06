/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { APIReferenceRequest } from './APIReferenceRequest';
import type { RuntimeConfig } from './RuntimeConfig';
/**
 * Create request for oauth2 credentials.
 *
 * For managed providers (e.g. pipedream, direct_oauth2), token_url/client_id/client_secret
 * are optional — the connect flow handles authentication without caller-supplied client details.
 */
export type OAuth2CreateRequest = {
    api: APIReferenceRequest;
    authorize_url?: (string | null);
    /**
     * After saving, make one read call with the credential and return the verdict as `check`. The credential is saved either way.
     */
    check?: boolean;
    client_id?: (string | null);
    client_secret?: (string | null);
    grant_type?: string;
    name: string;
    provider?: string;
    runtime_config?: (RuntimeConfig | null);
    scopes?: (Array<string> | null);
    server_variables?: (Record<string, string> | null);
    token_url?: (string | null);
    type: string;
};

