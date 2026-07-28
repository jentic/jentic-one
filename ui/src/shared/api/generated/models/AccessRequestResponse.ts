/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AccessRequestItemResponse } from './AccessRequestItemResponse';
import type { AccessRequestOwnerResponse } from './AccessRequestOwnerResponse';
import type { EvaluationResponse } from './EvaluationResponse';
/**
 * Response model for an access request envelope.
 */
export type AccessRequestResponse = {
    actor_id: string;
    approve_url: string;
    created_by: string;
    evaluation?: (EvaluationResponse | null);
    expires_at: string;
    filed_at: string;
    filer_owner?: (AccessRequestOwnerResponse | null);
    filer_owner_id?: (string | null);
    id: string;
    items: Array<AccessRequestItemResponse>;
    reason?: (string | null);
    requested_by: string;
    status: string;
};

