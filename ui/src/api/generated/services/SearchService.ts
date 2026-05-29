/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SearchResult } from '../models/SearchResult';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class SearchService {
    /**
     * Search the catalog — find operations and workflows by natural language intent
     * BM25 search over all registered API operations, Arazzo workflows, and the Jentic public API catalog.
     *
     * Returns id, summary, description (≤3 sentences), type, score, and _links.
     * - `source: "local"` — operation or workflow in your local registry
     * - `source: "catalog"` — API available from the Jentic public catalog; add credentials to use
     *
     * Each row also carries `matched_on` (which fields the query hit) and an
     * optional `match_snippet` with the matched span wrapped in `` markers.
     *
     * _links.inspect → GET /inspect/{id} for full schema and auth detail.
     * _links.execute → broker URL to call directly once ready.
     * Typical flow: search → inspect → execute.
     * @returns SearchResult Successful Response
     * @throws ApiError
     */
    public static searchSearchGet({
        q,
        n = 10,
        source,
        type,
    }: {
        /**
         * Search query, e.g. "send an email" or "create payment"
         */
        q: string,
        /**
         * Number of results to return
         */
        n?: number,
        /**
         * Restrict results by source: `workspace` (locally registered APIs and workflows) or `directory` (Jentic public catalog). Default `all` mixes both. Legacy synonyms `local`→`workspace` and `catalog`→`directory` are accepted for backwards compatibility.
         */
        source?: (string | null),
        /**
         * Restrict by result type: `endpoint` (workspace operations only), `workflow` (workspace workflows + directory APIs that ship workflows), or `api` (directory APIs). Default `all` returns the full mix. Directory APIs always carry a `has_workflows` boolean indicating whether the public catalog also ships Arazzo workflows for that vendor.
         */
        type?: (string | null),
    }): CancelablePromise<Array<SearchResult>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/search',
            query: {
                'q': q,
                'n': n,
                'source': source,
                'type': type,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
