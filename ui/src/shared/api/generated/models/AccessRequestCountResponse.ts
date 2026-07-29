/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Count of access requests visible to the caller.
 */
export type AccessRequestCountResponse = {
    /**
     * Per-status breakdown (stored statuses only; the derived 'expired' presentation status counts under 'pending'). Only present when group_by=status was requested; statuses with no rows are omitted.
     */
    by_status?: (Record<string, number> | null);
    /**
     * Number of matching requests, after visibility filtering.
     */
    count: number;
};

