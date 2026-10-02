/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A single permission entry from the catalogue.
 */
export type PermissionResponse = {
    description: string;
    /**
     * Whether the caller may grant this scope to an agent. `org:admin` callers may grant any scope; anyone else only scopes they hold or the default agent scopes, and never `org:admin` or `agents:write`.
     */
    grantable_by_caller: boolean;
    implies: Array<string>;
    name: string;
};

