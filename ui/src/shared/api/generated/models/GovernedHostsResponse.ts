/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The caller's governed host set (canonical order) with its change digest.
 *
 * **Hosts only, deliberately**: the set exists for interception scoping
 * (divert lists, host filters), so it carries the minimum knowledge — API
 * detail stays behind the existing authenticated reads (``GET /apis``).
 *
 * Deliberately **unpaginated**: the set is bounded by the caller's own toolkit
 * bindings (tens of hosts, not thousands) and the digest must cover the whole
 * set atomically — a paginated digest would be meaningless. This is a
 * documented deviation from the list-endpoint pagination convention.
 */
export type GovernedHostsResponse = {
    /**
     * Governed host patterns, lowercased, deduplicated, and sorted — the URL-index hosts the broker's discovery matches for the caller's toolkit-bound APIs. An entry may contain a `{var}` placeholder label (a defaultless server variable), which the broker matches as a single-label wildcard.
     */
    data: Array<string>;
    /**
     * SHA-256 hex digest over the newline-joined `data` list; also emitted as the response's strong `ETag` for If-None-Match change-polling.
     */
    digest: string;
};

