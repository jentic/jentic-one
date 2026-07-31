/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { APIReference } from './APIReference';
/**
 * Toolkit response.
 */
export type ToolkitResponse = {
    active: boolean;
    /**
     * Distinct (vendor, name, version) APIs served by this toolkit's credential bindings, sorted by vendor/name/version. Empty when no credentials are bound.
     */
    apis?: Array<APIReference>;
    created_at: string;
    created_by?: (string | null);
    credential_count: number;
    description?: (string | null);
    key_count: number;
    name: string;
    toolkit_id: string;
    updated_at?: (string | null);
};

