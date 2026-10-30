/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Import source carrying raw OpenAPI/Arazzo content inline.
 */
export type ApiSourceInline = {
    /**
     * Name to register the API under; taken from the document title when omitted.
     */
    api_name?: (string | null);
    /**
     * Raw OpenAPI or Arazzo document as a JSON or YAML string. At most `ingest.max_spec_bytes` UTF-8 bytes (25 MiB by default); larger content is refused with 413.
     */
    content: string;
    /**
     * File name of the submitted document.
     */
    filename: string;
    /**
     * Who the resulting revision is attributed to. Honoured only for an organisation admin (`org:admin`); for anyone else the revision is attributed to the caller.
     */
    submitted_by?: (string | null);
    /**
     * Source kind; always `inline` for this variant.
     */
    type: string;
    /**
     * Vendor to register the API under; taken from the document when omitted.
     */
    vendor?: (string | null);
    /**
     * Version to register the API under; taken from the document when omitted.
     */
    version?: (string | null);
};

