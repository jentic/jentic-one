/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { VendorSummaryResponse } from './VendorSummaryResponse';
/**
 * The vendors the caller can connect a credential for.
 */
export type VendorListResponse = {
    /**
     * The connectable vendors.
     */
    data: Array<VendorSummaryResponse>;
};

