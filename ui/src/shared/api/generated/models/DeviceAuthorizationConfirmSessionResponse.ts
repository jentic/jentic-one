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
    poll_interval_seconds?: (number | null);
    user_code: string;
    verification_uri: string;
    verification_uri_complete?: (string | null);
};

