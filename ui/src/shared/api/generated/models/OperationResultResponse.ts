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
     * The value to pass as the operation target to inspect/execute (CLI argument; MCP operation_id argument). METHOD:url when url is absolute; the registry operation_id when url is host-relative (the spec declares no servers, or only a relative one) — such a target is inspect-only: with no upstream host there is nothing for the broker to proxy, so execute refuses it.
     */
    target: string;
    type?: string;
    url: string;
};

