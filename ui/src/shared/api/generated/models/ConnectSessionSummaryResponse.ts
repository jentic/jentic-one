/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Slim list row for the console — deliberately excludes ``poll_token``.
 */
export type ConnectSessionSummaryResponse = {
    /**
     * ID of the agent the credential is bound to on confirm; null when unbound.
     */
    agent_id?: (string | null);
    /**
     * Upstream account the credential connected as; null until known.
     */
    connected_as?: (string | null);
    /**
     * When the session was created (RFC 3339).
     */
    created_at: string;
    /**
     * ID of the session's credential: pending until a create session connects; an edit session's is the live credential it changes.
     */
    credential_id: string;
    /**
     * Reserved for a failure code; always null on listed sessions.
     */
    error_code?: (string | null);
    /**
     * `create` connects a new credential; `edit` changes an existing binding.
     */
    intent?: ConnectSessionSummaryResponse.intent;
    /**
     * Free-text reason given when the session was started.
     */
    reason?: (string | null);
    /**
     * ID of the actor that started the session.
     */
    requested_by_actor_id: string;
    /**
     * Unique ID of the connect session.
     */
    session_id: string;
    /**
     * Current lifecycle state of the session.
     */
    state: ConnectSessionSummaryResponse.state;
    /**
     * Display name of the vendor.
     */
    vendor_display_name: string;
    /**
     * Vendor registry key the session connects.
     */
    vendor_key: string;
};
export namespace ConnectSessionSummaryResponse {
    /**
     * `create` connects a new credential; `edit` changes an existing binding.
     */
    export enum intent {
        CREATE = 'create',
        EDIT = 'edit',
    }
    /**
     * Current lifecycle state of the session.
     */
    export enum state {
        CREATED = 'created',
        AWAITING_APP = 'awaiting_app',
        POLLING = 'polling',
        CONNECTED = 'connected',
    }
}

