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
    /**
     * Description of the operation; null if the spec gives none.
     */
    description?: (string | null);
    /**
     * HTTP method of the operation.
     */
    method: string;
    /**
     * Summary of the operation from the spec; null if it has none.
     */
    name?: (string | null);
    /**
     * Registry ID of the operation.
     */
    operation_id: string;
    /**
     * Relevance of the result to the query, from 0 to 1; higher is more relevant.
     */
    relevance_score: number;
    /**
     * The value to pass as the operation target to inspect/execute (CLI argument; MCP operation_id argument). METHOD:url when url is absolute; the registry operation_id when url is host-relative (the spec declares no servers, or only a relative one) — such a target is inspect-only: with no upstream host there is nothing for the broker to proxy, so execute refuses it.
     */
    target: string;
    /**
     * Result kind; always `operation`.
     */
    type?: string;
    /**
     * URL of the operation, built from the spec's server and path; host-relative when the spec declares no absolute server.
     */
    url: string;
};

