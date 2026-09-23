/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Slim list row for the console — deliberately excludes ``poll_token``.
 */
export type ConnectSessionSummaryResponse = {
    agent_id?: (string | null);
    connected_as?: (string | null);
    created_at: string;
    error_code?: (string | null);
    reason?: (string | null);
    requested_by_actor_id: string;
    session_id: string;
    state: ConnectSessionSummaryResponse.state;
    vendor_display_name: string;
    vendor_key: string;
};
export namespace ConnectSessionSummaryResponse {
    export enum state {
        CREATED = 'created',
        POLLING = 'polling',
        CONNECTED = 'connected',
    }
}

