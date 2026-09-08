/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ServedApiRef } from './ServedApiRef';
/**
 * Toolkit response.
 */
export type ToolkitResponse = {
    active: boolean;
    /**
     * Distinct APIs served by this toolkit's credential bindings that are visible to the caller, sorted by vendor/name/version. NULL api_name/api_version mean the credential covers all names/versions for the vendor. Empty when no visible credentials are bound.
     */
    apis?: Array<ServedApiRef>;
    created_at: string;
    created_by?: (string | null);
    credential_count: number;
    description?: (string | null);
    key_count: number;
    name: string;
    toolkit_id: string;
    updated_at?: (string | null);
};

