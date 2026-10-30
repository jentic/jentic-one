/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExistingCredentialResponse } from './ExistingCredentialResponse';
import type { PermissionRuleSchema } from './PermissionRuleSchema';
import type { ReviewAgentResponse } from './ReviewAgentResponse';
import type { ReviewEditResponse } from './ReviewEditResponse';
import type { ReviewProvenanceResponse } from './ReviewProvenanceResponse';
import type { ReviewSchemeResponse } from './ReviewSchemeResponse';
import type { ReviewScopeResponse } from './ReviewScopeResponse';
import type { SessionApiReferenceResponse } from './SessionApiReferenceResponse';
import type { SessionRevisionRef } from './SessionRevisionRef';
export type ReviewSessionResponse = {
    agent?: (ReviewAgentResponse | null);
    api_reference: SessionApiReferenceResponse;
    can_confirm: boolean;
    digest: string;
    edit?: (ReviewEditResponse | null);
    existing_credentials?: Array<ExistingCredentialResponse>;
    /**
     * When a human last rejected this agent's request for the same target (or, for an edit, the same binding), within the last 30 days; null when none. Lets the reviewer see that a request repeats one already rejected.
     */
    last_rejected_at?: (string | null);
    pinned_hosts?: (Array<string> | null);
    provenance?: (ReviewProvenanceResponse | null);
    reason: (string | null);
    requested_by_actor_id: string;
    requested_permission_rules?: Array<PermissionRuleSchema>;
    requested_scopes?: Array<string>;
    resolved_flow: string;
    revision?: (SessionRevisionRef | null);
    scheme?: (ReviewSchemeResponse | null);
    scopes: Array<ReviewScopeResponse>;
    session_id: string;
    state: string;
    target_kind?: ReviewSessionResponse.target_kind;
    vendor_display_name: string;
    vendor_key: string;
};
export namespace ReviewSessionResponse {
    export enum target_kind {
        VENDOR = 'vendor',
        API = 'api',
    }
}

