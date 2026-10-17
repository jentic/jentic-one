/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Acknowledgement of a catalog refresh.
 */
export type CatalogRefreshResponse = {
    /**
     * Live entries of the enabled sources after the sync.
     */
    count: number;
    /**
     * Refresh outcome marker.
     */
    status?: string;
};

