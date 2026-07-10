/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { jentic_one__control__web__schemas__access_requests__PermissionRuleSchema } from './jentic_one__control__web__schemas__access_requests__PermissionRuleSchema';
/**
 * A single line-item in a file request.
 */
export type AccessRequestItemRequest = {
    action: AccessRequestItemRequest.action;
    /**
     * Explicit ID of the resource (e.g. a toolkit tk_… or credential cred_… ID). For toolkit:bind, you can omit this and use resource_reference instead.
     */
    resource_id?: (string | null);
    /**
     * Look up the resource by API identity instead of by ID. For toolkit:bind, provide {vendor, name, version} to resolve the toolkit that serves the given API. Use this when you don't know the toolkit ID.
     */
    resource_reference?: (Record<string, any> | null);
    resource_type: AccessRequestItemRequest.resource_type;
    rules?: (Array<jentic_one__control__web__schemas__access_requests__PermissionRuleSchema> | null);
    to_id?: (string | null);
    to_type?: (string | null);
};
export namespace AccessRequestItemRequest {
    export enum action {
        BIND = 'bind',
        GRANT = 'grant',
    }
    export enum resource_type {
        CREDENTIAL = 'credential',
        TOOLKIT = 'toolkit',
        SCOPE = 'scope',
    }
}

