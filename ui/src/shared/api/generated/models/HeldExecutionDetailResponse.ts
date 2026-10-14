/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { HeldExecutionLinksResponse } from './HeldExecutionLinksResponse';
import type { HeldExecutionState } from './HeldExecutionState';
import type { HeldRequestResponse } from './HeldRequestResponse';
/**
 * A held execution with the agent context and the held request a reviewer decides on.
 */
export type HeldExecutionDetailResponse = {
    _links: HeldExecutionLinksResponse;
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
     * Execution record written once the approved call ran.
     */
    execution_id?: (string | null);
    /**
     * Where the held call stands: `held` until decided, then `queued`, `running`, and `completed`, `failed` or `cancelled`.
     */
    execution_status?: ('held' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | null);
    /**
     * When a pending held execution expires undecided.
     */
    expires_at: string;
    /**
     * Held execution id (`held_…`).
     */
    id: string;
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
     * Review page a signed-in reviewer opens to approve or deny the call. Carries no credential.
     */
    review_url: string;
    /**
     * The decision: `pending` until a reviewer acts, then `approved`, `denied`, `expired` or `withdrawn`.
     */
    state: HeldExecutionState;
    /**
     * Trace id of the held call.
     */
    trace_id?: (string | null);
    /**
     * Last change.
     */
    updated_at?: (string | null);
};

