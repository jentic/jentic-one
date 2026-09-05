/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { GovernedHostsResponse } from '../models/GovernedHostsResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class GovernedHostsService {
    /**
     * Get Governed Hosts
     * The caller's governed host set (toolkit-bound hosts) with an ETag digest.
     *
     * **Always self-scoped** — derived from the authenticated identity's own
     * toolkit bindings; there is no cross-actor variant. Toolkits bind to agents
     * and toolkit keys, so those are the callers this endpoint serves — a plain
     * user token yields an empty set. The ``digest`` covers exactly the ``data``
     * list and is also emitted as a strong ``ETag``, so integrators poll with
     * ``If-None-Match`` and get an empty ``304`` until their host set actually
     * changes (the change-poll seam that replaces ``GET /apis`` enumeration for
     * interception scoping).
     * @returns GovernedHostsResponse Successful Response
     * @throws ApiError
     */
    public static getGovernedHosts(): CancelablePromise<GovernedHostsResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/governed-hosts',
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
