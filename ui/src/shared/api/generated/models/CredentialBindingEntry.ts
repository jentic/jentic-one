/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ServedApiRef } from './ServedApiRef';
/**
 * Direct agent↔credential binding summary for the /me response (theme 5 phase 1).
 */
export type CredentialBindingEntry = {
    /**
     * When the credential was bound (RFC 3339).
     */
    bound_at: string;
    /**
     * ID of the bound credential.
     */
    credential_id: string;
    /**
     * Name of the bound credential; null if it no longer exists or cannot be resolved.
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
     * Whether the binding is suspended; the agent cannot execute through it until resumed.
     */
    suspended?: boolean;
    /**
     * Why the binding is suspended: null for a manual suspension, `api_deleted` when the API the credential serves was deleted.
     */
    suspended_reason?: (string | null);
};

