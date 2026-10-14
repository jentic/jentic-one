/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiSourceInline } from './ApiSourceInline';
import type { ApiSourceUrl } from './ApiSourceUrl';
/**
 * Wrapper for a batch of import sources.
 */
export type ApiImportRequest = {
    /**
     * Documents to import, between 1 and 100 per request.
     */
    sources: Array<(ApiSourceUrl | ApiSourceInline)>;
};

