/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { MeAgent } from '../models/MeAgent';
import type { MeUser } from '../models/MeUser';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class IdentityService {
    /**
     * Get Me
     * Return the caller's identity and context, discriminated by actor type.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getMe(): CancelablePromise<(MeUser | MeAgent)> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/me',
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
