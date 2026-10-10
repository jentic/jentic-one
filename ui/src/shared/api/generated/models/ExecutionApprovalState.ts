/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Lifecycle of an execution approval; every state but ``pending`` is terminal.
 */
export enum ExecutionApprovalState {
    PENDING = 'pending',
    APPROVED = 'approved',
    DENIED = 'denied',
    EXPIRED = 'expired',
    WITHDRAWN = 'withdrawn',
}
