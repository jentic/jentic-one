/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExecutionStatus } from './ExecutionStatus';
/**
 * The outcome of a held call that ran: the execution document the run recorded.
 *
 * A held call that never ran answers an `application/problem+json` body
 * instead of this one.
 */
export type HeldExecutionResultResponse = {
    /**
     * The upstream response body, base64-encoded; absent when the upstream returned none. Interpret the decoded bytes against the upstream's own content type — the body is never inlined as JSON.
     */
    body_b64?: (string | null);
    /**
     * How long the call took, in milliseconds; 0 when it never ran.
     */
    duration_ms: number;
    /**
     * The execution record written for the released call.
     */
    execution_id: string;
    /**
     * Status the upstream answered with; null when the call never reached it.
     */
    http_status?: (number | null);
    /**
     * Present only when authorization was refused as the released call ran: the RFC 9457 body the synchronous execute route would have returned.
     */
    problem?: (Record<string, any> | null);
    /**
     * Outcome of the run: `completed`, or `failed` for an upstream, pipeline or re-authorization failure.
     */
    status: ExecutionStatus;
};

