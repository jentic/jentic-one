/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ActorListResponse } from '../models/ActorListResponse';
import type { ActorLookupResponse } from '../models/ActorLookupResponse';
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
    /**
     * Resolve actor names by id
     * Resolve user and agent ids to display names for any signed-in caller.
     *
     * Returns only ``id``, ``actor_type``, ``name`` and ``active`` for the ids
     * asked for, so a caller without ``users:read`` can label the owners,
     * approvers and actors it already sees by id. Users resolve for any caller;
     * agents only when the caller may see them (its own agents, itself, or with
     * ``owner:agents:read`` its owner's agents; every agent for ``org:admin``).
     * Ids that match no visible user or agent are left out of the response
     * rather than reported as errors. Listing the whole directory stays behind
     * ``users:read`` on ``GET /actors``.
     * @returns ActorLookupResponse Successful Response
     * @throws ApiError
     */
    public static lookupActors({
        id,
    }: {
        /**
         * Actor id to resolve; repeat the parameter for several ids (at most 100 per call, each at most 64 characters).
         */
        id: Array<string>,
    }): CancelablePromise<ActorLookupResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/actors/lookup',
            query: {
                'id': id,
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
