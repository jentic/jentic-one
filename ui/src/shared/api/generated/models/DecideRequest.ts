/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApprovalDecision } from './ApprovalDecision';
/**
 * Approve or deny a pending execution approval.
 */
export type DecideRequest = {
    /**
     * `approve` releases the call; `deny` fails it.
     */
    decision: ApprovalDecision;
    /**
     * Optional reason, recorded with the decision.
     */
    reason?: (string | null);
};

