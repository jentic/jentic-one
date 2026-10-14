/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiReferenceResponse } from './ApiReferenceResponse';
import type { OperationSummaryLinksResponse } from './OperationSummaryLinksResponse';
/**
 * Single operation in a paginated list.
 */
export type OperationSummaryResponse = {
    _links: OperationSummaryLinksResponse;
    api: ApiReferenceResponse;
    /**
     * Whether the spec marks the operation as deprecated.
     */
    deprecated?: boolean;
    /**
     * Description of the operation; null if the spec gives none.
     */
    description?: (string | null);
    /**
     * HTTP method of the operation.
     */
    method: string;
    /**
     * Summary of the operation from the spec; null if it has none.
     */
    name?: (string | null);
    /**
     * Registry ID of the operation.
     */
    operation_id: string;
    /**
     * Path template of the operation as declared in the spec.
     */
    path: string;
    /**
     * ID of the revision the operation belongs to.
     */
    revision_id: string;
    /**
     * Tags the spec assigns to the operation.
     */
    tags?: Array<string>;
};

