/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CatalogSourceResponse } from './CatalogSourceResponse';
/**
 * The configured catalog sources the caller can see.
 */
export type CatalogSourceListResponse = {
    /**
     * Sources, ordered by slug.
     */
    data: Array<CatalogSourceResponse>;
};

