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
     * Governed host entries, normalised (lowercased, FQDN root dot stripped, internationalised names IDNA/punycode-encoded), deduplicated, and sorted — the literal URL-index hosts the broker's discovery matches for the caller's toolkit-bound APIs. An entry is a hostname, a bare IP literal, or either followed by a non-default port (`host[:port]` — default ports are already stripped). Compare case-insensitively (lowercase the incoming host before matching); a gate keying on hostname or SNI alone must strip any `:port` suffix from the entry first. Variable-bearing hosts (defaultless `{var}` server variables) are excluded — the broker's discovery never matches them. On a `5xx` retain the last known set; never fall back to an empty (intercept-nothing) list.
     */
    data: Array<string>;
    /**
     * SHA-256 hex digest over the newline-joined `data` list; also emitted as the response's strong `ETag`. To change-poll, send it back quoted — `If-None-Match: "<digest>"` (the bare digest is accepted as a compatibility form) — and expect an empty `304` until the host set changes. Poll at most once per minute.
     */
    digest: string;
};

