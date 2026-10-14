/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SearchRequest } from '../models/SearchRequest';
import type { SearchResponse } from '../models/SearchResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class SearchService {
    /**
     * Search operations
     * Lexical (full-text) search over registered API operations.
     * @returns SearchResponse Successful Response
     * @throws ApiError
     */
    public static searchOperations({
        requestBody,
    }: {
        /**
         * POST /search request body.
         */
        requestBody: SearchRequest,
    }): CancelablePromise<SearchResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/search',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
}
