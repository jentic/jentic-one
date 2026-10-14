/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiImportLinksResponse } from './ApiImportLinksResponse';
/**
 * Acknowledgement payload for an asynchronous import job.
 */
export type ApiImportResponse = {
    _links: ApiImportLinksResponse;
    /**
     * ID of the import job.
     */
    job_id: string;
    /**
     * Status of the job when accepted; `queued`.
     */
    status: string;
};

