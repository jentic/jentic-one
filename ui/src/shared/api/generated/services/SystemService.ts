/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { HealthResponse } from '../models/HealthResponse';
import type { InstanceIdentityResponse } from '../models/InstanceIdentityResponse';
import type { RootHealthResponse } from '../models/RootHealthResponse';
import type { VersionResponse } from '../models/VersionResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class SystemService {
    /**
     * Admin health and first-run status
     * Report admin-surface health, and whether the deployment still needs setup.
     *
     * Unlike the dependency-free root `/health` liveness probe, this one reaches the
     * database to answer `setup_required`: true while no user exists, which is what
     * the SPA and CLI read before offering a login. A database that cannot be
     * reached still answers 200 with `setup_required: false`, so treat this as a
     * readiness hint rather than a database health check.
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
     * Liveness probe
     * Liveness probe for the combined control-plane app.
     *
     * Unauthenticated and dependency-free so orchestrators and load balancers
     * have a stable target. Returns ``{"status": "ok"}`` when the process is up.
     * @returns RootHealthResponse Successful Response
     * @throws ApiError
     */
    public static getHealth(): CancelablePromise<RootHealthResponse> {
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
     * Running and latest-available app version
     * Return the running version and the latest available release.
     *
     * Requires an authenticated session (any valid caller; no special
     * permission). The SPA reads it from inside the signed-in shell to show the
     * current version and, when a newer release is available, an update banner.
     * The latest release is resolved best-effort (cached); on any failure it is
     * ``null`` and no banner shows.
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
