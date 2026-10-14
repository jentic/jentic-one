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
    /**
     * Vendor registry key the registration connects.
     */
    api_vendor: string;
    /**
     * Device authorization endpoint URL; null for authorization-code registrations.
     */
    authorization_endpoint?: (string | null);
    /**
     * Authorization endpoint URL; null for device-authorization registrations.
     */
    authorize_url?: (string | null);
    /**
     * Catalog API slug this OAuth app targets (e.g. 'googleapis-com/gmail'). Feeds credential.catalog_api_id at connect time so the operations preview resolves against a real registered API.
     */
    catalog_api_id: string;
    /**
     * OAuth client ID of the app.
     */
    client_id: string;
    /**
     * When the registration was created (RFC 3339).
     */
    created_at: string;
    /**
     * ID of the actor that created the registration.
     */
    created_by: (string | null);
    /**
     * Scopes requested by default; null if none are set.
     */
    default_scopes?: (Array<string> | null);
    /**
     * Number of credentials minted through this registration.
     */
    dependent_credential_count: number;
    /**
     * Vendor family label ('Gmail'), shown alongside the admin's per-registration 'name' on the picker card.
     */
    display_name: string;
    flow_kind: OAuthAppRegistrationFlowKind;
    /**
     * Whether a client secret is stored.
     */
    has_client_secret: boolean;
    /**
     * Unique ID of the OAuth app registration.
     */
    id: string;
    /**
     * Whether the registration is active.
     */
    is_active: boolean;
    /**
     * Admin-chosen name of the registration.
     */
    name: string;
    /**
     * When the client secret was last rotated (RFC 3339); null if never rotated.
     */
    secret_last_rotated_at: (string | null);
    /**
     * Token endpoint URL; null for authorization-code registrations.
     */
    token_endpoint?: (string | null);
    /**
     * Token endpoint URL; null for device-authorization registrations.
     */
    token_url?: (string | null);
    /**
     * When the registration was last updated (RFC 3339).
     */
    updated_at: string;
};

