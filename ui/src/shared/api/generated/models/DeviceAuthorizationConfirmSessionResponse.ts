/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * RFC 8628 device-code result — user types ``user_code`` at
 * ``verification_uri`` and the client polls ``/status`` until connected.
 *
 * Discriminator matches ``ConnectChallengeResponse`` (the direct
 * credential-connect endpoint) — one wire value for the same concept
 * across both entry points.
 */
export type DeviceAuthorizationConfirmSessionResponse = {
    kind?: string;
    /**
     * Seconds to wait between status polls.
     */
    poll_interval_seconds?: (number | null);
    /**
     * The code the user enters at `verification_uri`.
     */
    user_code: string;
    /**
     * Where the user enters `user_code`.
     */
    verification_uri: string;
    /**
     * `verification_uri` with the code filled in, when the vendor offers one.
     */
    verification_uri_complete?: (string | null);
};

