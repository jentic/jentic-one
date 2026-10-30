/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type StatusResponse = {
    bound_scopes?: (Array<string> | null);
    connected_as?: (string | null);
    credential_id?: (string | null);
    error_code?: (string | null);
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

