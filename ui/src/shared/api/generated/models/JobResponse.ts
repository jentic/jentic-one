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
     * Kind of actor that enqueued the job: 'user' or 'agent'. Opaque string: historical rows may carry a retired type such as 'service_account'.
     */
    actor_type: string;
    /**
     * When the job was created (RFC 3339).
     */
    created_at: string;
    /**
     * Error message recorded for the job; null if none.
     */
    error?: (string | null);
    /**
     * ID of the execution record linked to the job, if any.
     */
    execution_id?: (string | null);
    /**
     * Unique ID of the job.
     */
    job_id: string;
    /**
     * Kind of job, e.g. `import` or `execution`.
     */
    kind: string;
    /**
     * Lifecycle state: `queued`, `running`, `completed`, `failed`, `cancelled` or `dead_letter`.
     */
    status: string;
    /**
     * When the job was last updated (RFC 3339); null if never updated.
     */
    updated_at?: (string | null);
};

