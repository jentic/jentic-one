/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * RFC 8628 device-code challenge.
 *
 * Client shows ``user_code`` at ``verification_uri`` and polls
 * ``GET /credentials/{id}`` until state moves off ``pending``
 * (scanner-driven server-side).
 */
export type DeviceAuthorizationConnectChallengeResponse = {
    kind?: string;
    /**
     * Minimum seconds to wait between polls, when the vendor supplies it; null means use your own default.
     */
    poll_interval_seconds?: (number | null);
    /**
     * Code the user types at `verification_uri`.
     */
    user_code: string;
    /**
     * Where the user enters `user_code`.
     */
    verification_uri: string;
    /**
     * `verification_uri` with `user_code` already embedded, when the vendor supplies it (RFC 8628 §3.3.1); null when it does not.
     */
    verification_uri_complete?: (string | null);
};

