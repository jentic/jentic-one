/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A single declared parameter (query / header / path).
 */
export type OperationParameter = {
    /**
     * The parameter's description from the spec.
     */
    description?: (string | null);
    /**
     * The parameter's name.
     */
    name: string;
    /**
     * Whether the operation requires it.
     */
    required?: boolean;
    /**
     * The parameter's JSON Schema, as the spec declares it.
     */
    schema?: (Record<string, any> | null);
};

