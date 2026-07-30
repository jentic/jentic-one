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
     * Whether this item's outcome is already in effect (the binding or grant it asks for already exists), letting a reviewer approve manually-fulfilled work instead of re-doing it in the wizard. Populated on single-request GETs for pending credential:bind, toolkit:bind, and scope:grant items; null when not computed (list endpoints, decided items, fulfilment-only intents, or an item whose target cannot be determined). Toolkit references are resolved under the caller's visibility, mirroring decide-time resolution.
     */
    already_satisfied?: (boolean | null);
    applied_effects?: (Record<string, any> | null);
    credential_name?: (string | null);
    decided_at?: (string | null);
    decided_by?: (string | null);
    decision_reason?: (string | null);
    id: string;
    resource_id?: (string | null);
    resource_reference?: (Record<string, any> | null);
    resource_type: string;
    rules?: null;
    status: string;
    to_id?: (string | null);
    to_type?: (string | null);
    toolkit_name?: (string | null);
};

