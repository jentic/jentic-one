/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ActorListResponse } from '../models/ActorListResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ActorsService {
    /**
     * List Actors
     * List all actors (users and agents) for UI cache hydration.
     * @returns ActorListResponse Successful Response
     * @throws ApiError
     */
    public static listActors({
        cursor,
        limit = 1000,
    }: {
        cursor?: (string | null),
        limit?: number,
    }): CancelablePromise<ActorListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/actors',
            query: {
                'cursor': cursor,
                'limit': limit,
            },
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
