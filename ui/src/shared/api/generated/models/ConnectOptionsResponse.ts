/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConnectSchemeOptionResponse } from './ConnectSchemeOptionResponse';
/**
 * How a connection to one registry API would be made, scheme by scheme.
 */
export type ConnectOptionsResponse = {
    /**
     * Whether `:connect` takes this API as a target on this instance.
     */
    api_target_supported: boolean;
    display_name?: (string | null);
    name: string;
    schemes: Array<ConnectSchemeOptionResponse>;
    vendor: string;
    version: string;
};

