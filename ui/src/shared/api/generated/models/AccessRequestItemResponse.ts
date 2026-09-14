/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Response model for a single access-request line item.
 */
export type AccessRequestItemResponse = {
    action: string;
    /**
     * Whether this item's outcome is already in effect (the binding or grant it asks for already exists), letting a reviewer approve manually-fulfilled work instead of re-doing it in the wizard. Populated on single-request GETs for pending credential:bind and scope:grant items; null when not computed (list endpoints, decided items, fulfilment-only intents, an item whose target cannot be determined, an ambiguous credential reference — which approval would refuse as filed — or a credential:bind whose credential is not visible to the caller). API REFERENCES are resolved under the caller's visibility, mirroring decide-time resolution, so False can also mean 'satisfied by a credential this caller cannot see'; explicit-id targets are probed directly.
     */
    already_satisfied?: (boolean | null);
    /**
     * For a satisfied credential:bind, the id of the credential the agent is already bound to — names the exact object so consumers can point the operator at it. Null for other item types and whenever already_satisfied is not true.
     */
    already_satisfied_by?: (string | null);
    applied_effects?: (Record<string, any> | null);
    credential_name?: (string | null);
    decided_at?: (string | null);
    decided_by?: (string | null);
    decision_reason?: (string | null);
    id: string;
    resource_id?: (string | null);
    resource_reference?: (Record<string, any> | null);
    resource_type: string;
    /**
     * Shared permission rule set attached to a credential:bind item, as an alternative policy carrier to inline rules.
     */
    rule_set_id?: (string | null);
    rules?: null;
    status: string;
    to_id?: (string | null);
    to_type?: (string | null);
    toolkit_name?: (string | null);
};

