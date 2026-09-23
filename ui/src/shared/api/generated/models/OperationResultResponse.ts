/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiReferenceResponse } from './ApiReferenceResponse';
import type { SearchLinksResponse } from './SearchLinksResponse';
/**
 * A single search result matching the OperationResult spec.
 */
export type OperationResultResponse = {
    _links: SearchLinksResponse;
    api: ApiReferenceResponse;
    description?: (string | null);
    method: string;
    name?: (string | null);
    operation_id: string;
    relevance_score: number;
    /**
     * The value to pass as the operation target to inspect/execute (CLI argument; MCP operation_id argument). METHOD:url when url is absolute; the registry operation_id when the operation's spec declares no servers (url is then host-relative and does not resolve as METHOD:url).
     */
    target: string;
    type?: string;
    url: string;
};

