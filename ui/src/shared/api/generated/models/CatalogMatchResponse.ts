/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Why a catalog entry matched a search, as reported by the ranking strategy.
 */
export type CatalogMatchResponse = {
    /**
     * Kinds of evidence behind the match, as short slugs (e.g. `alias`). An open set: new values may appear, so clients must accept values they don't recognise.
     */
    channels: Array<string>;
};

