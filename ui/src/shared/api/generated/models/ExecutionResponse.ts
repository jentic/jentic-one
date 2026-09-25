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
    /**
     * The operation's HTTP method, e.g. GET. Null whenever operation_path is null.
     */
    operation_method?: (string | null);
    /**
     * The operation's spec path template, e.g. /repos/{owner}/{repo}. Null when the record carries no human-readable operation identity: records predating the column, executions of a URL that resolved to no registered operation, and async jobs enqueued with only an operation_id. Display surfaces show a placeholder for such rows — the opaque operation_id is a machine key, not a human fallback.
     */
    operation_path?: (string | null);
    origin?: (string | null);
    pinned_revisions?: (Record<string, any> | null);
    started_at: string;
    status: string;
    toolkit_id?: (string | null);
    toolkit_name?: (string | null);
    trace_id: string;
};

