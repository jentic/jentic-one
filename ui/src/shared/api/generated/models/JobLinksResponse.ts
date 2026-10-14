/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Hypermedia links for a job.
 */
export type JobLinksResponse = {
    /**
     * URL of the related execution record; null if none.
     */
    execution?: (string | null);
    /**
     * URL of the job result; null until the job has completed.
     */
    result?: (string | null);
    /**
     * URL of this job.
     */
    self: string;
};

