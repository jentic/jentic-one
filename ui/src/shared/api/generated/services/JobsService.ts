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
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Get Job Result
     * Get the result of a completed job — polymorphic by kind.
     * @returns any The completed job's result, polymorphic by the job's `kind` (read `kind` from `GET /jobs/{job_id}`). The body is always a JSON object — never an array or a scalar — but its members depend on the kind:
     *
     * - **`kind=import`**: `revisions`, one entry per API revision the import produced, each with `api` (the API object), `revision_id` and `state`.
     * - **`kind=execution`**: the execution document — `execution_id`, `status` (`completed` or `failed`), `http_status` (the upstream status, or null when the call never reached the upstream), `duration_ms`, and `body_b64` when the upstream returned a body. The upstream payload is base64 in `body_b64`; it is never inlined as JSON, so it needs decoding and interpreting against the upstream's own content type. A run-time authorization denial adds `problem`, the RFC 9457 body the synchronous execute route would have returned.
     * - **A held execution that never ran** (denied, its approval window lapsed, or its approved run interrupted) carries an `application/problem+json` body in place of the execution document, with `type` `approval_denied`, `approval_expired`, `approval_required` or `approval_resume_failed`, plus `error_origin` and a `held_execution` object (`id`, `state`). The HTTP status stays `200`; the problem's own `status` member (403, or 409 for an interrupted approved run) says why the call never happened.
     *
     * `Content-Type` caveat: for a `kind=execution` result the header echoes the **upstream** response's content type, while the body is still the JSON execution document above — parse the body as JSON whatever the header says.
     * @throws ApiError
     */
    public static getJobResult({
        jobId,
    }: {
        /**
         * ID of the job.
         */
        jobId: string,
    }): CancelablePromise<Record<string, any>> {
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
