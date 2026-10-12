/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { JobLinksResponse } from './JobLinksResponse';
/**
 * Job representation in API responses.
 */
export type JobResponse = {
    _links: JobLinksResponse;
    /**
     * Subject that enqueued the job. Null only for legacy rows with no actor.
     */
    actor_id?: (string | null);
    /**
     * Kind of actor that enqueued the job (user or agent).
     */
    actor_type: string;
    created_at: string;
    error?: (string | null);
    execution_id?: (string | null);
    job_id: string;
    kind: string;
    status: string;
    updated_at?: (string | null);
};

