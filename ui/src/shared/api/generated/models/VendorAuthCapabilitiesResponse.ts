/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { VendorFlowResponse } from './VendorFlowResponse';
import type { VendorScopeResponse } from './VendorScopeResponse';
/**
 * How to connect a vendor: its OAuth flows and scope catalogue.
 */
export type VendorAuthCapabilitiesResponse = {
    /**
     * Display name of the vendor.
     */
    display_name: string;
    /**
     * OAuth flows the vendor supports.
     */
    flows: Array<VendorFlowResponse>;
    /**
     * Every scope the vendor offers.
     */
    scopes: Array<VendorScopeResponse>;
    /**
     * Vendor registry key.
     */
    vendor: string;
};

