/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Import source pointing at a fetchable URL.
 */
export type ApiSourceUrl = {
    /**
     * Name to register the API under; taken from the document title when omitted.
     */
    api_name?: (string | null);
    /**
     * Who the resulting revision is attributed to; defaults to the caller.
     */
    submitted_by?: (string | null);
    /**
     * Source kind; always `url` for this variant.
     */
    type: string;
    /**
     * URL the OpenAPI or Arazzo document is fetched from.
     */
    url: string;
    /**
     * Vendor to register the API under; taken from the document when omitted.
     */
    vendor?: (string | null);
    /**
     * Version to register the API under; taken from the document when omitted.
     */
    version?: (string | null);
};

