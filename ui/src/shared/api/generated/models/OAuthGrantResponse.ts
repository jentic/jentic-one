/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One consent→agent grant in API responses.
 */
export type OAuthGrantResponse = {
    /**
     * The agent this grant binds the client to.
     */
    agent_id: string;
    /**
     * Display name of the registered client, if the row still exists.
     */
    client_name: (string | null);
    /**
     * Origin (scheme://host) of the client's first redirect URI — the 'authorized apps' display pattern.
     */
    client_origin: (string | null);
    created_at: string;
    /**
     * Grant ID (ksuid, `ocg_` prefix).
     */
    id: string;
    /**
     * Last time the client obtained tokens under this grant (stamped at exchange/refresh, not per request).
     */
    last_used_at: (string | null);
    /**
     * The client's public client_id — the same identifier stamped on tokens minted under this grant.
     */
    oauth_client_id: string;
    revoked_at: (string | null);
    /**
     * Scopes granted at consent (the D2 intersection).
     */
    scopes: Array<string>;
    /**
     * Grant lifecycle state: ``active`` or ``revoked``.
     */
    status: string;
    /**
     * The consenting user who approved this grant. Shown even after an agent ownership transfer: the grant stays with the original consenter (it is their consent, not the agent's).
     */
    user_id: string;
};

