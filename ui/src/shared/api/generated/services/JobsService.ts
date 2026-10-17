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
        /**
         * Only return jobs of this kind, e.g. `import` or `execution`.
         */
        kind?: (string | null),
        /**
         * Only return jobs in one of these states; repeat the parameter for several.
         */
        status?: (Array<string> | null),
        /**
         * Only return jobs created at or after this time (RFC 3339).
         */
        from?: (string | null),
        /**
         * Only return jobs created before this time (RFC 3339).
         */
        to?: (string | null),
        /**
         * Opaque cursor from a previous page's `next_cursor`; omit for the first page.
         */
        cursor?: (string | null),
        /**
         * Maximum number of jobs to return (1-100, default 25).
         */
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
        /**
         * ID of the job.
         */
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
                404: `Not Found`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Get Job Result
     * Get the result of a completed job — polymorphic by kind.
     *
     * Answers ``409`` unless the job's ``status`` is ``completed`` (pending,
     * running, failed or cancelled), and ``410`` once an
     * approved execution's result is past
     * ``held_executions.result_retention_seconds``.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getJobResult({
        jobId,
    }: {
        /**
         * ID of the job.
         */
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
                404: `Not Found`,
                409: `Conflict`,
                410: `Gone`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Cancel Job
     * Cancel an active job.
     *
     * A held execution answers ``409``: it settles only through its hold (decide,
     * withdraw or expiry), never by cancelling the job.
     * @returns JobResponse Successful Response
     * @throws ApiError
     */
    public static cancelJob({
        jobId,
    }: {
        /**
         * ID of the job.
         */
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
