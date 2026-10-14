/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The framework's plain route-not-found body.
 *
 * Config-gated routes answer this exact body when their gate is closed, so the
 * gate state is indistinguishable from a route that does not exist.
 */
export type RouteNotFoundResponse = {
    /**
     * Always `Not Found`.
     */
    detail: string;
};

