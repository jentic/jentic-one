/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type VendorSummaryResponse = {
    catalog_api_id?: (string | null);
    display_name: string;
    entry_id: string;
    flow_kinds: Array<string>;
    key: string;
    name: string;
    registration_id?: (string | null);
    source: VendorSummaryResponse.source;
    vendor: string;
};
export namespace VendorSummaryResponse {
    export enum source {
        DB = 'db',
        CONFIG = 'config',
    }
}

