/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type IntegrationsConnectResponse = {
    approval_url: string;
    /**
     * Capability to poll the session's status; treat it as a secret.
     */
    poll_token: string;
    resolved_flow: string;
    /**
     * The session's first revision; a confirm names it until the session is amended.
     */
    revision_id?: (string | null);
    session_id: string;
};

