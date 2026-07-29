/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Self-describing identity of the backend serving this request.
 *
 * A client can compare ``backend``/``canonical_base_url``/``host`` against where
 * it *thinks* it is pointed to confirm it is talking to the intended backend
 * (e.g. a local install vs. a remote one) before diagnosing "missing" data.
 */
export type InstanceIdentityResponse = {
    /**
     * Operator-declared backend locality (server.backend): 'local' for a self-hosted install on the operator's own machine/network, 'remote' for a hosted install run elsewhere. A hint, not an authorization signal; defaults to 'local'.
     */
    backend: InstanceIdentityResponse.backend;
    /**
     * The instance's own canonical base URL (auth.canonical_base_url), with any userinfo stripped; '' if unset.
     */
    canonical_base_url: string;
    /**
     * Host (and port, when the canonical base URL declares one) parsed from canonical_base_url; '' if unset or unparseable.
     */
    host: string;
    /**
     * Opaque digest derived from the telemetry instance id — stable per install, but not the telemetry id itself. Null when telemetry has not resolved an id (e.g. telemetry disabled).
     */
    instance_id?: (string | null);
};
export namespace InstanceIdentityResponse {
    /**
     * Operator-declared backend locality (server.backend): 'local' for a self-hosted install on the operator's own machine/network, 'remote' for a hosted install run elsewhere. A hint, not an authorization signal; defaults to 'local'.
     */
    export enum backend {
        LOCAL = 'local',
        REMOTE = 'remote',
    }
}

