/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { GovernedHostResponse } from './GovernedHostResponse';
/**
 * The caller's governed host set, sorted by host, with its change digest.
 *
 * Deliberately **unpaginated**: the set is bounded by the caller's own toolkit
 * bindings (tens of hosts, not thousands) and the digest must cover the whole
 * set atomically — a paginated digest would be meaningless. This is a
 * documented deviation from the list-endpoint pagination convention.
 */
export type GovernedHostsResponse = {
    data: Array<GovernedHostResponse>;
    digest: string;
};

