/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A vendor, or one admin registration for it, that the caller can connect to.
 */
export type VendorSummaryResponse = {
    /**
     * Catalog API (`<domain>/<sub>`) the registration signs in to; null for platform-shipped vendors.
     */
    catalog_api_id?: (string | null);
    /**
     * Vendor's display name (e.g. 'Gmail').
     */
    display_name: string;
    /**
     * Stable per-row key: the registration ID for admin registrations, else the vendor key.
     */
    entry_id: string;
    /**
     * OAuth flow kinds this row supports (`authorization_code`, `device_authorization`).
     */
    flow_kinds: Array<string>;
    /**
     * Vendor registry key shared by every registration for the vendor.
     */
    key: string;
    /**
     * Label for this row: the registration name for admin registrations, else the display name.
     */
    name: string;
    /**
     * ID of the admin OAuth app registration behind this row; null for platform-shipped vendors.
     */
    registration_id?: (string | null);
    /**
     * Where the row came from: `db` for an admin registration, `config` for a platform-shipped vendor.
     */
    source: VendorSummaryResponse.source;
    /**
     * Fully-qualified vendor ID (`<host>/<api-id>`) when known; otherwise the vendor key.
     */
    vendor: string;
};
export namespace VendorSummaryResponse {
    /**
     * Where the row came from: `db` for an admin registration, `config` for a platform-shipped vendor.
     */
    export enum source {
        DB = 'db',
        CONFIG = 'config',
    }
}

