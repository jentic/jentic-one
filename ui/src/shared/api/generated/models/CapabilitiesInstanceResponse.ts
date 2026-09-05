/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Identity slice of the document (the full probe stays ``GET /instance``).
 */
export type CapabilitiesInstanceResponse = {
    /**
     * Operator-declared backend locality (server.backend); a hint, not an authorization signal.
     */
    backend: CapabilitiesInstanceResponse.backend;
    /**
     * The instance's own canonical base URL (auth.canonical_base_url), with any userinfo stripped; '' if unset.
     */
    canonical_base_url: string;
};
export namespace CapabilitiesInstanceResponse {
    /**
     * Operator-declared backend locality (server.backend); a hint, not an authorization signal.
     */
    export enum backend {
        LOCAL = 'local',
        REMOTE = 'remote',
    }
}

