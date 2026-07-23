/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Toolkit response.
 */
export type ToolkitResponse = {
    active: boolean;
    created_at: string;
    created_by?: (string | null);
    credential_count: number;
    description?: (string | null);
    key_count: number;
    name: string;
    permissions: Array<Record<string, any>>;
    toolkit_id: string;
    updated_at?: (string | null);
};

