/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * An API a toolkit serves — the (vendor, name, version) identity tuple.
 *
 * Populated from the credentials bound to the toolkit, so an agent reading its
 * own `whoami` can tell which APIs it can already call — and skip filing a
 * provisioning plan for an API it is already bound to, instead of executing
 * just to discover it's denied.
 */
export type ApiRef = {
    api_name?: (string | null);
    api_vendor: string;
    api_version?: (string | null);
};

