/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { jentic_one__control__web__schemas__access_requests__PermissionRuleSchema } from './jentic_one__control__web__schemas__access_requests__PermissionRuleSchema';
/**
 * A single line-item in a file request.
 *
 * **Permission rules:** Rules control which upstream API operations the broker
 * allows through a direct agent↔credential binding. They are enforced per
 * (agent, credential) pair, so they can only be attached to credential:bind
 * items — not scope:grant. Include them (or a shared ``rule_set_id``)
 * directly on the credential:bind item when filing the access request, and
 * the approver's decision persists them on the binding.
 */
export type AccessRequestItemRequest = {
    action: AccessRequestItemRequest.action;
    resource_id?: (string | null);
    resource_reference?: (Record<string, any> | null);
    resource_type: AccessRequestItemRequest.resource_type;
    /**
     * Shared permission rule set for the binding (credential:bind only), as an alternative to inline rules. While attached, the set's ordered list is the binding's effective policy.
     */
    rule_set_id?: (string | null);
    /**
     * Permission rules for the binding (credential:bind only). Rules are evaluated first-match-wins by the broker; if no rule matches, the request is denied. Example: [{"effect": "allow", "path": ".*"}].
     */
    rules?: (Array<jentic_one__control__web__schemas__access_requests__PermissionRuleSchema> | null);
};
export namespace AccessRequestItemRequest {
    export enum action {
        BIND = 'bind',
        GRANT = 'grant',
        PROVISION = 'provision',
    }
    export enum resource_type {
        CREDENTIAL = 'credential',
        SCOPE = 'scope',
    }
}

