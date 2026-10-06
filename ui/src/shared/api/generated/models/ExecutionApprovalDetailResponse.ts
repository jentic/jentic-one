/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExecutionApprovalLinksResponse } from './ExecutionApprovalLinksResponse';
import type { ExecutionApprovalState } from './ExecutionApprovalState';
import type { HeldRequestResponse } from './HeldRequestResponse';
/**
 * An approval with the agent context and the held request a reviewer decides on.
 */
export type ExecutionApprovalDetailResponse = {
    _links: ExecutionApprovalLinksResponse;
    /**
     * The agent whose call was held.
     */
    agent_id: string;
    /**
     * Display name of the agent.
     */
    agent_name?: (string | null);
    /**
     * The agent's owner; null for an ownerless agent (admin-only).
     */
    agent_owner_id?: (string | null);
    /**
     * Name of the API called.
     */
    api_name: string;
    /**
     * Vendor of the API called.
     */
    api_vendor: string;
    /**
     * Version of the API called.
     */
    api_version: string;
    /**
     * When the call was held.
     */
    created_at: string;
    /**
     * The credential selected when the call was held.
     */
    credential_id: string;
    /**
     * When it left `pending`.
     */
    decided_at?: (string | null);
    /**
     * Reviewer who decided it.
     */
    decided_by?: (string | null);
    /**
     * Reviewer's reason.
     */
    decision_reason?: (string | null);
    /**
     * Execution record written once the approved job ran.
     */
    execution_id?: (string | null);
    /**
     * When a pending approval expires undecided.
     */
    expires_at: string;
    /**
     * Approval id (`exap_…`).
     */
    id: string;
    /**
     * The held execution job.
     */
    job_id: string;
    /**
     * The require-approval permission rule that held the call.
     */
    matched_rule_id?: (string | null);
    /**
     * HTTP method of the held call.
     */
    method: string;
    /**
     * Resolved OpenAPI operation id.
     */
    operation_id?: (string | null);
    /**
     * Upstream path of the held call.
     */
    path: string;
    /**
     * The held call.
     */
    request?: (HeldRequestResponse | null);
    /**
     * Approval state.
     */
    state: ExecutionApprovalState;
    /**
     * Trace id of the held call.
     */
    trace_id?: (string | null);
    /**
     * Last change.
     */
    updated_at?: (string | null);
};

