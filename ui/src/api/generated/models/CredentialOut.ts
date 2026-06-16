/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Upstream API credential metadata. Secret values are never returned after creation.
 */
export type CredentialOut = {
    id: string;
    label: string;
    description?: (string | null);
    identity?: (string | null);
    api_id?: (string | null);
    auth_type?: (string | null);
    server_variables?: (Record<string, string> | null);
    scheme?: (Record<string, any> | null);
    routes?: (Array<string> | null);
    created_at?: (number | null);
    updated_at?: (number | null);
    last_used_at?: (number | null);
    account_id?: (string | null);
    app_slug?: (string | null);
    synced_at?: (number | null);
    healthy?: (boolean | null);
    health_checked_at?: (number | null);
};

