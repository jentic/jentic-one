/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One vendor CLI facade an agent's CLI can be pointed at.
 */
export type CliFacadeResponse = {
    /**
     * Base URL to point the CLI at (e.g. `CLOUDFLARE_API_BASE_URL`). Null when the deployment cannot advertise one: a `remote` backend whose Broker URL is a loopback address and no `public_address` set for the vendor.
     */
    address?: (string | null);
    /**
     * The vendor CLI the facade speaks to, e.g. `cf` for `jentic cf`.
     */
    cli: string;
    /**
     * How the facade is addressed: `host` on a hostname of its own, `path` under a path prefix on the Broker's own address.
     */
    mode: CliFacadeResponse.mode;
    /**
     * Vendor the facade serves, as passed to `POST /cli-invocations`.
     */
    vendor: string;
};
export namespace CliFacadeResponse {
    /**
     * How the facade is addressed: `host` on a hostname of its own, `path` under a path prefix on the Broker's own address.
     */
    export enum mode {
        HOST = 'host',
        PATH = 'path',
    }
}

