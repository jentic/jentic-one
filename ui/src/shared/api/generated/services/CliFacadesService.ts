/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CliFacadeListResponse } from '../models/CliFacadeListResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class CliFacadesService {
    /**
     * List vendor CLI facades
     * The vendor CLI facades this deployment serves, and the address of each.
     *
     * `jentic cf` (and `jentic cli list`) read this to learn where to point the
     * vendor's own CLI, so no agent hard-codes a facade address. Lists only
     * facades that are switched on (`cli_facades.enabled` and the vendor's
     * `enabled`) and implemented by the Broker. Whether the caller holds a
     * credential for the vendor is not checked here: a call without one fails
     * at the facade with the usual Broker problem.
     * @returns CliFacadeListResponse Successful Response
     * @throws ApiError
     */
    public static listCliFacades(): CancelablePromise<CliFacadeListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/cli-facades',
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
