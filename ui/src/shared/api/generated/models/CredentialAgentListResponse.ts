/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CredentialAgentResponse } from './CredentialAgentResponse';
/**
 * Paginated list of agents directly bound to a credential.
 */
export type CredentialAgentListResponse = {
    data: Array<CredentialAgentResponse>;
    has_more: boolean;
    next_cursor?: (string | null);
};

