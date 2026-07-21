/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { jentic_one__control__web__schemas__access_requests__PermissionRuleSchema } from './jentic_one__control__web__schemas__access_requests__PermissionRuleSchema';
/**
 * A single line-item in a file request.
 *
 * **Permission rules:** Rules control which upstream API operations the broker
 * allows through a credential binding. They are enforced per (toolkit_id,
 * credential_id) pair, so they can only be attached to credential:bind items —
 * not toolkit:bind or scope:grant. You do not need toolkits:write scope to set
 * rules; include them directly on the credential:bind item when filing the
 * access request, and the approver's decision persists them on the binding.
 */
export type AccessRequestItemRequest = {
    action: AccessRequestItemRequest.action;
    resource_id?: (string | null);
    resource_reference?: (Record<string, any> | null);
    resource_type: AccessRequestItemRequest.resource_type;
    /**
     * Permission rules for the binding (credential:bind only). Rules are evaluated first-match-wins by the broker; if no rule matches, the request is denied. Example: [{"effect": "allow", "path": ".*"}].
     */
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

