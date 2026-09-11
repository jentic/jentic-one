/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Grouping dimension for usage statistics.
 *
 * ``TOOLKIT`` is a legacy axis: it groups over the surviving
 * ``execution_records.toolkit_id`` attribution column, which nothing writes
 * since the toolkit path was deleted (theme-5 Phase 6b). It stays so
 * historical dashboards keep working; ``CREDENTIAL`` is the live
 * consumer axis (execution records carry ``credential_id`` since Phase 2).
 */
export enum GroupBy {
    API = 'api',
    TOOLKIT = 'toolkit',
    CREDENTIAL = 'credential',
    AGENT = 'agent',
}
