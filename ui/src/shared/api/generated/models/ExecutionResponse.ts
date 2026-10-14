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
    /**
     * ID of the actor that ran the execution.
     */
    actor_id: string;
    /**
     * Kind of actor that ran the execution, e.g. `user` or `agent`. Opaque string: historical rows may carry a retired type.
     */
    actor_type: string;
    /**
     * The API that was called; null when it is not recorded.
     */
    api?: (ApiInfoResponse | null);
    /**
     * When the execution record was created (RFC 3339).
     */
    created_at: string;
    /**
     * ID of the credential used. Null for inline auth, credential-less APIs, older records and executions that failed before a credential was chosen.
     */
    credential_id?: (string | null);
    /**
     * Name of the credential used; null when no credential is recorded.
     */
    credential_name?: (string | null);
    /**
     * Duration of the execution in milliseconds; null if unknown.
     */
    duration_ms?: (number | null);
    /**
     * Short error message (at most 128 characters) for a failed execution.
     */
    error?: (string | null);
    /**
     * Unique ID of the execution record.
     */
    execution_id: string;
    /**
     * HTTP status returned by the upstream; null if no upstream response.
     */
    http_status?: (number | null);
    /**
     * Machine identifier of the operation called; null when none was resolved.
     */
    operation_id?: (string | null);
    /**
     * The operation's HTTP method, e.g. GET. Null whenever operation_path is null.
     */
    operation_method?: (string | null);
    /**
     * The operation's spec path template, e.g. /repos/{owner}/{repo}. Null when the record carries no human-readable operation identity: records predating the column, executions of a URL that resolved to no registered operation, and async jobs enqueued with only an operation_id. Display surfaces show a placeholder for such rows — the opaque operation_id is a machine key, not a human fallback.
     */
    operation_path?: (string | null);
    /**
     * Surface the execution was initiated from, e.g. `api`, `agent` or `mcp`; null if not recorded.
     */
    origin?: (string | null);
    /**
     * Pinned API revisions used, keyed `vendor:name:version` with the revision ID as value; null when no pin applied.
     */
    pinned_revisions?: (Record<string, any> | null);
    /**
     * When the execution started (RFC 3339).
     */
    started_at: string;
    /**
     * Terminal outcome: `completed` or `failed`. An upstream response with HTTP status 400 or above counts as `failed`.
     */
    status: string;
    /**
     * Legacy: toolkit that mediated the execution. Null for direct-binding executions; attribute those via `credential_id` instead.
     */
    toolkit_id?: (string | null);
    /**
     * Legacy: name of the toolkit in `toolkit_id`; null when unavailable.
     */
    toolkit_name?: (string | null);
    /**
     * Trace ID of the execution request.
     */
    trace_id: string;
};

