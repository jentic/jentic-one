/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Lifecycle of an execution approval; every state but ``pending`` is terminal.
 */
export enum HeldExecutionState {
    PENDING = 'pending',
    APPROVED = 'approved',
    DENIED = 'denied',
    EXPIRED = 'expired',
    WITHDRAWN = 'withdrawn',
}
