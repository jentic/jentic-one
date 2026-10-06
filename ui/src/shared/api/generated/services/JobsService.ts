/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { JobListResponse } from '../models/JobListResponse';
import type { JobResponse } from '../models/JobResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class JobsService {
    /**
     * List Jobs
     * List jobs with optional filters.
     * @returns JobListResponse Successful Response
     * @throws ApiError
     */
    public static listJobs({
        kind,
        status,
        from,
        to,
        cursor,
        limit = 25,
    }: {
        kind?: (string | null),
        status?: (Array<string> | null),
        from?: (string | null),
        to?: (string | null),
        cursor?: (string | null),
        limit?: number,
    }): CancelablePromise<JobListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/jobs',
            query: {
                'kind': kind,
                'status': status,
                'from': from,
                'to': to,
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
     * Get Job
     * Get a job by ID.
     * @returns JobResponse Successful Response
     * @throws ApiError
     */
    public static getJob({
        jobId,
    }: {
        jobId: string,
    }): CancelablePromise<JobResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/jobs/{job_id}',
            path: {
                'job_id': jobId,
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
     * Get Job Result
     * Get the result of a completed job, or the problem body of a failed execution.
     *
     * Polymorphic by kind. A held execution that was denied or expired is
     * ``failed`` with a permission-denied problem as its result.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getJobResult({
        jobId,
    }: {
        jobId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/jobs/{job_id}/result',
            path: {
                'job_id': jobId,
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
     * Cancel Job
     * Cancel a queued or running job; an already-terminal job is returned unchanged.
     *
     * A ``held`` execution answers ``409``: it settles only through its approval
     * (a reviewer's decision, the filing agent's withdrawal, or expiry).
     * @returns JobResponse Successful Response
     * @throws ApiError
     */
    public static cancelJob({
        jobId,
    }: {
        jobId: string,
    }): CancelablePromise<JobResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/jobs/{job_id}:cancel',
            path: {
                'job_id': jobId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                404: `Not Found`,
                409: `Conflict`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
}
