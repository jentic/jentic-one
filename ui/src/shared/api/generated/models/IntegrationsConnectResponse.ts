/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A started connect session: where a human approves it and how to poll it.
 */
export type IntegrationsConnectResponse = {
    /**
     * URL a human opens to review and approve the session.
     */
    approval_url: string;
    /**
     * Opaque capability for the session's status, review and cancel routes.
     */
    poll_token: string;
    /**
     * The flow the session runs, e.g. `authorization_code` or `manual_api_key`.
     */
    resolved_flow: string;
    /**
     * Unique ID of the connect session.
     */
    session_id: string;
};

