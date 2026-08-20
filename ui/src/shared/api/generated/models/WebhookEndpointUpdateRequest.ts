/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Partial update for a webhook endpoint (PATCH semantics).
 *
 * Every field is optional: only the fields actually present in the request are
 * applied, and an omitted field is left unchanged. Deliberately carries **no**
 * secret field — editing configuration must never touch signing authority,
 * which is the separate rotation flow. An explicit empty ``event_types`` list is
 * meaningful (subscribe to every relayable type), which is why the caller must
 * distinguish "omitted" from "empty" via the request's set fields.
 */
export type WebhookEndpointUpdateRequest = {
    /**
     * Whether the endpoint receives deliveries. Set false to pause it.
     */
    active?: (boolean | null);
    /**
     * Event types to subscribe to. Empty means all relayable types.
     */
    event_types?: (Array<string> | null);
    name?: (string | null);
    /**
     * The URL signed events are POSTed to. Must be an http(s) URL.
     */
    target_url?: (string | null);
};

