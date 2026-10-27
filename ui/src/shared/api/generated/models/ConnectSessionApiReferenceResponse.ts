/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Where the vendor's OpenAPI lives in the registry.
 *
 * ``version`` is nullable because the catalog auto-importer runs
 * asynchronously — the SPA's rules-page preview polls this endpoint
 * and shows an "operations still importing…" skeleton until it
 * becomes non-null and the operations endpoint returns 200.
 */
export type ConnectSessionApiReferenceResponse = {
    /**
     * Registry name of the API; null if not yet known.
     */
    name: (string | null);
    /**
     * Registry vendor of the API.
     */
    vendor: string;
    /**
     * Registry version of the API; null until the catalog import completes.
     */
    version: (string | null);
};

