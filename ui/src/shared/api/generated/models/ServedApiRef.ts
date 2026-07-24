/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * An API served by a toolkit's bound credential, keyed by its stored identity.
 *
 * Distinct from ``APIReference`` on purpose: this carries the *stored* credential
 * identity, where ``api_name``/``api_version`` may be NULL (the "covers all
 * names/versions" wildcard, #775) — so they're optional here, unlike the strict
 * all-required ``APIReference``. Shared so the auth service schema and the
 * ``/me`` web schema use ONE model instead of two identical copies.
 */
export type ServedApiRef = {
    api_name?: (string | null);
    api_vendor: string;
    api_version?: (string | null);
};

