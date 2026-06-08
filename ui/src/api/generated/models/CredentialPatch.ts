/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type CredentialPatch = {
    label?: (string | null);
    value?: (string | null);
    description?: (string | null);
    identity?: (string | null);
    api_id?: (string | null);
    /**
     * Update the auth type for this credential. See `POST /credentials` for valid values and semantics.
     */
    auth_type?: ('bearer' | 'basic' | 'apiKey' | 'none' | 'oauth2' | 'pipedream_oauth' | 'JenticApiKey' | null);
    server_variables?: (Record<string, string> | null);
    /**
     * Update the self-describing injection rule. See POST /credentials for format.
     */
    scheme?: (Record<string, any> | null);
    /**
     * Update the host+path routing patterns for this credential.
     */
    routes?: (Array<string> | null);
};

