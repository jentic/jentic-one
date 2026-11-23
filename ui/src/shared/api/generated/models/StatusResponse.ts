/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type StatusResponse = {
    bound_scopes?: (Array<string> | null);
    connected_as?: (string | null);
    credential_id?: (string | null);
    error_code?: (string | null);
    /**
     * An open edit whose binding moved since it was opened. Its ask is resolved against the binding as it is now by `POST /connect-sessions/{id}:rebase`, or by amending it.
     */
    stale?: boolean;
    status: StatusResponse.status;
};
export namespace StatusResponse {
    export enum status {
        PENDING = 'pending',
        POLLING = 'polling',
        CONNECTED = 'connected',
        APPLIED = 'applied',
        FAILED = 'failed',
        EXPIRED = 'expired',
    }
}

