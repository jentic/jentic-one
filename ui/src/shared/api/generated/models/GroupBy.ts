/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Grouping dimension for usage statistics.
 *
 * ``TOOLKIT`` is deprecated (theme-5 Phase 5b) and will be removed one
 * release later, with the toolkit tables (Phase 6b): execution records
 * carry a ``credential_id`` since Phase 2 and the direct-binding path
 * writes no ``toolkit_id``, so ``CREDENTIAL`` is the replacement axis.
 */
export enum GroupBy {
    API = 'api',
    TOOLKIT = 'toolkit',
    CREDENTIAL = 'credential',
    AGENT = 'agent',
}
