/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConnectSessionApiReferenceResponse } from './ConnectSessionApiReferenceResponse';
import type { ExistingCredentialResponse } from './ExistingCredentialResponse';
import type { PermissionRuleSchema } from './PermissionRuleSchema';
import type { ReviewAgentResponse } from './ReviewAgentResponse';
import type { ReviewProvenanceResponse } from './ReviewProvenanceResponse';
import type { ReviewSchemeResponse } from './ReviewSchemeResponse';
import type { ReviewScopeResponse } from './ReviewScopeResponse';
/**
 * What the review page shows the approver of a connect session.
 */
export type ReviewSessionResponse = {
    /**
     * The agent the session binds, and its owner.
     */
    agent?: (ReviewAgentResponse | null);
    /**
     * Where the target's OpenAPI lives in the registry.
     */
    api_reference: ConnectSessionApiReferenceResponse;
    /**
     * Whether this viewer may confirm the session as it stands.
     */
    can_confirm: boolean;
    /**
     * Fingerprint of the review; echo it on `:confirm`.
     */
    digest: string;
    /**
     * Credentials the viewer already holds for the target.
     */
    existing_credentials?: Array<ExistingCredentialResponse>;
    /**
     * Hosts the credential may be sent to, pinned at `:connect`.
     */
    pinned_hosts?: (Array<string> | null);
    /**
     * Where an API target's live revision came from.
     */
    provenance?: (ReviewProvenanceResponse | null);
    /**
     * Free-text reason given when the session was started.
     */
    reason: (string | null);
    /**
     * ID of the actor that started the session.
     */
    requested_by_actor_id: string;
    /**
     * Permission rules the initiator asked for.
     */
    requested_permission_rules?: Array<PermissionRuleSchema>;
    /**
     * OAuth scopes the session asks for.
     */
    requested_scopes?: Array<string>;
    /**
     * The flow the session runs.
     */
    resolved_flow: string;
    /**
     * The declared scheme an API target collects.
     */
    scheme?: (ReviewSchemeResponse | null);
    /**
     * The vendor's scope catalogue, flagged default and requested.
     */
    scopes: Array<ReviewScopeResponse>;
    /**
     * Unique ID of the connect session.
     */
    session_id: string;
    /**
     * Current lifecycle state of the session.
     */
    state: string;
    /**
     * `vendor` for a vendor-registry key, `api` for a registry API.
     */
    target_kind?: ReviewSessionResponse.target_kind;
    /**
     * Display name of the vendor.
     */
    vendor_display_name: string;
    /**
     * Vendor registry key the session connects.
     */
    vendor_key: string;
};
export namespace ReviewSessionResponse {
    /**
     * `vendor` for a vendor-registry key, `api` for a registry API.
     */
    export enum target_kind {
        VENDOR = 'vendor',
        API = 'api',
    }
}

