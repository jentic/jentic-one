/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiInfoResponse } from './ApiInfoResponse';
import type { ExecutionRecordLinks } from './ExecutionRecordLinks';
/**
 * Execution record representation in API responses.
 */
export type ExecutionResponse = {
    _links: ExecutionRecordLinks;
    actor_id: string;
    actor_type: string;
    api?: (ApiInfoResponse | null);
    created_at: string;
    credential_id?: (string | null);
    credential_name?: (string | null);
    duration_ms?: (number | null);
    error?: (string | null);
    execution_id: string;
    http_status?: (number | null);
    operation_id?: (string | null);
    origin?: (string | null);
    pinned_revisions?: (Record<string, any> | null);
    started_at: string;
    status: string;
    toolkit_id: string;
    toolkit_name?: (string | null);
    trace_id: string;
};

