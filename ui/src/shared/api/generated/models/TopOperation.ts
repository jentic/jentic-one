/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Aggregated execution counts for a single operation.
 */
export type TopOperation = {
    /**
     * Name of the API the operation belongs to.
     */
    api_name: string;
    /**
     * Vendor of the API the operation belongs to.
     */
    api_vendor: string;
    /**
     * Number of those executions that failed.
     */
    failed: number;
    /**
     * Identifier of the operation.
     */
    operation_id: string;
    /**
     * Number of executions of the operation in the window.
     */
    total: number;
};

