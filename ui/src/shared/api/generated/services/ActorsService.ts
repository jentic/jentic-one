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
     * List users and agents
     * List users and agents with the minimal fields needed to label them.
     *
     * Each entry gives `id`, `actor_type` (`user` or `agent`), display `name`
     * and `active`. Requires `users:read`. Results are scoped to the caller:
     * `org:admin` lists everyone; anyone else lists only themselves and the
     * agents they may see (their own, or with `owner:agents:read` their owner's
     * agents). Cursor-paginated (`cursor`, `limit`). To label specific ids you
     * already hold, use `GET /actors/lookup`.
     * @returns ActorListResponse Successful Response
     * @throws ApiError
     */
    public static listActors({
        cursor,
        limit = 1000,
    }: {
        /**
         * Opaque cursor from a previous page's `next_cursor`; omit for the first page.
         */
        cursor?: (string | null),
        /**
         * Maximum number of actors to return (1-5000, default 1000).
         */
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
     * Resolve user and agent ids to display names.
     *
     * Pass each id as a repeated `id` parameter. Returns `id`, `actor_type`,
     * `name` and `active` for each id the caller may see. Any signed-in caller
     * may call it, so a UI can label the owners, approvers and actors it
     * already holds by id without `users:read`.
     *
     * Agents resolve when visible to the caller: itself, its own agents, its
     * owner's agents with `owner:agents:read`, or every agent for `org:admin`.
     * Users resolve when the caller can already see them by id: itself, the
     * owner or approver of an agent it can see (with `agents:read`), the actor
     * on an event it can see (with `events:read`), or the creator of a
     * credential it can read (with `credentials:read` or
     * `owner:credentials:read`). `org:admin`
     * resolves every user. Ids that match no visible actor are silently left
     * out, exactly like unknown ids. Listing the whole directory needs
     * `users:read` on `GET /actors`.
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
