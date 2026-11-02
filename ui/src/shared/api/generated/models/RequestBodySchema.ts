/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The request body an operation accepts, projected to a single schema.
 *
 * ``content_type`` records which media type the ``schema`` was taken from
 * (JSON is preferred when the spec offers several).
 */
export type RequestBodySchema = {
    /**
     * Media type the `schema` was taken from.
     */
    content_type?: (string | null);
    /**
     * The body's description from the spec.
     */
    description?: (string | null);
    /**
     * Whether the operation requires a body.
     */
    required?: boolean;
    /**
     * The body's JSON Schema, as the spec declares it.
     */
    schema?: (Record<string, any> | null);
};

