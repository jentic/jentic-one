/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CliProfileProvisionedApiResponse } from './CliProfileProvisionedApiResponse';
/**
 * The outcome of provisioning a CLI profile.
 */
export type CliProfileProvisionResponse = {
    /**
     * One entry per declared API.
     */
    apis: Array<CliProfileProvisionedApiResponse>;
    /**
     * Profile name.
     */
    profile: string;
    /**
     * CLI facade vendor.
     */
    vendor: string;
    /**
     * Whether the vendor's facade is switched on. Provisioning runs either way; agents reach the APIs through the vendor's CLI only while it is on.
     */
    vendor_enabled: boolean;
};

