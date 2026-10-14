/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CredentialAgentResponse } from './CredentialAgentResponse';
/**
 * Paginated list of agents directly bound to a credential.
 */
export type CredentialAgentListResponse = {
    /**
     * The page of bound agents.
     */
    data: Array<CredentialAgentResponse>;
    /**
     * Whether more results exist after this page.
     */
    has_more: boolean;
    /**
     * Cursor for the next page; null when there are no more results.
     */
    next_cursor?: (string | null);
};

