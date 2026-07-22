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
    resource_id?: (string | null);
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
        CREATE = 'create',
        PROVISION = 'provision',
    }
    export enum resource_type {
        CREDENTIAL = 'credential',
        TOOLKIT = 'toolkit',
        SCOPE = 'scope',
    }
}

