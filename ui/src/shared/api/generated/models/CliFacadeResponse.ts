/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CliFacadeContainerWiringResponse } from './CliFacadeContainerWiringResponse';
import type { CliFacadeWrapperWiringResponse } from './CliFacadeWrapperWiringResponse';
/**
 * One vendor CLI facade an agent's CLI can be pointed at.
 */
export type CliFacadeResponse = {
    /**
     * Base URL to point the CLI at (e.g. `CLOUDFLARE_API_BASE_URL`). Null when the deployment cannot advertise one: a `remote` backend whose Broker URL is a loopback address and no `public_address` set for the vendor.
     */
    address?: (string | null);
    /**
     * The vendor CLI's executable, looked up on PATH.
     */
    binary?: (string | null);
    /**
     * The vendor CLI the facade speaks to, e.g. `cf` for `jentic cf`.
     */
    cli: string;
    /**
     * What `jentic run --container` sets for the vendor; null when nothing.
     */
    container?: (CliFacadeContainerWiringResponse | null);
    /**
     * How the vendor is named to people, e.g. in a wrapper's directives.
     */
    display_name?: (string | null);
    /**
     * Domains whose hosts the egress proxy of `jentic run --container` relays to the Broker for this vendor.
     */
    governed_domains?: Array<string>;
    /**
     * How the facade is addressed: `host` on a hostname of its own, `path` under a path prefix on the Broker's own address.
     */
    mode: CliFacadeResponse.mode;
    /**
     * What the vendor's tools reach through Jentic: `rest` (the vendor's REST API), `graphql` (GitHub's GraphQL endpoint, governed by GraphQL rules) and `git` (git clone, fetch and push to github.com, governed by git rules; listed only while the governed git pipeline is on).
     */
    protocols: Array<'rest' | 'graphql' | 'git'>;
    /**
     * CLI versions the vendor's CLI profile is written for, e.g. `>=2.40.0,<3`.
     */
    supported_range?: (string | null);
    /**
     * Vendor the facade serves, as passed to `POST /cli-invocations`.
     */
    vendor: string;
    /**
     * How `jentic <cli>` wires the vendor CLI (from its CLI profile); null when the CLI is not wrapped.
     */
    wrapper?: (CliFacadeWrapperWiringResponse | null);
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

