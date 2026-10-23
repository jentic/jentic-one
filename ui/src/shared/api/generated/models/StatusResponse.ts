/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A connect session's lifecycle status.
 */
export type StatusResponse = {
    /**
     * Scopes the connected credential holds.
     */
    bound_scopes?: (Array<string> | null);
    /**
     * Upstream account the credential connected as; null until known.
     */
    connected_as?: (string | null);
    /**
     * ID of the connected credential; null until connected.
     */
    credential_id?: (string | null);
    /**
     * Why a failed session failed, e.g. `rejected` or `access_denied`.
     */
    error_code?: (string | null);
    /**
     * The session's lifecycle status.
     */
    status: StatusResponse.status;
};
export namespace StatusResponse {
    /**
     * The session's lifecycle status.
     */
    export enum status {
        PENDING = 'pending',
        POLLING = 'polling',
        CONNECTED = 'connected',
        FAILED = 'failed',
        EXPIRED = 'expired',
    }
}

