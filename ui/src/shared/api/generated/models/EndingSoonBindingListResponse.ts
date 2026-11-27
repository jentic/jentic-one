/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EndingSoonBindingResponse } from './EndingSoonBindingResponse';
/**
 * Bindings that will soon reach their idle limit.
 */
export type EndingSoonBindingListResponse = {
    /**
     * Bindings with the warning on that end soon, soonest first.
     */
    data: Array<EndingSoonBindingResponse>;
};

