/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApprovalDecision } from './ApprovalDecision';
import type { GrantRenewalRequest } from './GrantRenewalRequest';
/**
 * Approve or deny a pending held execution.
 */
export type DecideHeldExecutionRequest = {
    /**
     * `approve` releases the call; `deny` fails it.
     */
    decision: ApprovalDecision;
    /**
     * Optional reason, recorded with the decision.
     */
    reason?: (string | null);
    /**
     * Approving a `grant_renewal` hold: the binding's fresh policy, which replaces its old one (renewing also restarts its idle clock and restores its uses). Required to approve one; refused on any other hold.
     */
    renewal?: (GrantRenewalRequest | null);
    /**
     * With `renewal`: whether the held call runs once access is renewed. `false` renews and fails the call (for a stale one).
     */
    run?: boolean;
};

