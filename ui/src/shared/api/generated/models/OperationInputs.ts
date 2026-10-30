/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OperationParameter } from './OperationParameter';
import type { RequestBodySchema } from './RequestBodySchema';
/**
 * Declared inputs for an operation, grouped by where they belong.
 *
 * Declares the query / header / path parameters and request body so a client
 * can construct a complete request rather than only supplying path parameters
 * (issue #768).
 */
export type OperationInputs = {
    body?: (RequestBodySchema | null);
    header?: Array<OperationParameter>;
    path?: Array<OperationParameter>;
    query?: Array<OperationParameter>;
};

