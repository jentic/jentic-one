/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { HealthResponse } from '../models/HealthResponse';
import type { InstanceIdentityResponse } from '../models/InstanceIdentityResponse';
import type { LatestReleaseResponse } from '../models/LatestReleaseResponse';
import type { LatestReleaseSetRequest } from '../models/LatestReleaseSetRequest';
import type { VersionResponse } from '../models/VersionResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class SystemService {
    /**
     * Health
     * Return admin service health status.
     * @returns HealthResponse Successful Response
     * @throws ApiError
     */
    public static health(): CancelablePromise<HealthResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/admin/health',
            errors: {
                400: `Bad Request`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Report the latest available release
     * Record the latest available app release (operator/CLI action).
     *
     * The value is normalized to a bare ``X.Y.Z`` and upserted into the singleton
     * ``latest_releases`` row; the public ``GET /system/version`` reads it to decide
     * whether to advertise an update.
     * @returns LatestReleaseResponse Successful Response
     * @throws ApiError
     */
    public static setLatestRelease({
        requestBody,
    }: {
        requestBody: LatestReleaseSetRequest,
    }): CancelablePromise<LatestReleaseResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/admin/system/latest-release',
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
    /**
     * Auth health
     * Return service health status for this surface.
     *
     * Unauthenticated liveness probe. In combined mode this is served under
     * the surface prefix (e.g. ``/control/health``) so surfaces don't collide;
     * the canonical platform probe is the root ``GET /health``.
     * @returns string Successful Response
     * @throws ApiError
     */
    public static authHealth(): CancelablePromise<Record<string, string>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/auth/health',
            errors: {
                400: `Bad Request`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Control health
     * Return service health status for this surface.
     *
     * Unauthenticated liveness probe. In combined mode this is served under
     * the surface prefix (e.g. ``/control/health``) so surfaces don't collide;
     * the canonical platform probe is the root ``GET /health``.
     * @returns string Successful Response
     * @throws ApiError
     */
    public static controlHealth(): CancelablePromise<Record<string, string>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/control/health',
            errors: {
                400: `Bad Request`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Health
     * Liveness probe for the combined control-plane app.
     *
     * Unauthenticated and dependency-free so orchestrators and load balancers
     * have a stable target. Returns ``{"status": "ok"}`` when the process is up.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getHealth(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/health',
        });
    }
    /**
     * Backend identity
     * Return this backend's self-describing identity.
     *
     * Unauthenticated and dependency-free so any client (an MCP server, the
     * CLI, an agent) can read which backend it is bound to — local vs. a
     * remote install — and label its responses accordingly.
     * @returns InstanceIdentityResponse Successful Response
     * @throws ApiError
     */
    public static getInstance(): CancelablePromise<InstanceIdentityResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/instance',
        });
    }
    /**
     * Registry health
     * Return service health status for this surface.
     *
     * Unauthenticated liveness probe. In combined mode this is served under
     * the surface prefix (e.g. ``/control/health``) so surfaces don't collide;
     * the canonical platform probe is the root ``GET /health``.
     * @returns string Successful Response
     * @throws ApiError
     */
    public static registryHealth(): CancelablePromise<Record<string, string>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/registry/health',
            errors: {
                400: `Bad Request`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Running and latest-known app version
     * Return the running version and the latest release known to this backend.
     *
     * Unauthenticated and dependency-free (only the app context) so the SPA can
     * read it before/without a session to show the current version and, when a
     * newer release has been reported, an update banner.
     * @returns VersionResponse Successful Response
     * @throws ApiError
     */
    public static getVersion(): CancelablePromise<VersionResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/system/version',
        });
    }
}
