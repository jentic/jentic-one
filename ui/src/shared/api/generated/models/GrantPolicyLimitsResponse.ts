/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DurationLimitResponse } from './DurationLimitResponse';
/**
 * The org's defaults and maximums for new grants.
 */
export type GrantPolicyLimitsResponse = {
    /**
     * A grant's end date, as a lifetime from when it is granted or renewed.
     */
    expires_in: DurationLimitResponse;
    /**
     * A grant's idle limit.
     */
    idle_period: DurationLimitResponse;
    /**
     * Whether a grant created with no policy allows a single call.
     */
    single_use_by_default: boolean;
};

