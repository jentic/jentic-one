/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OAuthAppRegistrationFlowKind } from './OAuthAppRegistrationFlowKind';
/**
 * Registration as returned to admins.
 *
 * Never includes the client secret — only ``has_client_secret`` +
 * ``secret_last_rotated_at`` so an admin can see rotation history without
 * being able to read the material.
 */
export type OAuthAppRegistrationResponse = {
    api_vendor: string;
    authorization_endpoint?: (string | null);
    authorize_url?: (string | null);
    /**
     * Catalog API slug this OAuth app targets (e.g. 'googleapis-com/gmail'). Feeds credential.catalog_api_id at connect time so the operations preview resolves against a real registered API. Nullable on pre-refactor rows only — new registrations always carry a value.
     */
    catalog_api_id?: (string | null);
    client_id: string;
    created_at: string;
    created_by: (string | null);
    default_scopes?: (Array<string> | null);
    dependent_credential_count: number;
    /**
     * Vendor family label ('Gmail'), shown alongside the admin's per-registration 'name' on the picker card. Nullable on pre-refactor rows only.
     */
    display_name?: (string | null);
    flow_kind: OAuthAppRegistrationFlowKind;
    has_client_secret: boolean;
    id: string;
    is_active: boolean;
    name: string;
    secret_last_rotated_at: (string | null);
    token_endpoint?: (string | null);
    token_url?: (string | null);
    updated_at: string;
};

