/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { UsageBucket } from './UsageBucket';
import type { UsageStatsBlock } from './UsageStatsBlock';
import type { UsageTopRow } from './UsageTopRow';
/**
 * Full usage statistics response.
 */
export type UsageResponse = {
    /**
     * Width of each entry in `buckets`, in seconds; chosen from the window length.
     */
    bucket_seconds: number;
    /**
     * Execution counts per time bucket, oldest first; empty buckets are omitted.
     */
    buckets: Array<UsageBucket>;
    /**
     * Dimension the `top` rows are grouped by.
     */
    group_by: string;
    /**
     * Start of the window, in Unix epoch seconds (inclusive).
     */
    since: number;
    stats: UsageStatsBlock;
    /**
     * The entities with the most executions in the window, most first.
     */
    top: Array<UsageTopRow>;
    /**
     * End of the window, in Unix epoch seconds (exclusive).
     */
    until: number;
};

