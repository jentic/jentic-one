/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { VendorFlowResponse } from './VendorFlowResponse';
import type { VendorScopeResponse } from './VendorScopeResponse';
export type VendorAuthCapabilitiesResponse = {
    /**
     * Human-facing vendor name, for a connect screen.
     */
    display_name: string;
    flows: Array<VendorFlowResponse>;
    scopes: Array<VendorScopeResponse>;
    /**
     * Vendor registry key, as passed in the path.
     */
    vendor: string;
};

