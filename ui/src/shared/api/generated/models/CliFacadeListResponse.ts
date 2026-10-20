/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CliFacadeResponse } from './CliFacadeResponse';
/**
 * The vendor CLI facades this deployment serves.
 */
export type CliFacadeListResponse = {
    /**
     * Served facades, one per vendor; empty when none is enabled.
     */
    data: Array<CliFacadeResponse>;
};

