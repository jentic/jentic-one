/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExecutionApprovalLinksResponse } from './ExecutionApprovalLinksResponse';
/**
 * Execution approval detail in API responses.
 */
export type ExecutionApprovalResponse = {
    _links: ExecutionApprovalLinksResponse;
    agent_id: string;
    api_name: string;
    api_vendor: string;
    api_version: string;
    created_at: string;
    credential_id: string;
    decided_at?: (string | null);
    decided_by?: (string | null);
    decision_reason?: (string | null);
    execution_id?: (string | null);
    expires_at: string;
    id: string;
    job_id: string;
    matched_rule_id?: (string | null);
    method: string;
    operation_id?: (string | null);
    path: string;
    state: string;
    trace_id?: (string | null);
    updated_at?: (string | null);
};

