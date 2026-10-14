/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A single permission entry from the catalogue.
 */
export type PermissionResponse = {
    /**
     * Human-readable description of what the permission allows.
     */
    description: string;
    /**
     * Whether the caller may grant this scope to an agent. `org:admin` callers may grant any scope; anyone else only scopes they hold or the default agent scopes, and never `org:admin` or `agents:write`.
     */
    grantable_by_caller: boolean;
    /**
     * Permissions this one implies when granted.
     */
    implies: Array<string>;
    /**
     * Permission name, e.g. `users:read`.
     */
    name: string;
};

