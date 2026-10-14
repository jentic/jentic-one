/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ServedApiRef } from './ServedApiRef';
/**
 * Direct agent↔credential binding representation in API responses.
 */
export type CredentialBindingResponse = {
    /**
     * ID of the agent the credential is bound to.
     */
    agent_id: string;
    /**
     * When the credential was bound (RFC 3339).
     */
    bound_at: string;
    /**
     * ID of the bound credential.
     */
    credential_id: string;
    /**
     * Unique ID of the binding.
     */
    id: string;
    /**
     * Name of the bound credential; null if it cannot be resolved.
     */
    name?: (string | null);
    /**
     * ID of the shared permission rule set attached; null when inline rules apply.
     */
    rule_set_id?: (string | null);
    /**
     * The APIs the bound credential serves; empty if unresolvable.
     */
    serves?: Array<ServedApiRef>;
    /**
     * Whether the binding is suspended, cutting off the agent's access to it.
     */
    suspended: boolean;
    /**
     * Why the binding is suspended: null for a manual suspension, `api_deleted` when the API the credential serves was deleted; cleared on resume.
     */
    suspended_reason?: (string | null);
};

